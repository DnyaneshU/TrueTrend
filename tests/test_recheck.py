"""Re-checking saved results: normalised and verified again from the database and the stored originals."""

import shutil

import pytest
from factories import report_record, rows, saved_result, table

from arogya_vahi import db
from arogya_vahi.config import settings
from arogya_vahi.recheck import main, recheck


@pytest.fixture
def save(conn, make_pdf):
    """Save a report whose stored original is a synthetic PDF of `page`, with its results unchecked."""

    def _save(page, *results, stored=True):
        pdf = make_pdf([page])
        if stored:
            settings.originals_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(pdf, settings.originals_dir / "abc123.pdf")
        unchecked = [result.model_copy(update={"status": "needs_check"}) for result in results]
        db.save_report(conn, report_record(), unchecked)

    return _save


def test_recheck_verifies_saved_results_against_the_stored_original(conn, save, report_page):
    save(report_page, saved_result("HBA1C", 7.2), saved_result("HB", 12.1))
    assert rows(conn, "SELECT status FROM results") == [("needs_check",), ("needs_check",)]
    assert recheck(conn) == (2, 2)
    assert rows(conn, "SELECT status, check_notes, bbox_json IS NOT NULL FROM results") == [
        ("verified", None, 1),
        ("verified", None, 1),
    ]


def test_recheck_recomputes_the_numbers(conn, save):
    page = [
        (50, 100, "Vitamin D, 25 Hydroxy"),
        (200, 100, "150"),
        (260, 100, "nmol/L"),
        (320, 100, "75 - 250"),
    ]
    save(page, saved_result("VITD", 150.0, unit="nmol/L", value_std=None, ref_text="75 - 250"))
    recheck(conn)
    assert rows(
        conn, "SELECT value, value_std, unit_std, ref_low, ref_high, ref_verified, status FROM results"
    ) == [(150.0, 60.0962, "ng/mL", 30.0481, 100.16, 1, "verified")]


def test_a_missing_original_leaves_results_to_check(conn, save, report_page, caplog):
    save(report_page, saved_result("HB", 12.1), stored=False)
    assert recheck(conn) == (1, 0)
    ((status, notes),) = rows(conn, "SELECT status, check_notes FROM results")
    assert status == "needs_check" and "could not be opened" in notes
    assert "report #1: File not found" in caplog.text


def test_a_test_no_longer_in_the_catalog_is_left_as_saved(conn, save, caplog):
    psa = saved_result(construct=True, test_code="PSA", raw_name="PSA")
    save(table(("HbA1c", "7.2", "%")), saved_result("HBA1C", 7.2), psa)
    assert recheck(conn) == (1, 1)
    assert rows(conn, "SELECT test_code, status FROM results") == [
        ("HBA1C", "verified"),
        ("PSA", "needs_check"),
    ]
    assert "left PSA as saved: not in the catalog" in caplog.text


def test_main_says_what_it_did(save, report_page, caplog):
    caplog.set_level("INFO", logger="arogya_vahi")
    save(report_page, saved_result("HB", 12.1))
    assert main([]) == 0
    assert "Re-checked 1 saved results: 1 verified, 0 to check." in caplog.text


def test_main_help_does_not_run_a_recheck(capsys):
    with pytest.raises(SystemExit) as exited:
        main(["--help"])
    assert exited.value.code == 0
    assert "without calling Gemma" in capsys.readouterr().out
