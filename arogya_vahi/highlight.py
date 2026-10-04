"""A report page as a picture, with one value marked, so a person can check it on a phone.

Safari ignores a PDF link's #page=N, so tapping a point on a timeline cannot open the
original at the right page. It opens this picture instead: the page she would have seen,
with the value ringed where the report prints it.
"""

from pathlib import Path

import pymupdf

from arogya_vahi.config import settings
from arogya_vahi.errors import UserError
from arogya_vahi.pages import open_pdf

Box = tuple[float, float, float, float]

_MARK = (0.85, 0.1, 0.45)  # the app's pink: a ring a phone screen shows clearly
_PAD = 2.5  # points of air around the value, so the ring doesn't sit on the digits
_WIDTH = 1.6  # the ring's line, in points


def page_png(path: Path, page: int, bbox: Box | None = None) -> bytes:
    """Page `page` (1-based) as a PNG, with `bbox` ringed when it is on the page."""
    with open_pdf(path) as doc:
        if not 1 <= page <= doc.page_count:
            raise UserError(f"{path.name} has no page {page}: it has {doc.page_count}.")
        drawn = doc[page - 1]
        if bbox is not None and drawn.rect.contains(pymupdf.Rect(bbox)):
            _ring(drawn, bbox)
        zoom = _scale(drawn)
        return drawn.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom)).tobytes("png")


def _ring(page: pymupdf.Page, bbox: Box) -> None:
    """Draw the mark around the value, on the page itself."""
    x0, y0, x1, y1 = bbox
    box = pymupdf.Rect(x0 - _PAD, y0 - _PAD, x1 + _PAD, y1 + _PAD)
    page.draw_rect(box, color=_MARK, width=_WIDTH, radius=0.2)


def _scale(page: pymupdf.Page) -> float:
    """How much to magnify the page so it comes out highlight_max_width pixels across."""
    return settings.highlight_max_width / (page.rect.width or 1)
