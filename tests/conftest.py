"""Shared test helpers. Tests only ever use synthetic PDFs, never real reports."""

import pymupdf
import pytest


@pytest.fixture
def report_page():
    """One synthetic lab report page as (x, y, text): 2 MVP tests and 1 look-alike."""
    return [
        (50, 60, "SUNRISE DIAGNOSTICS"),
        (50, 80, "Patient Name : Mrs. Sunita Patil"),
        (320, 80, "Age / Sex : 62 Y / F"),
        (50, 100, "Collected : 12/09/2026 08:10"),
        (320, 100, "Reported : 13/09/2026 14:02"),
        (50, 130, "Test Description"),
        (260, 130, "Result"),
        (320, 130, "Unit"),
        (380, 130, "Biological Ref. Interval"),
        (50, 150, "Glycosylated Haemoglobin (HbA1c)"),
        (260, 150, "7.2"),
        (320, 150, "%"),
        (380, 150, "4.0 - 5.6"),
        (50, 170, "Estimated Average Glucose"),
        (260, 170, "160"),
        (320, 170, "mg/dL"),
        (50, 190, "Haemoglobin"),
        (260, 190, "12.1"),
        (320, 190, "g/dL"),
        (380, 190, "12.0 - 15.0"),
    ]


@pytest.fixture
def make_pdf(tmp_path):
    """Build a synthetic PDF in tmp_path. Each page is a list of (x, y, text); [] is a blank page."""

    def _make(pages, name="report.pdf", password=None):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        doc = pymupdf.open()
        for items in pages:
            page = doc.new_page()
            for x, y, text in items:
                page.insert_text((x, y), text, fontsize=10)
        if password:
            doc.save(
                path, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw=password, owner_pw=password + "-owner"
            )
        else:
            doc.save(path)
        doc.close()
        return path

    return _make
