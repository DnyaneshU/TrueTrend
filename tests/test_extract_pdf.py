import pymupdf
import pytest

from app.extract import ExtractError, open_pdf, page_text, read_pages


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


def test_read_pages_sends_text_pages_as_text_and_blank_pages_as_images(make_pdf, report_page):
    with pymupdf.open(make_pdf([report_page, []])) as doc:
        first, second = read_pages(doc)
    assert (first.number, first.total, first.mode) == (1, 2, "text")
    assert "7.2" in first.text and first.image is None
    assert (second.number, second.total, second.mode) == (2, 2, "vision")
    assert second.image.startswith(b"\x89PNG")


def test_open_pdf_missing_file(tmp_path):
    with pytest.raises(ExtractError, match="File not found"):
        open_pdf(tmp_path / "nope.pdf")


def test_open_pdf_rejects_other_documents(tmp_path):
    notes = tmp_path / "notes.txt"
    notes.write_text("hello")
    with pytest.raises(ExtractError, match="Not a PDF"):
        open_pdf(notes)


def test_open_pdf_rejects_corrupt_file(tmp_path):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"\x00\x01 this is not a pdf")
    with pytest.raises(ExtractError, match="Not a readable PDF"):
        open_pdf(bad)


def test_open_pdf_rejects_password_protected(make_pdf, report_page):
    with pytest.raises(ExtractError, match="password-protected"):
        open_pdf(make_pdf([report_page], password="1234"))


def test_open_pdf_opens_a_good_pdf(make_pdf, report_page):
    with open_pdf(make_pdf([report_page])) as doc:
        assert doc.page_count == 1
