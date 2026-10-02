"""Extract lab test results from a lab report PDF with local Gemma.

    python -m app.extract path/to/report.pdf [--force] [--model gemma4:e2b]

One Gemma call per page. Digital pages are sent as text rebuilt line by line;
pages with almost no text (scans) are sent as an image. Gemma copies values
exactly as printed; code assigns page numbers, parses dates and saves the rows.
Every saved row is `needs_check` until verify.py exists.
"""
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pymupdf

SCAN_TEXT_THRESHOLD = 50  # fewer non-whitespace characters than this: treat the page as scanned
RENDER_DPI = 150
COLUMN_GAP = 0.6          # a gap wider than this × text height separates table columns


class ExtractError(Exception):
    """A problem the user can fix. main() prints it as one line and exits with 1."""


@dataclass
class PageInput:
    number: int                      # 1-based, assigned by code
    total: int
    mode: Literal["text", "vision"]
    text: str = ""
    image: bytes | None = None       # PNG, vision pages only


def open_pdf(path: Path) -> pymupdf.Document:
    if not path.is_file():
        raise ExtractError(f"File not found: {path}")
    try:
        doc = pymupdf.open(path)
    except RuntimeError:  # pymupdf.FileDataError and friends
        raise ExtractError(f"Not a readable PDF: {path}") from None
    if not doc.is_pdf:
        doc.close()
        raise ExtractError(f"Not a PDF: {path}")
    if doc.needs_pass:
        doc.close()
        raise ExtractError(
            f"{path.name} is password-protected. Open it once, save a copy "
            "without a password, and run this on the copy."
        )
    if doc.page_count == 0:
        doc.close()
        raise ExtractError(f"{path.name} has no pages.")
    return doc


def page_text(page: pymupdf.Page) -> str:
    """The page's text rebuilt one printed line at a time.

    Words whose vertical centres are within half a text height form one line,
    read left to right. A gap wider than COLUMN_GAP × text height becomes " | ",
    so a table row reads "HbA1c | 6.8 | % | 4.0 - 5.6".
    """
    words = page.get_text("words")  # (x0, y0, x1, y1, word, block_no, line_no, word_no)
    if not words:
        return ""
    heights = sorted(w[3] - w[1] for w in words)
    height = heights[len(heights) // 2] or 1.0
    lines: list[tuple[float, list]] = []
    for word in sorted(words, key=lambda w: ((w[1] + w[3]) / 2, w[0])):
        centre = (word[1] + word[3]) / 2
        if lines and centre - lines[-1][0] <= height / 2:
            lines[-1][1].append(word)
        else:
            lines.append((centre, [word]))
    rendered = []
    for _, line in lines:
        line.sort(key=lambda w: w[0])
        parts = [line[0][4]]
        for previous, word in zip(line, line[1:]):
            parts.append(" | " if word[0] - previous[2] > COLUMN_GAP * height else " ")
            parts.append(word[4])
        rendered.append("".join(parts))
    return "\n".join(rendered)


def read_pages(doc: pymupdf.Document) -> list[PageInput]:
    pages = []
    for index, page in enumerate(doc):
        number, total = index + 1, doc.page_count
        text = page_text(page)
        if len("".join(text.split())) >= SCAN_TEXT_THRESHOLD:
            pages.append(PageInput(number, total, "text", text=text))
        else:
            png = page.get_pixmap(dpi=RENDER_DPI).tobytes("png")
            pages.append(PageInput(number, total, "vision", image=png))
    return pages
