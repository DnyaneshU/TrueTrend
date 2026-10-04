"""Command line: normalise and verify every saved result again, without calling Gemma.

    truetrend-recheck        (or: python -m truetrend.recheck)

Run it after editing the catalog (names, units, believable limits) or after updating
the app: each result is rebuilt from what was printed and saved, and checked against
the report's stored original PDF. Reports not matched to a patient yet are matched.
"""

import argparse
import logging
import sqlite3
import sys
from contextlib import closing, nullcontext

import pymupdf

from truetrend import cli, db, patients
from truetrend.errors import UserError
from truetrend.lab_tests import CATALOG
from truetrend.pages import open_pdf
from truetrend.verify import verify_results

logger = logging.getLogger(__name__)


def recheck(conn: sqlite3.Connection) -> tuple[int, int]:
    """Re-normalise and re-verify every saved result; returns (results re-checked, how many verified).

    Results of a test no longer in the catalog are left as they are, with a warning.
    """
    known = {test.code for test in CATALOG.tests}
    total = verified = 0
    for report_id, file_path in db.saved_reports(conn):
        saved = db.printed_results(conn, report_id)
        if unknown := sorted({result.test_code for _, result in saved} - known):
            logger.warning("report #%d: left %s as saved: not in the catalog", report_id, ", ".join(unknown))
        saved = [(result_id, result) for result_id, result in saved if result.test_code in known]
        doc = _open_original(report_id, file_path)
        with nullcontext() if doc is None else doc:
            checked = verify_results([result for _, result in saved], doc)
        with db.write(conn):  # the PDF is read first: a run saving a report meanwhile waits only briefly
            for (result_id, _), result in zip(saved, checked, strict=True):
                db.update_result(conn, result_id, result)
        total += len(checked)
        verified += sum(result.status == "verified" for result in checked)
    return total, verified


def _open_original(report_id: int, file_path: str) -> pymupdf.Document | None:
    try:
        return open_pdf(db.resolve_stored_path(file_path))
    except UserError as error:
        logger.warning("report #%d: %s; its results can't be verified", report_id, error)
        return None


def _command(args: argparse.Namespace) -> int:
    with closing(db.connect()) as conn:
        total, verified = recheck(conn)
        patients.match_saved(conn)
    logger.info("Re-checked %d saved results: %d verified, %d to check.", total, verified, total - verified)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = cli.parser("recheck", "Normalise and verify every saved result again, without calling Gemma.")
    return cli.run_command(parser, _command, argv)


if __name__ == "__main__":
    sys.exit(main())
