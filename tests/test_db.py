"""SQLite storage: saving, replacing, migrating older databases, and reading timelines back."""

import re
import sqlite3
import typing
from contextlib import closing

import pytest
from factories import report_record, rows, saved_result

from arogya_vahi import db
from arogya_vahi.config import settings
from arogya_vahi.models import Source, Status


def test_connect_creates_tables_and_records_the_schema_version(conn):
    names = {name for (name,) in rows(conn, "SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"patients", "reports", "results"} <= names
    assert rows(conn, "PRAGMA user_version") == [(db.SCHEMA_VERSION,)]


def test_connect_creates_a_missing_folder(tmp_path):
    path = tmp_path / "new" / "folder" / "test.db"
    db.connect(path).close()
    assert path.exists()


def test_save_report_and_find_it(conn):
    report_id = db.save_report(
        conn,
        report_record(),
        [
            saved_result("HBA1C", 7.2, bbox=(260.0, 141.0, 273.0, 153.0)),
            saved_result("HB", 12.1, status="needs_check", notes=["a", "b"]),
        ],
    )
    assert db.find_report_id(conn, "abc123") == report_id
    assert rows(
        conn, "SELECT test_code, raw_value_text, status, check_notes, bbox_json, page FROM results"
    ) == [
        ("HBA1C", "7.2", "verified", None, "[260.0, 141.0, 273.0, 153.0]", 1),
        ("HB", "12.1", "needs_check", "a; b", None, 1),
    ]
    assert rows(conn, "SELECT created_at IS NOT NULL FROM reports") == [(1,)]


def test_find_report_id_unknown(conn):
    assert db.find_report_id(conn, "nope") is None


def test_duplicate_sha256_rejected(conn):
    db.save_report(conn, report_record(), [])
    with pytest.raises(sqlite3.IntegrityError, match=r"reports.sha256"):
        db.save_report(conn, report_record(), [])


def test_replace_deletes_old_report_and_its_results(conn):
    old_id = db.save_report(conn, report_record(), [saved_result(value=7.2)])
    new_id = db.save_report(conn, report_record(), [saved_result(value=7.0)], replace=True)
    assert new_id != old_id  # AUTOINCREMENT: ids are never reused
    assert rows(conn, "SELECT report_id, raw_value_text FROM results") == [(new_id, "7")]


def test_failed_replace_keeps_old_report(conn):
    old_id = db.save_report(conn, report_record(), [saved_result()])
    with pytest.raises(sqlite3.IntegrityError):
        db.save_report(conn, report_record(), [saved_result(construct=True, status="bogus")], replace=True)
    assert db.find_report_id(conn, "abc123") == old_id
    assert rows(conn, "SELECT COUNT(*) FROM results") == [(1,)]


@pytest.mark.filterwarnings("ignore:Pydantic serializer warnings")  # the invalid value is the point
@pytest.mark.parametrize("overrides", [{"source": "fax"}, {"is_scanned": 2}])
def test_the_database_enforces_its_own_constraints(conn, overrides):
    with pytest.raises(sqlite3.IntegrityError):
        db.save_report(conn, report_record(construct=True, **overrides), [])


@pytest.mark.parametrize("name, literal", [("status", Status), ("source", Source)])
def test_schema_checks_match_the_models(name, literal):
    allowed = re.search(rf"CHECK \({name} IN \(([^)]*)\)\)", db.SCHEMA)[1]
    assert set(re.findall(r"'(\w+)'", allowed)) == set(typing.get_args(literal))


def test_printed_results_and_update(conn):
    report_id = db.save_report(conn, report_record(), [saved_result("TG", 168.0, flag="H", ref_text="< 150")])
    ((result_id, printed),) = db.printed_results(conn, report_id)
    assert (printed.test_code, printed.value_text, printed.flag, printed.ref_text) == (
        "TG",
        "168",
        "H",
        "< 150",
    )
    with conn:
        db.update_result(conn, result_id, saved_result("TG", 170.0, status="needs_check", notes=["x"]))
    assert rows(conn, "SELECT raw_value_text, status, check_notes FROM results") == [
        ("170", "needs_check", "x")
    ]
    assert db.saved_reports(conn) == [(report_id, "abc123.pdf")]


# ---------------------------------------------------------------- older databases


def older_database(path, *, without=("flag", "qualifier", "ref_verified")):
    schema = db.SCHEMA
    for column in without:
        schema = re.sub(rf"\n    {column} [^\n]*", "", schema)
    with closing(sqlite3.connect(path)) as old:
        old.executescript(schema)
        old.execute(
            "INSERT INTO reports (source, file_path, sha256) "
            "VALUES ('upload', 'storage/originals/a.pdf', 'a')"
        )
        old.execute(
            "INSERT INTO results (report_id, test_code, raw_name, raw_value_text, page) "
            "VALUES (1, 'TG', 'Triglyceride', 'H 168.0', 1)"
        )
        old.commit()


def test_an_older_database_is_migrated_once(tmp_path):
    path = tmp_path / "old.db"
    older_database(path)
    with closing(db.connect(path)) as conn:
        columns = {name for (_, name, *_) in rows(conn, "PRAGMA table_info(results)")}
        assert {"flag", "qualifier", "ref_verified"} <= columns
        # results saved before flags were split out had them in the value text
        assert rows(conn, "SELECT raw_value_text, flag, ref_verified FROM results") == [("168.0", "H", 0)]
        assert rows(conn, "PRAGMA user_version") == [(db.SCHEMA_VERSION,)]
    with closing(db.connect(path)) as again:  # nothing left to do
        assert rows(again, "SELECT raw_value_text FROM results") == [("168.0",)]


def test_paths_saved_by_older_versions_still_resolve(tmp_path):
    assert db.resolve_stored_path("storage/originals/abc.pdf") == settings.originals_dir / "abc.pdf"
    outside = tmp_path / "abc.pdf"  # an absolute path, from AROGYA_STORAGE_DIR outside the repo
    assert db.resolve_stored_path(str(outside)) == outside


def test_stored_paths_survive_moving_the_storage_folder(tmp_path, monkeypatch):
    recorded = db.stored_path(settings.originals_dir / "abc.pdf")
    monkeypatch.setattr(settings, "storage_dir", tmp_path / "moved")
    assert db.resolve_stored_path(recorded) == settings.originals_dir / "abc.pdf"
    assert db.resolve_stored_path(recorded).parent.parent.name == "moved"


# ---------------------------------------------------------------- timelines


def test_timeline_points_are_dated_results_oldest_first_without_rejected_ones(conn):
    db.save_report(
        conn,
        report_record(sha256="b", sample_date="2026-04-15", patient_name_raw=None),
        [saved_result(value=7.4)],
    )
    db.save_report(
        conn,
        report_record(sha256="a", sample_date="2026-01-15"),
        [saved_result(value=7.0), saved_result("HB", 12.0, status="rejected")],
    )
    db.save_report(conn, report_record(sha256="c"), [saved_result(value=9.9)])  # no sample date
    points = db.timeline_points(conn)
    assert [(p.sample_date.isoformat(), p.value_text, p.patient_name) for p in points] == [
        ("2026-01-15", "7", "Sunita Patil"),
        ("2026-04-15", "7.4", None),
    ]


def test_timeline_points_of_one_patient(conn):
    db.save_report(conn, report_record(sample_date="2026-01-15"), [saved_result()])
    assert db.timeline_points(conn, patient_id=1) == []
    assert len(db.timeline_points(conn)) == 1
