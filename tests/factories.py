"""Builders for the objects tests need, with sensible defaults; pass only what a test is about."""

from datetime import date
from itertools import count

from arogya_vahi import db
from arogya_vahi.lab_tests import CATALOG
from arogya_vahi.models import PrintedPerson, ReportRecord, Result, SavedResult, TimelinePoint

_ids = count(1)


def report_record(*, construct: bool = False, **overrides) -> ReportRecord:
    """A reports row. construct=True skips validation, to test the database's own CHECKs."""
    fields = {
        "lab_name": "Sunrise Diagnostics", "sample_date": None, "report_date": None, "source": "upload",
        "file_path": "abc123.pdf", "sha256": "abc123", "is_scanned": False,
        "patient_name_raw": "Sunita Patil", "patient_age_raw": None, "patient_sex_raw": None,
        "extract_model": "gemma4:e4b", "extract_seconds": 1.0, "raw_json": "{}",
    }  # fmt: skip
    fields.update(overrides)
    return ReportRecord.model_construct(**fields) if construct else ReportRecord(**fields)


def printed(code: str = "HBA1C", value: str = "7.2", unit: str | None = None, **overrides) -> Result:
    """A result as Gemma copied it off page 1; the unit defaults to the test's standard unit."""
    fields = {
        "page": 1, "test_code": code, "raw_name": CATALOG.test(code).name, "value_text": value,
        "unit": unit or CATALOG.test(code).unit, "ref_text": None,
    }  # fmt: skip
    return Result(**{**fields, **overrides})


def saved_result(
    code: str = "HBA1C", value: float = 7.2, *, construct: bool = False, **overrides
) -> SavedResult:
    """A verified, normalised result as saved (construct=True skips validation)."""
    unit = CATALOG.test(code).unit
    fields = {
        "page": 1, "test_code": code, "raw_name": CATALOG.test(code).name, "value_text": f"{value:g}",
        "value": value, "unit": unit, "value_std": value, "unit_std": unit, "status": "verified",
    }  # fmt: skip
    fields.update(overrides)
    return SavedResult.model_construct(**fields) if construct else SavedResult(**fields)


def timeline_point(
    code: str = "HBA1C",
    value: float = 7.0,
    when: date = date(2026, 1, 15),
    *,
    low=None,
    high=None,
    **overrides,
) -> TimelinePoint:
    """A verified result on a timeline: from Sunrise Diagnostics, for Sunita Patil (patient #1), in the
    standard unit.

    low/high give a verified normal range, printed as it reads ("4 - 5.6", "< 200", "> 40").
    """
    unit = CATALOG.test(code).unit
    if low is not None and high is not None:
        ref_text = f"{low:g} - {high:g}"
    elif high is not None:
        ref_text = f"< {high:g}"
    elif low is not None:
        ref_text = f"> {low:g}"
    else:
        ref_text = None
    report_id = overrides.pop("report_id", when.toordinal())
    fields = {
        "result_id": next(_ids), "report_id": report_id, "patient_id": 1, "patient_name": "Sunita Patil",
        "test_code": code,
        "sample_date": when, "lab_name": "Sunrise Diagnostics", "value_text": f"{value:g}", "value": value,
        "unit": unit, "qualifier": None, "value_std": value, "unit_std": unit, "ref_text": ref_text,
        "ref_low": low, "ref_high": high, "ref_verified": ref_text is not None, "status": "verified",
        "page": 1, "file_path": "abc123.pdf",
    }  # fmt: skip
    return TimelinePoint(**{**fields, **overrides})


def table(*rows: tuple[str, str, str], top: int = 100) -> list[tuple[int, int, str]]:
    """A page with one printed row per (name, value, unit), 20 points apart, for make_pdf."""
    items = []
    for number, (name, value, unit) in enumerate(rows):
        y = top + 20 * number
        items += [(50, y, name), (260, y, value), (320, y, unit)]
    return items


def rows(conn, sql: str, *args) -> list[tuple]:
    """A query's rows as plain tuples."""
    return [tuple(row) for row in conn.execute(sql, args)]


def printed_person(
    name: str | None = "Sunita Patil", age=None, sex=None, sample_date="2026-01-15", report_date=None
) -> PrintedPerson:
    """Who a report says it is for, as printed."""
    return PrintedPerson(name=name, age=age, sex=sex, sample_date=sample_date, report_date=report_date)


def saved_report(conn, sha256: str = "abc123", results=None, **record_fields) -> int:
    """Save a report (one verified HbA1c result unless `results` says otherwise); returns its id."""
    record = report_record(sha256=sha256, file_path=f"{sha256}.pdf", **record_fields)
    return db.save_report(conn, record, [saved_result()] if results is None else list(results))
