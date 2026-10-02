"""Turn a report PDF into pages Gemma can read: rebuilt text, or an image for scans."""
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pymupdf

from app.errors import ExtractError

SCAN_TEXT_THRESHOLD = 50          # fewer visible characters than this: the page is a scan
IMAGE_PAGE_TEXT_THRESHOLD = 200   # ...or fewer than this while images cover IMAGE_PAGE_COVERAGE
IMAGE_PAGE_COVERAGE = 0.5         # of the page (a scan with a typed header or footer)
RENDER_DPI = 150
COLUMN_GAP = 0.6                  # a gap wider than this × text height separates table columns
WORD_FLAGS = pymupdf.TEXTFLAGS_WORDS & ~pymupdf.TEXT_PRESERVE_LIGATURES  # "ﬁ" comes out as "fi"


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
    for word in sorted(words, key=lambda w: (_centre(w), w[0])):
        if lines and _centre(word) - line_centre <= height / 2:
            lines[-1].append(word)
        else:
            lines.append([word])
            line_centre = _centre(word)
    return "\n".join(_join_line(sorted(line), height) for line in lines)


def _centre(word: tuple) -> float:
    _, y0, _, y1, _ = word
    return (y0 + y1) / 2


def _join_line(words: list[tuple], height: float) -> str:
    """One line's words, left to right, with " | " wherever the gap is a column break."""
    parts = [words[0][4]]
    for (_, _, previous_x1, _, _), (x0, _, _, _, text) in zip(words, words[1:]):
        parts.append(" | " if x0 - previous_x1 > COLUMN_GAP * height else " ")
        parts.append(text)
    return "".join(parts)


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
