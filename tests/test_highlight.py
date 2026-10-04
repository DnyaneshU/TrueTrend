"""The page picture that shows a person where a value is printed."""

import pytest

from truetrend.errors import UserError
from truetrend.highlight import page_png


def size_of(png: bytes) -> tuple[int, int]:
    """The picture's size in pixels, read from the PNG's own header."""
    return int.from_bytes(png[16:20], "big"), int.from_bytes(png[20:24], "big")


def test_the_page_is_drawn_as_a_png(make_pdf, report_page):
    png = page_png(make_pdf([report_page]), page=1)
    assert png.startswith(b"\x89PNG")


def test_a_marked_page_differs_from_the_same_page_unmarked(make_pdf, report_page):
    pdf = make_pdf([report_page])
    plain = page_png(pdf, page=1)
    marked = page_png(pdf, page=1, bbox=(50, 300, 120, 312))
    assert marked != plain
    assert size_of(marked) == size_of(plain)  # the mark is drawn on the page, not beside it


def test_a_box_outside_the_page_is_ignored_rather_than_drawn_somewhere_wrong(make_pdf, report_page):
    pdf = make_pdf([report_page])
    assert page_png(pdf, page=1, bbox=(5000, 5000, 5100, 5100)) == page_png(pdf, page=1)


def test_the_second_page_is_not_the_first(make_pdf, report_page):
    pdf = make_pdf([report_page, [(50, 100, "Page two")]])
    assert page_png(pdf, page=2) != page_png(pdf, page=1)


@pytest.mark.parametrize("page", [0, 3, -1])
def test_a_page_the_report_does_not_have_is_refused(make_pdf, report_page, page):
    with pytest.raises(UserError, match="page"):
        page_png(make_pdf([report_page, [(50, 100, "two")]]), page=page)


def test_a_huge_page_is_drawn_no_wider_than_the_limit(make_pdf, report_page, monkeypatch):
    from truetrend.config import settings

    monkeypatch.setattr(settings, "highlight_max_width", 400)
    width, _ = size_of(page_png(make_pdf([report_page]), page=1))
    assert width == 400


def test_a_narrow_page_is_drawn_wider_so_the_print_can_be_read(make_pdf, report_page, monkeypatch):
    from truetrend.config import settings

    monkeypatch.setattr(settings, "highlight_max_width", 4000)
    width, _ = size_of(page_png(make_pdf([report_page]), page=1))
    assert width == 4000
