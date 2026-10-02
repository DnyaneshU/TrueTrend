"""Extract lab test results from a lab report PDF with local Gemma.

    python -m app.extract path/to/report.pdf [--force] [--model gemma4:e2b]

One Gemma call per page. Digital pages are sent as text rebuilt line by line;
pages with almost no text (scans) are sent as an image. Gemma copies values
exactly as printed; code assigns page numbers, parses dates and saves the rows.
Every saved row is `needs_check` until verify.py exists.
"""
import re
import statistics
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Literal

import httpx
import ollama
import pymupdf
from dateutil import parser as dateparser
from pydantic import BaseModel

DEFAULT_MODEL = "gemma4:e4b"
OLLAMA_OPTIONS = {"temperature": 0, "num_ctx": 8192, "num_predict": 2048}
RETRY_TEMPERATURE = 0.3   # a second temperature-0 call would usually repeat the same broken reply
SCAN_TEXT_THRESHOLD = 50         # fewer visible characters than this: the page is a scan
IMAGE_PAGE_TEXT_THRESHOLD = 200   # ...or fewer than this while images cover IMAGE_PAGE_COVERAGE
IMAGE_PAGE_COVERAGE = 0.5         # of the page (a scan with a typed header or footer)
RENDER_DPI = 150
COLUMN_GAP = 0.6                  # a gap wider than this × text height separates table columns
WORD_FLAGS = pymupdf.TEXTFLAGS_WORDS & ~pymupdf.TEXT_PRESERVE_LIGATURES  # "ﬁ" comes out as "fi"


class ExtractError(Exception):
    """A problem the user can fix. main() prints it as one line and exits with 1."""


@dataclass(frozen=True)
class PageInput:
    number: int                      # 1-based, assigned by code
    total: int
    mode: Literal["text", "vision"]
    text: str = ""
    image: bytes | None = None       # PNG, vision pages only


def open_pdf(path: Path) -> pymupdf.Document:
    """Open a report PDF, or raise ExtractError saying in one sentence why not."""
    if not path.is_file():
        raise ExtractError(f"File not found: {path}")
    try:
        doc = pymupdf.open(path)
    except RuntimeError:  # pymupdf.FileDataError and friends
        raise ExtractError(f"Not a readable PDF: {path}") from None
    if not doc.is_pdf:
        problem = f"Not a PDF: {path}"
    elif doc.needs_pass:
        problem = (f"{path.name} is password-protected. Open it once, save a copy "
                   "without a password, and run this on the copy.")
    elif doc.page_count == 0:
        problem = f"{path.name} has no pages."
    else:
        return doc
    doc.close()
    raise ExtractError(problem)


def page_text(page: pymupdf.Page) -> str:
    """The page's text rebuilt one printed line at a time.

    Words whose vertical centres are within half a text height form one line,
    read left to right. A gap wider than COLUMN_GAP × text height becomes " | ",
    so a table row reads "HbA1c | 6.8 | % | 4.0 - 5.6".
    """
    words = [word[:5] for word in page.get_text("words", flags=WORD_FLAGS)]  # (x0, y0, x1, y1, text)
    if not words:
        return ""
    height = statistics.median(y1 - y0 for _, y0, _, y1, _ in words) or 1.0
    lines: list[list[tuple]] = []
    line_centre = 0.0
    for word in sorted(words, key=lambda w: ((w[1] + w[3]) / 2, w[0])):
        centre = (word[1] + word[3]) / 2
        if lines and centre - line_centre <= height / 2:
            lines[-1].append(word)
        else:
            lines.append([word])
            line_centre = centre
    return "\n".join(_join_line(sorted(line), height) for line in lines)


def _join_line(words: list[tuple], height: float) -> str:
    """One line's words, left to right, with " | " wherever the gap is a column break."""
    parts = [words[0][4]]
    for (_, _, previous_x1, _, _), (x0, _, _, _, text) in zip(words, words[1:]):
        parts.append(" | " if x0 - previous_x1 > COLUMN_GAP * height else " ")
        parts.append(text)
    return "".join(parts)


def read_pages(doc: pymupdf.Document) -> list[PageInput]:
    """One PageInput per page: its rebuilt text, or a PNG of the page if it looks scanned."""
    pages = []
    for number, page in enumerate(doc, start=1):
        text = page_text(page)
        if _looks_scanned(page, text):
            png = page.get_pixmap(dpi=RENDER_DPI).tobytes("png")
            pages.append(PageInput(number, doc.page_count, "vision", image=png))
        else:
            pages.append(PageInput(number, doc.page_count, "text", text=text))
    return pages


def _looks_scanned(page: pymupdf.Page, text: str) -> bool:
    """True when the page's content is in an image rather than in its text."""
    visible = len("".join(text.split()))
    if visible < SCAN_TEXT_THRESHOLD:
        return True
    return visible < IMAGE_PAGE_TEXT_THRESHOLD and _image_coverage(page) >= IMAGE_PAGE_COVERAGE


def _image_coverage(page: pymupdf.Page) -> float:
    """Share of the page covered by images, 0 to 1 (overlaps count twice, hence the cap)."""
    covered = sum(abs(pymupdf.Rect(info["bbox"]) & page.rect) for info in page.get_image_info())
    return min(covered / abs(page.rect), 1.0)


_ISO_DATE = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
_DEFAULT_A, _DEFAULT_B = datetime(2000, 1, 1), datetime(2001, 2, 2)


def parse_date(text: str | None) -> str | None:
    """A printed date as ISO YYYY-MM-DD, read day-first (Indian DD/MM/YYYY).

    Returns None for missing, partial ("Sep 2026") or unreadable text. Parsing
    twice with different defaults catches parts dateutil would silently fill in.
    """
    if not text or not text.strip():
        return None
    iso = _ISO_DATE.search(text)
    if iso:
        try:
            return date(*map(int, iso.groups())).isoformat()
        except ValueError:
            return None
    try:
        first = dateparser.parse(text, dayfirst=True, fuzzy=True, default=_DEFAULT_A)
        second = dateparser.parse(text, dayfirst=True, fuzzy=True, default=_DEFAULT_B)
    except (ValueError, OverflowError):
        return None
    if first.date() != second.date():  # day, month or year was missing and came from the default
        return None
    return first.date().isoformat()


TestCode = Literal[
    "HBA1C", "GLU_F", "GLU_PP", "TSH", "FT4", "CHOL", "LDL", "HDL",
    "TG", "CREAT", "HB", "VITD", "B12", "URIC", "UREA",
]


class ExtractedResult(BaseModel):
    test_code: TestCode
    raw_name: str
    value_text: str
    unit: str | None
    ref_text: str | None


class PageExtraction(BaseModel):
    patient_name: str | None
    age: str | None
    sex: str | None
    lab_name: str | None
    sample_date: str | None
    report_date: str | None
    results: list[ExtractedResult]


PAGE_SCHEMA = PageExtraction.model_json_schema()  # Ollama constrains Gemma's reply to this

SYSTEM_PROMPT = """You read one page of an Indian medical laboratory report and copy data exactly as printed.
Never calculate, convert, round, translate or guess. If something is not printed on this page, use null.

Fields:
- patient_name, age, sex, lab_name: exactly as printed on this page, or null.
- sample_date: the sample COLLECTION date and time as printed (labels such as "Collected", "Sample Collected On", "Collection Date", "Drawn"). Not the registration date and not the report date.
- report_date: the date the report was released, as printed (labels such as "Reported", "Report Date", "Reported On").
- results: one entry for each result of ONLY these tests:
  HBA1C  = HbA1c, Glycated / Glycosylated Haemoglobin
  GLU_F  = Fasting blood or plasma glucose, FBS, Fasting Blood Sugar
  GLU_PP = Post-prandial glucose, PPBS, PP blood sugar, 2-hour glucose
  TSH    = TSH, Thyroid Stimulating Hormone (including ultrasensitive TSH)
  FT4    = Free T4, FT4, Free Thyroxine
  CHOL   = Total Cholesterol
  LDL    = LDL Cholesterol (direct or calculated)
  HDL    = HDL Cholesterol
  TG     = Triglycerides
  CREAT  = Serum Creatinine
  HB     = Haemoglobin / Hemoglobin / Hb
  VITD   = 25-Hydroxy (25-OH) Vitamin D, Vitamin D Total
  B12    = Vitamin B12, Cyanocobalamin
  URIC   = Uric Acid
  UREA   = Urea, Blood Urea, Serum Urea
  Do NOT include: Total T4 or T4, T3, LDL/HDL or other ratios, VLDL, non-HDL cholesterol, Estimated Average Glucose, Mean Blood Glucose, Random Blood Sugar, BUN / Blood Urea Nitrogen, any urine test, or any other test.
  Haemoglobin (HB) and HbA1c are different tests.
  For each result: raw_name = the test name exactly as printed; value_text = the result exactly as printed (keep "<", ">" and all decimals); unit = as printed, or null; ref_text = the reference range exactly as printed, or null.
  If none of these tests are on this page, results is [].
In the page text, each line is one printed line and " | " separates table columns.
Reply with compact JSON on a single line, no indentation."""


def ask_gemma(page: PageInput, model: str, retry: bool = False) -> PageExtraction:
    """One Gemma call for one page.

    Raises pydantic.ValidationError if the reply does not fit the schema, and
    ExtractError if Ollama is unreachable, the model is missing or the connection drops.
    """
    intro = f"Page {page.number} of {page.total}."
    if page.mode == "vision":
        user = {"role": "user", "content": f"{intro} The page is attached as an image.",
                "images": [page.image]}
    else:
        user = {"role": "user", "content": f"{intro} Page text:\n\n{page.text}"}
    options = dict(OLLAMA_OPTIONS, temperature=RETRY_TEMPERATURE) if retry else dict(OLLAMA_OPTIONS)
    try:
        response = ollama.chat(
            model=model,
            messages=[{"role": "system", "content": SYSTEM_PROMPT}, user],
            format=PAGE_SCHEMA,
            options=options,
            think=False,
        )
    except ConnectionError:
        raise ExtractError(
            "Can't reach Ollama. Start the Ollama app (or run: ollama serve) and try again."
        ) from None
    except ollama.ResponseError as error:
        if error.status_code == 404:
            raise ExtractError(f"Model {model} is not installed. Run: ollama pull {model}") from None
        raise ExtractError(f"Ollama error: {error.error}") from None
    except httpx.TransportError:
        raise ExtractError(
            "Lost the connection to Ollama while reading the report. Is it still running?"
        ) from None
    return PageExtraction.model_validate_json(response.message.content)
