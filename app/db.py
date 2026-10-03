"""SQLite storage for reports and their results.

The database and the stored original PDFs live in settings.storage_dir, which is
gitignored: real reports and patient data never go into the repo.
"""

import json
import sqlite3
from collections.abc import Sequence
from importlib import resources
from pathlib import Path

from app.config import ROOT, settings
from app.models import ReportRecord, SavedResult

SCHEMA = resources.files("app").joinpath("schema.sql").read_text(encoding="utf-8")

REPORT_COLUMNS = tuple(ReportRecord.model_fields)
RESULT_COLUMNS = (
    "test_code", "raw_name", "raw_value_text", "unit", "ref_text", "flag", "page",
    "value", "qualifier", "value_std", "unit_std", "ref_low", "ref_high",
    "bbox_json", "status", "check_notes",
)  # fmt: skip

# Columns added after the first databases were made: (table, column, definition).
# CREATE TABLE IF NOT EXISTS leaves an older table as it was, so connect() adds them.
ADDED_COLUMNS = (("results", "flag", "TEXT"), ("results", "qualifier", "TEXT"))

_INSERT_REPORT = (
    f"INSERT INTO reports ({', '.join(REPORT_COLUMNS)}) VALUES ({', '.join(['?'] * len(REPORT_COLUMNS))})"
)
_INSERT_RESULT = (
    f"INSERT INTO results (report_id, {', '.join(RESULT_COLUMNS)}) "
    f"VALUES (?, {', '.join(['?'] * len(RESULT_COLUMNS))})"
)
_UPDATE_RESULT = f"UPDATE results SET {', '.join(f'{column} = ?' for column in RESULT_COLUMNS)} WHERE id = ?"


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    """Open the database (settings.db_path by default), creating its folder and tables if needed."""
    path = Path(path or settings.db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    for table, column, definition in ADDED_COLUMNS:
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    return conn


def find_report_id(conn: sqlite3.Connection, sha256: str) -> int | None:
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


def update_result(conn: sqlite3.Connection, result_id: int, result: SavedResult) -> None:
    """Overwrite a saved result with a re-checked one (call inside the caller's transaction)."""
    conn.execute(_UPDATE_RESULT, [*_result_row(result), result_id])


def stored_path(path: Path) -> str:
    """How a stored file is recorded in reports.file_path: relative to the repo root when inside it."""
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path.resolve())


def resolve_stored_path(file_path: str) -> Path:
    """The file a reports.file_path names (the inverse of stored_path)."""
    return ROOT / file_path


def _result_row(result: SavedResult) -> list:
    """A result's values in RESULT_COLUMNS order."""
    row = {
        **result.model_dump(),
        "raw_value_text": result.value_text,
        "bbox_json": json.dumps(result.bbox) if result.bbox else None,
        "check_notes": "; ".join(result.notes) or None,
    }
    return [row[column] for column in RESULT_COLUMNS]
