"""Re-checking saved results: normalised and verified again from the database and the stored originals."""

import pytest

from app import db
from app.models import ReportRecord, SavedResult
from app.recheck import recheck


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "test.db")
    yield connection
    connection.close()


@pytest.fixture
def save(conn, make_pdf):
    """Save a report whose stored original is a synthetic PDF of `page`, with results as printed."""

    def _save(page, *results, original=True):
        pdf = make_pdf([page]) if original else make_pdf([page]).with_name("missing.pdf")
        report = ReportRecord(
            lab_name=None, sample_date=None, report_date=None, file_path=db.stored_path(pdf), sha256="abc",
            is_scanned=False, patient_name_raw=None, patient_age_raw=None, patient_sex_raw=None,
            extract_model="gemma4:e4b", extract_seconds=1.0, raw_json="{}",
        )  # fmt: skip
        db.save_report(conn, report, [SavedResult(page=1, **fields) for fields in results])

    return _save


def saved(conn, *columns):
    return [tuple(row) for row in conn.execute(f"SELECT {', '.join(columns)} FROM results ORDER BY id")]


def printed(code, name, value, unit, ref=None):
    return {"test_code": code, "raw_name": name, "value_text": value, "unit": unit, "ref_text": ref}


def test_recheck_verifies_saved_results_against_the_stored_original(conn, save, report_page):
    save(report_page, printed("HBA1C", "HbA1c", "7.2", "%"), printed("HB", "Haemoglobin", "12.1", "g/dL"))
    assert saved(conn, "status") == [("needs_check",), ("needs_check",)]  # saved without checking
    assert recheck(conn) == (2, 2)
    assert saved(conn, "status", "check_notes") == [("verified", None), ("verified", None)]
    assert all(bbox for (bbox,) in saved(conn, "bbox_json"))


def test_recheck_recomputes_the_numbers(conn, save):
    save([(50, 100, "Vitamin D, 25 Hydroxy"), (200, 100, "150"), (260, 100, "nmol/L")],
         printed("VITD", "Vitamin D", "150", "nmol/L", ref="75 - 250"))  # fmt: skip
    recheck(conn)
    assert saved(conn, "value", "value_std", "unit_std", "ref_low", "ref_high", "status") == [
        (150.0, 60.0962, "ng/mL", 30.0481, 100.16, "verified")
    ]


def test_recheck_splits_a_flag_saved_inside_the_value(conn, save):
    # rows saved before flags were split out have "H 168.0" as their value text
    save([(50, 100, "Triglyceride"), (200, 100, "H"), (220, 100, "168.0"), (260, 100, "mg/dL")],
         printed("TG", "Triglyceride", "H 168.0", "mg/dL", ref="<150"))  # fmt: skip
    recheck(conn)
    assert saved(conn, "raw_value_text", "flag", "value", "ref_high", "status") == [
        ("168.0", "H", 168.0, 150.0, "verified")
    ]


def test_a_missing_original_leaves_results_to_check(conn, save, report_page, caplog):
    save(report_page, printed("HB", "Haemoglobin", "12.1", "g/dL"), original=False)
    assert recheck(conn) == (1, 0)
    ((status, notes),) = saved(conn, "status", "check_notes")
    assert status == "needs_check" and "could not be opened" in notes
    assert "report #1: File not found" in caplog.text
