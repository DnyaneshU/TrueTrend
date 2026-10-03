"""Turn a report PDF into pages Gemma can read: rebuilt text, or an image for scans."""

import statistics
from itertools import pairwise
from pathlib import Path
from typing import NamedTuple

import pymupdf

from arogya_vahi.config import settings
from arogya_vahi.errors import UserError
from arogya_vahi.models import PageInput

# Extract words with ligatures expanded, so "Proﬁle" comes out as "Profile".
WORD_FLAGS = pymupdf.TEXTFLAGS_WORDS & ~pymupdf.TEXT_PRESERVE_LIGATURES


class Word(NamedTuple):
    """A word printed on the page and its box, in PDF points from the top left."""

    x0: float
    y0: float
    x1: float
    y1: float
    text: str

    @property
    def centre(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        """The box to a tenth of a point, plenty for highlighting it."""
        return round(self.x0, 1), round(self.y0, 1), round(self.x1, 1), round(self.y1, 1)


def open_pdf(path: Path, data: bytes | None = None) -> pymupdf.Document:
    """Open a report PDF, or raise UserError saying in one sentence why not.

    With `data`, the PDF is opened from those bytes (already read from `path`).
    """
    if data is None and not path.is_file():
        raise UserError(f"File not found: {path}")
    try:
        doc = pymupdf.open(path) if data is None else pymupdf.open(stream=data)
    except RuntimeError:  # pymupdf.FileDataError and friends
        raise UserError(f"Not a readable PDF: {path}") from None
    if not doc.is_pdf:
        problem = f"Not a PDF: {path}"
    elif doc.needs_pass:
        problem = (
            f"{path.name} is password-protected. Open it once, save a copy "
            "without a password, and run this on the copy."
        )
    elif doc.page_count == 0:
        problem = f"{path.name} has no pages."
    else:
        return doc
    doc.close()
    raise UserError(problem)


def read_pages(doc: pymupdf.Document) -> list[PageInput]:
    """One PageInput per page: its rebuilt text, or a PNG of the page if it looks scanned."""
    pages = []
    for number, page in enumerate(doc, start=1):
        text = page_text(page)
        if looks_scanned(page, text):
            pages.append(PageInput(number=number, total=doc.page_count, mode="vision", image=_render(page)))
        else:
            pages.append(PageInput(number=number, total=doc.page_count, mode="text", text=text))
    return pages


def _render(page: pymupdf.Page) -> bytes:
    """PNG of the page at settings.render_dpi, scaled down so neither side exceeds settings.max_image_side."""
    zoom = min(settings.render_dpi / 72, settings.max_image_side / max(page.rect.width, page.rect.height))
    return page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom)).tobytes("png")


def page_text(page: pymupdf.Page) -> str:
    """The page's text rebuilt one printed line at a time.

    Cells are separated by " | ", so a table row reads "HbA1c | 6.8 | % | 4.0 - 5.6".
    """
    return rows_text(page_rows(page))


Cell = list[Word]
Row = list[Cell]


def rows_text(rows: list[Row]) -> str:
    """Rows as text: one line per row, cells separated by " | "."""
    return "\n".join(" | ".join(" ".join(word.text for word in cell) for cell in row) for row in rows)


def page_rows(page: pymupdf.Page) -> list[Row]:
    """The page's printed lines, top to bottom, each split into cells read left to right.

    Words whose vertical centres are within half a text height form one line, and a
    gap wider than settings.column_gap × text height starts a new cell.
    """
    words = [Word(*word[:5]) for word in page.get_text("words", flags=WORD_FLAGS)]
    if not words:
        return []
    height = statistics.median(word.y1 - word.y0 for word in words) or 1.0
    lines: list[list[Word]] = []
    line_centre = 0.0
    for word in sorted(words, key=lambda word: (word.centre, word.x0)):
        if lines and word.centre - line_centre <= height / 2:
            lines[-1].append(word)
        else:
            lines.append([word])
            line_centre = word.centre
    return [_cells(sorted(line), settings.column_gap * height) for line in lines]


def _cells(line: list[Word], column_gap: float) -> Row:
    cells = [[line[0]]]
    for previous, word in pairwise(line):
        if word.x0 - previous.x1 > column_gap:
            cells.append([word])
        else:
            cells[-1].append(word)
    return cells


def looks_scanned(page: pymupdf.Page, text: str) -> bool:
    """True when the page's content is in an image rather than in its text."""
    visible = len("".join(text.split()))
    if visible < settings.scan_text_threshold:
        return True
    return (
        visible < settings.image_page_text_threshold and _image_coverage(page) >= settings.image_page_coverage
    )


def _image_coverage(page: pymupdf.Page) -> float:
    """Share of the page covered by images, 0 to 1 (overlaps count twice, hence the cap)."""
    covered = sum(abs(pymupdf.Rect(info["bbox"]) & page.rect) for info in page.get_image_info())
    return min(covered / abs(page.rect), 1.0)
