"""Calls the real local Gemma through Ollama (about two minutes).

Run with:  AROGYA_LIVE=1 .venv/Scripts/python -m pytest tests/test_live_gemma.py -v
"""

import os

import pytest

from arogya_vahi.extract import run

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("AROGYA_LIVE") != "1", reason="set AROGYA_LIVE=1 to call the real local Gemma"
    ),
]


def test_real_gemma_extracts_a_digital_report(make_pdf, report_page):
    out = run(make_pdf([report_page]))
    # 2 supported tests found; "Estimated Average Glucose" (a look-alike) left out
    assert {r.test_code: r.value_text for r in out.results} == {"HBA1C": "7.2", "HB": "12.1"}
    assert out.sample_date == "2026-09-12"
    assert out.lab_name == "SUNRISE DIAGNOSTICS"


def test_real_gemma_extracts_a_scanned_report(make_scan_pdf, report_page):
    out = run(make_scan_pdf(report_page))
    assert out.pages[0].mode == "vision"
    assert {r.test_code: r.value_text for r in out.results} == {"HBA1C": "7.2", "HB": "12.1"}
    # a scan has no text to check against: nothing is verified, and the date stays off the timeline
    assert {r.status for r in out.results} == {"needs_check"} and out.sample_date is None
