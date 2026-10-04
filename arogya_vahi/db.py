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
from arogya_vahi.models import (
    Account,
    Patient,
    ReportPerson,
    ReportRecord,
    Result,
    ResultToCheck,
    Review,
    SavedResult,
    TimelinePoint,
    Upload,
    UploadStatus,
)
from arogya_vahi.resources import read_text
from arogya_vahi.text import split_flag

SCHEMA = read_text("schema.sql")
# Bumped when an older database needs migrating: see _migrate.
SCHEMA_VERSION = 4  # 2: reports indexed by patient; 3: uploads and reviewed results; 4: accounts
# Columns added after the first databases were made: CREATE TABLE IF NOT EXISTS leaves
# an older table as it was, so _migrate adds them.
ADDED_COLUMNS = (
    ("results", "flag", "TEXT"),
    ("results", "qualifier", "TEXT"),
    ("results", "ref_verified", "INTEGER NOT NULL DEFAULT 0 CHECK (ref_verified IN (0, 1))"),
    ("results", "reviewed", "TEXT CHECK (reviewed IN ('verified', 'rejected'))"),
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
_SELECT_UPLOAD = (
    "SELECT id, file_name, sha256, status, message, report_id, created_at, updated_at FROM uploads"
)
_NOW = "strftime('%Y-%m-%dT%H:%M:%SZ', 'now')"  # SQLite's current time, as the schema stores it


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    """Open the database (settings.db_path by default), creating or migrating it if needed.

    Waits up to settings.db_timeout seconds while another command is writing.
    """
    path = Path(path or settings.db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False: the server answers each request on whichever worker
    # thread is free, so the thread that uses a connection is not the one that opened
    # it. Each request still gets its own connection, and every write goes through
    # write(), which holds SQLite's own lock -- so nothing is shared between threads.
    conn = sqlite3.connect(path, timeout=settings.db_timeout, check_same_thread=False)
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
    is named as their patient is; an unmatched one as printed. A result a person reviewed
    has the status they gave it.
    """
    rows = conn.execute(
        "SELECT results.id AS result_id, report_id, reports.patient_id, "
        "COALESCE(patients.display_name, patient_name_raw) AS patient_name, test_code, sample_date, "
        "lab_name, raw_value_text AS value_text, value, unit, qualifier, value_std, unit_std, ref_text, "
        "ref_low, ref_high, ref_verified, COALESCE(reviewed, status) AS status, page, file_path "
        "FROM results JOIN reports ON reports.id = results.report_id "
        "LEFT JOIN patients ON patients.id = reports.patient_id "
        "WHERE sample_date IS NOT NULL AND test_code IS NOT NULL "
        "AND COALESCE(reviewed, status) != 'rejected' "
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


def latest_patient_id(conn: sqlite3.Connection) -> int | None:
    """Whose reports the app opens on: the most recently tested patient.

    Two people in a family are often tested the same day, so a tie on the sample date
    goes to whoever has more reports here -- the person this vahi is being kept for --
    rather than to whichever report happened to be saved last.
    """
    row = conn.execute(
        "SELECT patient_id, max(sample_date) AS tested, count(*) AS reports FROM reports "
        "WHERE sample_date IS NOT NULL AND patient_id IS NOT NULL "
        "GROUP BY patient_id ORDER BY tested DESC, reports DESC, patient_id LIMIT 1"
    ).fetchone()
    return row["patient_id"] if row else None


def report_patient_id(conn: sqlite3.Connection, sha256: str) -> int | None:
    """Who the report saved from the file with this sha256 is for, if it is saved and matched."""
    row = conn.execute("SELECT patient_id FROM reports WHERE sha256 = ?", (sha256,)).fetchone()
    return row["patient_id"] if row else None


def results_to_check(conn: sqlite3.Connection, patient_id: int | None = None) -> list[ResultToCheck]:
    """Saved results that need a person to check them against the original, oldest first."""
    rows = conn.execute(
        "SELECT results.id AS result_id, report_id, reports.patient_id, test_code, raw_name, "
        "raw_value_text AS value_text, unit, page, sample_date, report_date, lab_name, check_notes "
        "FROM results JOIN reports ON reports.id = results.report_id "
        "WHERE status = 'needs_check' AND reviewed IS NULL AND test_code IS NOT NULL "
        "AND (:patient IS NULL OR reports.patient_id = :patient) "
        "ORDER BY COALESCE(sample_date, report_date), report_id, results.id",
        {"patient": patient_id},
    )
    return [
        ResultToCheck(**row, notes=(row["check_notes"] or "").split("; ") if row["check_notes"] else [])
        for row in rows
    ]


def review_result(conn: sqlite3.Connection, result_id: int, decision: Review) -> bool:
    """Record a person's decision on a result ("verified" or "rejected"); False if there is no such result."""
    return conn.execute("UPDATE results SET reviewed = ? WHERE id = ?", (decision, result_id)).rowcount > 0


def report_file(conn: sqlite3.Connection, report_id: int) -> str | None:
    """reports.file_path of a report, if it is saved."""
    row = conn.execute("SELECT file_path FROM reports WHERE id = ?", (report_id,)).fetchone()
    return row["file_path"] if row else None


def delete_report(conn: sqlite3.Connection, report_id: int) -> bool:
    """Delete a report and its results (a duplicate); its stored original is kept. True if deleted."""
    return conn.execute("DELETE FROM reports WHERE id = ?", (report_id,)).rowcount > 0


def same_sample_reports(conn: sqlite3.Connection) -> list[tuple[int, int]]:
    """(earlier, later) report ids of one patient, one lab and one sample date: likely duplicates."""
    rows = conn.execute(
        "SELECT a.id AS earlier, b.id AS later FROM reports a JOIN reports b "
        "ON a.patient_id = b.patient_id AND a.sample_date = b.sample_date AND a.id < b.id "
        "AND lower(COALESCE(a.lab_name, '')) = lower(COALESCE(b.lab_name, '')) "
        "WHERE a.patient_id IS NOT NULL AND a.sample_date IS NOT NULL ORDER BY b.id, a.id"
    )
    return [(row["earlier"], row["later"]) for row in rows]


# ---------------------------------------------------------------- the upload queue


def add_upload(conn: sqlite3.Connection, file_name: str, sha256: str) -> Upload:
    """Queue a file to be read."""
    upload_id = conn.execute(
        "INSERT INTO uploads (file_name, sha256) VALUES (?, ?)", (file_name, sha256)
    ).lastrowid
    return upload(conn, upload_id)


def upload(conn: sqlite3.Connection, upload_id: int) -> Upload | None:
    row = conn.execute(f"{_SELECT_UPLOAD} WHERE id = ?", (upload_id,)).fetchone()
    return Upload(**row) if row else None


def uploads(conn: sqlite3.Connection, limit: int = 50) -> list[Upload]:
    """The most recent uploads, newest first."""
    return [Upload(**row) for row in conn.execute(f"{_SELECT_UPLOAD} ORDER BY id DESC LIMIT ?", (limit,))]


def pending_upload(conn: sqlite3.Connection, sha256: str) -> Upload | None:
    """An upload of the same bytes still waiting or being read, if any."""
    row = conn.execute(
        f"{_SELECT_UPLOAD} WHERE sha256 = ? AND status IN ('queued', 'reading') ORDER BY id", (sha256,)
    ).fetchone()
    return Upload(**row) if row else None


def saved_upload(conn: sqlite3.Connection, sha256: str) -> Upload | None:
    """The upload that saved a report from the same bytes, if its report is still saved."""
    row = conn.execute(
        f"{_SELECT_UPLOAD} WHERE sha256 = ? AND report_id IS NOT NULL ORDER BY id DESC", (sha256,)
    ).fetchone()
    return Upload(**row) if row else None


def claim_next_upload(conn: sqlite3.Connection) -> Upload | None:
    """The oldest queued upload, now marked as being read; None when nothing is waiting."""
    with write(conn):
        row = conn.execute(f"{_SELECT_UPLOAD} WHERE status = 'queued' ORDER BY id LIMIT 1").fetchone()
        if row is None:
            return None
        conn.execute(
            f"UPDATE uploads SET status = 'reading', updated_at = {_NOW} WHERE id = :id",
            {"id": row["id"]},
        )
    return upload(conn, row["id"])


def finish_upload(
    conn: sqlite3.Connection, upload_id: int, status: UploadStatus, message: str | None, report_id: int | None
) -> None:
    with write(conn):
        conn.execute(
            "UPDATE uploads SET status = :status, message = :message, report_id = :report, "
            f"updated_at = {_NOW} WHERE id = :id",
            {"status": status, "message": message, "report": report_id, "id": upload_id},
        )


def requeue_interrupted_uploads(conn: sqlite3.Connection) -> int:
    """Uploads left 'reading' by a stop or crash are queued again; returns how many."""
    with write(conn):
        return conn.execute("UPDATE uploads SET status = 'queued' WHERE status = 'reading'").rowcount


def stored_path(path: Path) -> str:
    """How a stored original is recorded in reports.file_path: its name in settings.originals_dir.

    Originals are named by their content (<sha256>.pdf), so the storage folder can move.
    """
    return path.name


def resolve_stored_path(file_path: str) -> Path:
    """The stored original a reports.file_path names (absolute paths are from older versions)."""
    path = Path(file_path)
    return path if path.is_absolute() else settings.originals_dir / path.name


def result_bbox(conn: sqlite3.Connection, result_id: int, report_id: int) -> tuple | None:
    """Where this result's value is printed, if it is this report's and its box was saved."""
    row = conn.execute(
        "SELECT bbox_json FROM results WHERE id = ? AND report_id = ?", (result_id, report_id)
    ).fetchone()
    return tuple(json.loads(row["bbox_json"])) if row and row["bbox_json"] else None


# --- Accounts and the sessions that keep a device signed in (arogya_vahi.accounts).


def _folded(name: str) -> str:
    """What an account name is matched on: case and inner spacing don't distinguish two people."""
    return " ".join(name.split()).casefold()


def add_account(conn: sqlite3.Connection, name: str, password_hash: str) -> Account:
    """Save a new account. The caller checks the name is free inside the same write."""
    cursor = conn.execute(
        "INSERT INTO accounts (name, name_folded, password_hash) VALUES (?, ?, ?)",
        (name, _folded(name), password_hash),
    )
    return Account(id=int(cursor.lastrowid or 0), name=name, password_hash=password_hash)


def account_named(conn: sqlite3.Connection, name: str) -> Account | None:
    """The account with this name, however it was typed."""
    row = conn.execute(
        "SELECT id, name, password_hash FROM accounts WHERE name_folded = ?", (_folded(name),)
    ).fetchone()
    return Account(**row) if row else None


def account_count(conn: sqlite3.Connection) -> int:
    """How many accounts exist: none means the app asks the first person to make one."""
    return conn.execute("SELECT count(*) FROM accounts").fetchone()[0]


def add_session(conn: sqlite3.Connection, account_id: int, token_hash: str, days: int) -> None:
    """Remember a signed-in device until `days` from now."""
    conn.execute(
        "INSERT INTO sessions (token_hash, account_id, expires_at) "
        f"VALUES (?, ?, datetime('now', '+{int(days)} days'))",
        (token_hash, account_id),
    )


def account_of_session(conn: sqlite3.Connection, token_hash: str) -> Account | None:
    """Whose session this is, or None when it is unknown, signed out or expired."""
    row = conn.execute(
        "SELECT a.id, a.name, a.password_hash FROM sessions s JOIN accounts a ON a.id = s.account_id "
        "WHERE s.token_hash = ? AND s.expires_at > datetime('now')",
        (token_hash,),
    ).fetchone()
    return Account(**row) if row else None


def delete_session(conn: sqlite3.Connection, token_hash: str) -> None:
    """Sign one device out; the account's other devices stay signed in."""
    conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))


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
