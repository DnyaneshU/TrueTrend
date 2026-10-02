import sqlite3
from contextlib import closing

import pytest

from app import db
from app.normalize import renormalize


def make_report(sha256="abc123", **overrides):
    report = dict.fromkeys(db.REPORT_COLUMNS)
    report.update(source="upload", file_path="storage/originals/abc123.pdf", sha256=sha256, is_scanned=0)
    report.update(overrides)
    return report


def make_result(**overrides):
    result = {
        "test_code": "HBA1C",
        "raw_name": "HbA1c",
        "raw_value_text": "7.2",
        "unit": "%",
        "ref_text": "4.0 - 5.6",
        "flag": None,
        "page": 1,
        "status": "needs_check",
        "check_notes": "not verified yet",
        **dict.fromkeys(["value", "qualifier", "value_std", "unit_std", "ref_low", "ref_high"]),
    }
    result.update(overrides)
    return result


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
            make_result(),
            make_result(test_code="HB", raw_name="Haemoglobin", raw_value_text="12.1"),
        ],
    )
    assert db.find_report_id(conn, "abc123") == report_id
    rows = conn.execute(
        "SELECT test_code, status, check_notes, page FROM results WHERE report_id = ? ORDER BY id",
        (report_id,),
    ).fetchall()
    assert [tuple(r) for r in rows] == [
        ("HBA1C", "needs_check", "not verified yet", 1),
        ("HB", "needs_check", "not verified yet", 1),
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
    new_id = db.save_report(conn, make_report(), [make_result(raw_value_text="7.0")], replace=True)
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


def test_renormalize_recomputes_every_saved_result(conn):
    db.save_report(
        conn,
        make_report(),
        [
            make_result(
                test_code="VITD",
                raw_name="Vitamin D",
                raw_value_text="150",
                unit="nmol/L",
                ref_text="75 - 250",
            )
        ],
    )
    assert renormalize(conn) == 1
    row = conn.execute("SELECT value, value_std, unit_std, ref_low, ref_high FROM results").fetchone()
    assert tuple(row) == (150.0, 60.0962, "ng/mL", 30.0481, 100.16)


def test_renormalize_splits_a_flag_saved_inside_the_value(conn):
    # rows saved before flags were split out have "H 168.0" as their value text
    db.save_report(
        conn,
        make_report(),
        [
            make_result(
                test_code="TG",
                raw_name="Triglyceride",
                raw_value_text="H 168.0",
                unit="mg/dL",
                ref_text="<150",
            )
        ],
    )
    renormalize(conn)
    row = conn.execute("SELECT raw_value_text, flag, value, value_std, ref_high FROM results").fetchone()
    assert tuple(row) == ("168.0", "H", 168.0, 168.0, 150.0)


def test_is_scanned_must_be_0_or_1(conn):
    with pytest.raises(sqlite3.IntegrityError):
        db.save_report(conn, make_report(is_scanned=2), [])
