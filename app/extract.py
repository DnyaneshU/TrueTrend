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

import pymupdf
from dateutil import parser as dateparser

SCAN_TEXT_THRESHOLD = 50          # fewer visible characters than this: the page is a scan
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
