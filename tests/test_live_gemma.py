"""Calls the real local Gemma through Ollama (about two minutes).

Run with:  AROGYA_LIVE=1 .venv/Scripts/python -m pytest tests/test_live_gemma.py -v
"""
import os

import pymupdf
import pytest

from app.extract import run

pytestmark = pytest.mark.skipif(
    os.environ.get("AROGYA_LIVE") != "1",
    reason="set AROGYA_LIVE=1 to call the real local Gemma",
)


def test_real_gemma_extracts_a_digital_report(make_pdf, report_page, tmp_path):
    out = run(make_pdf([report_page]), db_path=tmp_path / "live.db", originals_dir=tmp_path / "originals")
    # 2 MVP tests found; "Estimated Average Glucose" (a look-alike) left out
    assert {r["test_code"]: r["value_text"] for r in out["results"]} == {"HBA1C": "7.2", "HB": "12.1"}
    assert out["sample_date"] == "2026-09-12"
    assert out["lab_name"] == "SUNRISE DIAGNOSTICS"


def test_real_gemma_extracts_a_scanned_report(make_pdf, report_page, tmp_path):
    with pymupdf.open(make_pdf([report_page], name="digital.pdf")) as digital:
        scan_png = digital[0].get_pixmap(dpi=150).tobytes("png")
    with pymupdf.open() as scan:
        page = scan.new_page()
        page.insert_image(page.rect, stream=scan_png)
        scan.save(tmp_path / "scan.pdf")
    out = run(tmp_path / "scan.pdf", db_path=tmp_path / "live.db", originals_dir=tmp_path / "originals")
    assert out["pages"][0]["mode"] == "vision"
    assert {r["test_code"]: r["value_text"] for r in out["results"]} == {"HBA1C": "7.2", "HB": "12.1"}
