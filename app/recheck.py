"""Command line: normalise and verify every saved result again, without calling Gemma.

    python -m app.recheck

Run it after editing data/lab_tests.toml (units, names, believable limits) or after
updating the app: each result is rebuilt from what was printed and saved, and checked
against the report's stored original PDF.
"""

import logging
import sqlite3
import sys
from contextlib import closing, nullcontext

import pymupdf

from app import db
from app.console import configure_console
from app.errors import ExtractError
from app.models import Result
from app.pages import open_pdf
from app.text import split_flag
from app.verify import verify_results

logger = logging.getLogger(__name__)


def recheck(conn: sqlite3.Connection) -> tuple[int, int]:
    """Re-normalise and re-verify every saved result; returns (results, how many verified)."""
    total = verified = 0
    reports = conn.execute("SELECT id, file_path FROM reports ORDER BY id").fetchall()
    with conn:
        for report in reports:
            rows = conn.execute(
                "SELECT id, page, test_code, raw_name, raw_value_text, unit, ref_text, flag "
                "FROM results WHERE report_id = ? ORDER BY id",
                (report["id"],),
            ).fetchall()
            doc = _open_original(report["id"], report["file_path"])
            with nullcontext() if doc is None else doc:
                checked = verify_results([_printed_result(row) for row in rows], doc)
            for row, result in zip(rows, checked, strict=True):
                db.update_result(conn, row["id"], result)
            total += len(checked)
            verified += sum(result.status == "verified" for result in checked)
    return total, verified


def _printed_result(row: sqlite3.Row) -> Result:
    """A saved result as it was printed: the columns normalisation and verification start from."""
    flag, value_text = split_flag(row["raw_value_text"])  # rows saved before flags were split out
    return Result(
        page=row["page"],
        test_code=row["test_code"],
        raw_name=row["raw_name"],
        value_text=value_text,
        flag=row["flag"] or flag,
        unit=row["unit"],
        ref_text=row["ref_text"],
    )


def _open_original(report_id: int, file_path: str) -> pymupdf.Document | None:
    try:
        return open_pdf(db.resolve_stored_path(file_path))
    except ExtractError as error:
        logger.warning("report #%d: %s; its results can't be verified", report_id, error)
        return None


def main() -> int:
    configure_console()
    with closing(db.connect()) as conn:
        total, verified = recheck(conn)
    logger.info("Re-checked %d saved results: %d verified, %d to check.", total, verified, total - verified)
    return 0


if __name__ == "__main__":
    sys.exit(main())
