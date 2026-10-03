import sqlite3
from contextlib import closing

import pytest

from app import db
from app.models import ReportRecord, SavedResult


def make_report(sha256="abc123", **overrides):
    # model_construct skips validation, so the database's own CHECK constraints are tested too
    fields = {
        "lab_name": None, "sample_date": None, "report_date": None, "source": "upload",
        "file_path": "storage/originals/abc123.pdf", "sha256": sha256, "is_scanned": False,
        "patient_name_raw": None, "patient_age_raw": None, "patient_sex_raw": None,
        "extract_model": "gemma4:e4b", "extract_seconds": 1.0, "raw_json": "{}",
    }  # fmt: skip
    return ReportRecord.model_construct(**{**fields, **overrides})


def make_result(**overrides):
    fields = {"page": 1, "test_code": "HBA1C", "raw_name": "HbA1c", "value_text": "7.2", "unit": "%",
              "ref_text": "4.0 - 5.6", "notes": [], "bbox": None}  # fmt: skip
    return SavedResult.model_construct(**{**fields, **overrides})


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "test.db")
    yield connection
    connection.close()


def test_connect_creates_tables(conn):
    names = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"patients", "reports", "results"} <= names


def test_connect_creates_missing_folder(tmp_path):
    path = tmp_path / "new" / "folder" / "test.db"
    db.connect(path).close()
    assert path.exists()


def test_save_report_and_find_it(conn):
    report_id = db.save_report(
        conn,
        make_report(),
        [
            make_result(status="verified", notes=[], bbox=(260.0, 141.0, 273.0, 153.0)),
            make_result(test_code="HB", raw_name="Haemoglobin", value_text="12.1", notes=["a", "b"]),
        ],
    )
    assert db.find_report_id(conn, "abc123") == report_id
    rows = conn.execute(
        "SELECT test_code, status, check_notes, bbox_json, page FROM results WHERE report_id = ? ORDER BY id",
        (report_id,),
    ).fetchall()
    assert [tuple(r) for r in rows] == [
        ("HBA1C", "verified", None, "[260.0, 141.0, 273.0, 153.0]", 1),
        ("HB", "needs_check", "a; b", None, 1),
    ]
    assert conn.execute("SELECT created_at FROM reports").fetchone()[0]


def test_find_report_id_unknown(conn):
    assert db.find_report_id(conn, "nope") is None


def test_duplicate_sha256_rejected(conn):
    db.save_report(conn, make_report(), [])
    with pytest.raises(sqlite3.IntegrityError):
        db.save_report(conn, make_report(), [])


def test_replace_deletes_old_report_and_its_results(conn):
    old_id = db.save_report(conn, make_report(), [make_result()])
    new_id = db.save_report(conn, make_report(), [make_result(value_text="7.0")], replace=True)
    assert new_id != old_id  # AUTOINCREMENT: ids are never reused
    assert conn.execute("SELECT COUNT(*) FROM reports").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM results WHERE report_id = ?", (old_id,)).fetchone()[0] == 0
    assert conn.execute("SELECT raw_value_text FROM results").fetchone()[0] == "7.0"


def test_failed_replace_keeps_old_report(conn):
    old_id = db.save_report(conn, make_report(), [make_result()])
    with pytest.raises(sqlite3.IntegrityError):
        db.save_report(conn, make_report(), [make_result(status="bogus")], replace=True)
    assert db.find_report_id(conn, "abc123") == old_id
    assert conn.execute("SELECT COUNT(*) FROM results").fetchone()[0] == 1


def test_unknown_source_rejected(conn):
    with pytest.raises(sqlite3.IntegrityError):
        db.save_report(conn, make_report(source="fax"), [])


def test_connect_adds_columns_missing_from_an_older_database(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    older_schema = db.SCHEMA
    for column in ("    flag            TEXT,", "    qualifier       TEXT,"):
        older_schema = older_schema.replace(column, "")
    old.executescript(older_schema)  # a database made before `flag` and `qualifier`
    old.close()
    with closing(db.connect(path)) as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(results)")}
        assert {"flag", "qualifier"} <= columns
        db.save_report(conn, make_report(), [make_result(flag="H")])


@pytest.mark.filterwarnings("ignore:Pydantic serializer warnings")  # the invalid value is the point
def test_is_scanned_must_be_0_or_1(conn):
    with pytest.raises(sqlite3.IntegrityError):
        db.save_report(conn, make_report(is_scanned=2), [])


def test_stored_paths_are_repo_relative_and_resolve_back(tmp_path):
    inside = db.ROOT / "storage" / "originals" / "abc.pdf"
    assert db.stored_path(inside) == "storage/originals/abc.pdf"
    assert db.resolve_stored_path("storage/originals/abc.pdf") == inside
    outside = tmp_path / "abc.pdf"  # AROGYA_STORAGE_DIR outside the repo
    assert db.resolve_stored_path(db.stored_path(outside)) == outside.resolve()


def test_timeline_points_are_dated_results_oldest_first_without_rejected_ones(conn):
    db.save_report(conn, make_report("b", sample_date="2026-04-15"), [make_result(value_text="7.4")])
    db.save_report(
        conn,
        make_report("a", sample_date="2026-01-15", patient_name_raw="Sunita Patil"),
        [make_result(value_text="7.0"), make_result(test_code="HB", status="rejected")],
    )
    db.save_report(conn, make_report("c"), [make_result(value_text="9.9")])  # no sample date
    points = db.timeline_points(conn)
    assert [(p.sample_date.isoformat(), p.value_text, p.patient_name) for p in points] == [
        ("2026-01-15", "7.0", "Sunita Patil"),
        ("2026-04-15", "7.4", None),
    ]


def test_timeline_points_of_one_patient(conn):
    db.save_report(conn, make_report("a", sample_date="2026-01-15"), [make_result()])
    assert db.timeline_points(conn, patient_id=1) == []
    assert len(db.timeline_points(conn)) == 1
