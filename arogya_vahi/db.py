"""SQLite storage for reports, their results and the patients they are for.

The database and the stored original PDFs live in settings.storage_dir: real reports
and patient data never go into the repo. All SQL is in this module.
"""

import json
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

from arogya_vahi.config import settings
from arogya_vahi.models import Patient, ReportPerson, ReportRecord, Result, SavedResult, TimelinePoint
from arogya_vahi.resources import read_text
from arogya_vahi.text import split_flag

SCHEMA = read_text("schema.sql")
# Bumped when an older database needs migrating: see _migrate.
SCHEMA_VERSION = 2  # 2: reports are indexed by patient
# Columns added after the first databases were made: CREATE TABLE IF NOT EXISTS leaves
# an older table as it was, so _migrate adds them.
ADDED_COLUMNS = (
    ("results", "flag", "TEXT"),
    ("results", "qualifier", "TEXT"),
    ("results", "ref_verified", "INTEGER NOT NULL DEFAULT 0 CHECK (ref_verified IN (0, 1))"),
)

REPORT_COLUMNS = tuple(ReportRecord.model_fields)
RESULT_COLUMNS = (
    "test_code", "raw_name", "raw_value_text", "unit", "ref_text", "flag", "page",
    "value", "qualifier", "value_std", "unit_std", "ref_low", "ref_high",
    "bbox_json", "ref_verified", "status", "check_notes",
)  # fmt: skip
PRINTED_COLUMNS = ("page", "test_code", "raw_name", "raw_value_text", "flag", "unit", "ref_text")
# Result columns whose model field has another name.
_FIELD_OF_COLUMN = {"raw_value_text": "value_text", "bbox_json": "bbox", "check_notes": "notes"}

_INSERT_REPORT = (
    f"INSERT INTO reports ({', '.join(REPORT_COLUMNS)}) VALUES ({', '.join(['?'] * len(REPORT_COLUMNS))})"
)
_INSERT_RESULT = (
    f"INSERT INTO results (report_id, {', '.join(RESULT_COLUMNS)}) "
    f"VALUES (?, {', '.join(['?'] * len(RESULT_COLUMNS))})"
)
_UPDATE_RESULT = f"UPDATE results SET {', '.join(f'{column} = ?' for column in RESULT_COLUMNS)} WHERE id = ?"
_SELECT_PRINTED = f"SELECT id, {', '.join(PRINTED_COLUMNS)} FROM results WHERE report_id = ? ORDER BY id"
_SELECT_PATIENT = "SELECT id, display_name, aliases_json, sex, birth_year FROM patients"
_SELECT_PERSON = (
    "SELECT id AS report_id, patient_id, patient_name_raw AS name, patient_age_raw AS age, "
    "patient_sex_raw AS sex, sample_date, report_date, lab_name FROM reports"
)
_PEOPLE_ORDER = "ORDER BY COALESCE(sample_date, report_date), id"


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    """Open the database (settings.db_path by default), creating or migrating it if needed.

    Waits up to settings.db_timeout seconds while another command is writing.
    """
    path = Path(path or settings.db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=settings.db_timeout)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if _version(conn) < SCHEMA_VERSION:
        _migrate(conn)
    return conn


def find_report_id(conn: sqlite3.Connection, sha256: str) -> int | None:
    """The id of the report saved from the file with this sha256, if any."""
    row = conn.execute("SELECT id FROM reports WHERE sha256 = ?", (sha256,)).fetchone()
    return row["id"] if row else None


def save_report(
    conn: sqlite3.Connection, report: ReportRecord, results: Sequence[SavedResult], replace: bool = False
) -> int:
    """Insert a report and its results in one transaction and return the report id.

    With replace=True an existing report with the same sha256 is deleted inside the
    same transaction (its results cascade), so a failed save keeps the old data.
    """
    with conn:
        if replace:
            conn.execute("DELETE FROM reports WHERE sha256 = ?", (report.sha256,))
        report_row = report.model_dump()
        report_id = conn.execute(_INSERT_REPORT, [report_row[column] for column in REPORT_COLUMNS]).lastrowid
        conn.executemany(_INSERT_RESULT, [[report_id, *_result_row(result)] for result in results])
    return report_id


def saved_reports(conn: sqlite3.Connection) -> list[tuple[int, str]]:
    """(report id, file_path) of every saved report, oldest first."""
    return [
        (row["id"], row["file_path"]) for row in conn.execute("SELECT id, file_path FROM reports ORDER BY id")
    ]


def printed_results(conn: sqlite3.Connection, report_id: int) -> list[tuple[int, Result]]:
    """(result id, the result as printed) for one report: what normalising and verifying start from."""
    return [
        (
            row["id"],
            Result(**{_FIELD_OF_COLUMN.get(column, column): row[column] for column in PRINTED_COLUMNS}),
        )
        for row in conn.execute(_SELECT_PRINTED, (report_id,))
    ]


def update_result(conn: sqlite3.Connection, result_id: int, result: SavedResult) -> None:
    """Overwrite a saved result with a re-checked one (call inside the caller's transaction)."""
    conn.execute(_UPDATE_RESULT, [*_result_row(result), result_id])


def timeline_points(conn: sqlite3.Connection, patient_id: int | None = None) -> list[TimelinePoint]:
    """Every saved result that isn't rejected and whose report has a sample date, oldest first.

    patient_id narrows it to one person; None means every report. A matched report's person
    is named as their patient is; an unmatched one as printed.
    """
    rows = conn.execute(
        "SELECT results.id AS result_id, report_id, reports.patient_id, "
        "COALESCE(patients.display_name, patient_name_raw) AS patient_name, test_code, sample_date, "
        "lab_name, raw_value_text AS value_text, value, unit, qualifier, value_std, unit_std, ref_text, "
        "ref_low, ref_high, ref_verified, status, page, file_path "
        "FROM results JOIN reports ON reports.id = results.report_id "
        "LEFT JOIN patients ON patients.id = reports.patient_id "
        "WHERE sample_date IS NOT NULL AND test_code IS NOT NULL AND status != 'rejected' "
        "AND (:patient IS NULL OR reports.patient_id = :patient) "
        "ORDER BY sample_date, report_id, results.id",
        {"patient": patient_id},
    )
    return [TimelinePoint(**row) for row in rows]


@contextmanager
def write(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """One transaction that holds the write lock from its first read, so what it reads (say,
    the patients a new report could be) can't change before it writes. Inside one already
    begun, it is part of that one."""
    if conn.in_transaction:
        yield conn
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.rollback()
        raise
    conn.commit()


def patients(conn: sqlite3.Connection) -> list[Patient]:
    """Every patient, in the order they were added."""
    return [_patient(row) for row in conn.execute(f"{_SELECT_PATIENT} ORDER BY id")]


def patient(conn: sqlite3.Connection, patient_id: int) -> Patient | None:
    row = conn.execute(f"{_SELECT_PATIENT} WHERE id = ?", (patient_id,)).fetchone()
    return _patient(row) if row else None


def add_patient(conn: sqlite3.Connection, patient: Patient) -> Patient:
    """Insert a patient (its id is ignored) and return it with its new id."""
    patient_id = conn.execute(
        "INSERT INTO patients (display_name, aliases_json, sex, birth_year) VALUES (?, ?, ?, ?)",
        _patient_row(patient),
    ).lastrowid
    return patient.model_copy(update={"id": patient_id})


def update_patient(conn: sqlite3.Connection, patient: Patient) -> None:
    """Save a patient's names, sex and birth year."""
    conn.execute(
        "UPDATE patients SET display_name = ?, aliases_json = ?, sex = ?, birth_year = ? WHERE id = ?",
        [*_patient_row(patient), patient.id],
    )


def delete_patient_if_unused(conn: sqlite3.Connection, patient_id: int) -> bool:
    """Delete a patient no report is for any more; True if they were deleted."""
    deleted = conn.execute(
        "DELETE FROM patients WHERE id = :id AND NOT EXISTS (SELECT 1 FROM reports WHERE patient_id = :id)",
        {"id": patient_id},
    )
    return deleted.rowcount > 0


def report_people(conn: sqlite3.Connection, *, to_match: bool = False) -> list[ReportPerson]:
    """Who each saved report is for, as printed, oldest sample first.

    to_match=True: only reports that print a name and are not matched to anyone yet.
    """
    where = "WHERE patient_id IS NULL AND patient_name_raw IS NOT NULL" if to_match else ""
    return [ReportPerson(**row) for row in conn.execute(f"{_SELECT_PERSON} {where} {_PEOPLE_ORDER}")]


def report_person(conn: sqlite3.Connection, report_id: int) -> ReportPerson | None:
    row = conn.execute(f"{_SELECT_PERSON} WHERE id = ?", (report_id,)).fetchone()
    return ReportPerson(**row) if row else None


def set_report_patient(conn: sqlite3.Connection, report_id: int, patient_id: int | None) -> None:
    """Record who a report is for."""
    conn.execute("UPDATE reports SET patient_id = ? WHERE id = ?", (patient_id, report_id))


def move_reports(conn: sqlite3.Connection, from_patient: int, to_patient: int) -> None:
    """Every report of one patient becomes another's."""
    conn.execute("UPDATE reports SET patient_id = ? WHERE patient_id = ?", (to_patient, from_patient))


def report_patient_id(conn: sqlite3.Connection, sha256: str) -> int | None:
    """Who the report saved from the file with this sha256 is for, if it is saved and matched."""
    row = conn.execute("SELECT patient_id FROM reports WHERE sha256 = ?", (sha256,)).fetchone()
    return row["patient_id"] if row else None


def stored_path(path: Path) -> str:
    """How a stored original is recorded in reports.file_path: its name in settings.originals_dir.

    Originals are named by their content (<sha256>.pdf), so the storage folder can move.
    """
    return path.name


def resolve_stored_path(file_path: str) -> Path:
    """The stored original a reports.file_path names (absolute paths are from older versions)."""
    path = Path(file_path)
    return path if path.is_absolute() else settings.originals_dir / path.name


def _result_row(result: SavedResult) -> list:
    """A result's values in RESULT_COLUMNS order."""
    row = {
        **result.model_dump(),
        "bbox": json.dumps(result.bbox) if result.bbox else None,
        "notes": "; ".join(result.notes) or None,
    }
    return [row[_FIELD_OF_COLUMN.get(column, column)] for column in RESULT_COLUMNS]


def _patient(row: sqlite3.Row) -> Patient:
    return Patient(
        id=row["id"],
        display_name=row["display_name"],
        aliases=json.loads(row["aliases_json"]),
        sex=row["sex"],
        birth_year=row["birth_year"],
    )


def _patient_row(patient: Patient) -> list:
    return [
        patient.display_name,
        json.dumps(patient.aliases, ensure_ascii=False),
        patient.sex,
        patient.birth_year,
    ]


def _version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def _migrate(conn: sqlite3.Connection) -> None:
    """Create the tables, or bring an older database up to date, one command at a time."""
    conn.executescript(SCHEMA)  # CREATE ... IF NOT EXISTS: safe to repeat
    conn.execute("BEGIN IMMEDIATE")  # another command migrating at the same time waits here
    try:
        if _version(conn) < SCHEMA_VERSION:
            for table, column, definition in ADDED_COLUMNS:
                existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
                if column not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            _split_saved_flags(conn)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def _split_saved_flags(conn: sqlite3.Connection) -> None:
    """Results saved before flags were split out have "H 168.0" as their value text."""
    rows = conn.execute("SELECT id, raw_value_text FROM results WHERE flag IS NULL").fetchall()
    for row in rows:
        flag, value_text = split_flag(row["raw_value_text"])
        if flag:
            conn.execute(
                "UPDATE results SET flag = ?, raw_value_text = ? WHERE id = ?", (flag, value_text, row["id"])
            )
