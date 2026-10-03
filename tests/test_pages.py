import pymupdf
import pytest

from arogya_vahi.errors import UserError
from arogya_vahi.pages import open_pdf, page_text, read_pages


def test_page_text_rebuilds_table_rows(make_pdf, report_page):
    with pymupdf.open(make_pdf([report_page])) as doc:
        lines = page_text(doc[0]).splitlines()
    assert "Glycosylated Haemoglobin (HbA1c) | 7.2 | % | 4.0 - 5.6" in lines
    assert "Haemoglobin | 12.1 | g/dL | 12.0 - 15.0" in lines
    assert "Collected : 12/09/2026 08:10 | Reported : 13/09/2026 14:02" in lines


def test_page_text_joins_slightly_offset_words(make_pdf):
    with pymupdf.open(make_pdf([[(50, 100, "Fasting Blood Sugar"), (260, 100.8, "112")]])) as doc:
        assert page_text(doc[0]) == "Fasting Blood Sugar | 112"


def test_page_text_of_blank_page_is_empty(make_pdf):
    with pymupdf.open(make_pdf([[]])) as doc:
        assert page_text(doc[0]) == ""


def test_page_text_expands_ligatures():
    # Fonts with ligatures print "Proﬁle" with one "ﬁ" character; names must match as "Profile".
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_font(fontname="F0", fontbuffer=pymupdf.Font("cjk").buffer)
        page.insert_text((50, 100), "Lipid Proﬁle", fontsize=10, fontname="F0")
        assert page_text(page) == "Lipid Profile"


def test_read_pages_sends_text_pages_as_text_and_blank_pages_as_images(make_pdf, report_page):
    with pymupdf.open(make_pdf([report_page, []])) as doc:
        first, second = read_pages(doc)
    assert (first.number, first.total, first.mode) == (1, 2, "text")
    assert "7.2" in first.text and first.image is None
    assert (second.number, second.total, second.mode) == (2, 2, "vision")
    assert second.image.startswith(b"\x89PNG")


def test_read_pages_caps_the_size_of_huge_scanned_pages():
    # Photo-to-PDF apps make pages thousands of points wide; 150 DPI would mean ~50 megapixels.
    with pymupdf.open() as doc:
        doc.new_page(width=3000, height=4000)
        (result,) = read_pages(doc)
    image = pymupdf.Pixmap(result.image)
    assert max(image.width, image.height) <= 2000


def test_read_pages_renders_a4_scans_at_150_dpi():
    with pymupdf.open() as doc:
        doc.new_page()  # A4
        (result,) = read_pages(doc)
    image = pymupdf.Pixmap(result.image)
    assert (image.width, image.height) == (1240, 1755)


def scan_image():
    """A PNG of printed results, standing in for a scanned page."""
    with pymupdf.open() as src:
        page = src.new_page()
        page.insert_text((50, 100), "Glycosylated Haemoglobin (HbA1c) 7.2 %", fontsize=10)
        return page.get_pixmap(dpi=72).tobytes("png")


def test_read_pages_sends_scan_with_typed_footer_as_image():
    # The footer is real text, but the results are inside the image: Gemma must see the image.
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_image(page.rect, stream=scan_image())
        page.insert_text(
            (50, 820), "This is a computer generated report. Scanned with CamScanner.", fontsize=8
        )
        (result,) = read_pages(doc)
    assert result.mode == "vision"


def test_read_pages_keeps_text_on_full_page_letterhead_as_text(report_page):
    # Some labs print results over a full-page letterhead image; the text is still the report.
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_image(page.rect, stream=scan_image())
        for x, y, text in report_page:
            page.insert_text((x, y), text, fontsize=10)
        (result,) = read_pages(doc)
    assert result.mode == "text" and "7.2" in result.text


def test_open_pdf_missing_file(tmp_path):
    with pytest.raises(UserError, match="File not found"):
        open_pdf(tmp_path / "nope.pdf")


def test_open_pdf_rejects_other_documents(tmp_path):
    notes = tmp_path / "notes.txt"
    notes.write_text("hello")
    with pytest.raises(UserError, match="Not a PDF"):
        open_pdf(notes)


def test_open_pdf_rejects_corrupt_file(tmp_path):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"\x00\x01 this is not a pdf")
    with pytest.raises(UserError, match="Not a readable PDF"):
        open_pdf(bad)


def test_open_pdf_rejects_password_protected(make_pdf, report_page):
    with pytest.raises(UserError, match="password-protected"):
        open_pdf(make_pdf([report_page], password="1234"))


def test_open_pdf_opens_a_good_pdf(make_pdf, report_page):
    with open_pdf(make_pdf([report_page])) as doc:
        assert doc.page_count == 1
