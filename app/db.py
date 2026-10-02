"""SQLite schema and queries.

The database and the stored original PDFs live in storage/, which is gitignored:
real reports and patient data never go into the repo.
"""
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STORAGE_DIR = ROOT / "storage"
DB_PATH = STORAGE_DIR / "arogya.db"
ORIGINALS_DIR = STORAGE_DIR / "originals"

SCHEMA = """
CREATE TABLE IF NOT EXISTS patients (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    display_name  TEXT NOT NULL,
    aliases_json  TEXT NOT NULL DEFAULT '[]',
    sex           TEXT,
    birth_year    INTEGER
);

CREATE TABLE IF NOT EXISTS reports (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id        INTEGER REFERENCES patients(id),
    lab_name          TEXT,
    sample_date       TEXT,                 -- ISO YYYY-MM-DD, sample collection date
    report_date       TEXT,                 -- ISO YYYY-MM-DD
    source            TEXT NOT NULL
                      CHECK (source IN ('whatsapp', 'gmail', 'upload', 'gmail_import')),
    file_path         TEXT NOT NULL,
    sha256            TEXT NOT NULL UNIQUE,
    is_scanned        INTEGER NOT NULL DEFAULT 0 CHECK (is_scanned IN (0, 1)),
    created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    patient_name_raw  TEXT,                 -- as printed; patient_id is set once matching exists
    patient_age_raw   TEXT,
    patient_sex_raw   TEXT,
    extract_model     TEXT,
    extract_seconds   REAL,
    raw_json          TEXT                  -- Gemma's reply for every page
);

CREATE TABLE IF NOT EXISTS results (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id       INTEGER NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
    test_code       TEXT,
    raw_name        TEXT NOT NULL,
    raw_value_text  TEXT NOT NULL,
    value           REAL,
    unit            TEXT,
    value_std       REAL,
    unit_std        TEXT,
    ref_low         REAL,
    ref_high        REAL,
    ref_text        TEXT,
    page            INTEGER NOT NULL,
    bbox_json       TEXT,
    status          TEXT NOT NULL DEFAULT 'needs_check'
                    CHECK (status IN ('verified', 'needs_check', 'rejected')),
    check_notes     TEXT
);

CREATE INDEX IF NOT EXISTS idx_results_report ON results(report_id);
CREATE INDEX IF NOT EXISTS idx_results_test ON results(test_code);
"""

REPORT_COLUMNS = (
    "lab_name", "sample_date", "report_date", "source", "file_path", "sha256",
    "is_scanned", "patient_name_raw", "patient_age_raw", "patient_sex_raw",
    "extract_model", "extract_seconds", "raw_json",
)
RESULT_COLUMNS = (
    "test_code", "raw_name", "raw_value_text", "unit", "ref_text", "page",
    "status", "check_notes",
)

_INSERT_REPORT = (
    f"INSERT INTO reports ({', '.join(REPORT_COLUMNS)}) "
    f"VALUES ({', '.join('?' * len(REPORT_COLUMNS))})"
)
_INSERT_RESULT = (
    f"INSERT INTO results (report_id, {', '.join(RESULT_COLUMNS)}) "
    f"VALUES (?, {', '.join('?' * len(RESULT_COLUMNS))})"
)


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    """Open the database, creating its folder and tables if needed."""
    path = Path(path or DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def find_report_id(conn: sqlite3.Connection, sha256: str) -> int | None:
    row = conn.execute("SELECT id FROM reports WHERE sha256 = ?", (sha256,)).fetchone()
    return row["id"] if row else None


def save_report(conn: sqlite3.Connection, report: dict, results: list[dict], replace: bool = False) -> int:
    """Insert a report and its results in one transaction and return the report id.

    With replace=True an existing report with the same sha256 is deleted inside the
    same transaction (its results cascade), so a failed save keeps the old data.
    `report` needs every key in REPORT_COLUMNS and each result every key in
    RESULT_COLUMNS; values may be None.
    """
    with conn:
        if replace:
            conn.execute("DELETE FROM reports WHERE sha256 = ?", (report["sha256"],))
        report_id = conn.execute(_INSERT_REPORT, [report[column] for column in REPORT_COLUMNS]).lastrowid
        conn.executemany(
            _INSERT_RESULT,
            [[report_id, *(result[column] for column in RESULT_COLUMNS)] for result in results],
        )
    return report_id
