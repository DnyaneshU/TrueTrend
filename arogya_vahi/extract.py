"""Command line: extract the supported lab tests from a report PDF and save them.

    arogya-extract path/to/report.pdf [--force] [--model gemma4:e2b]
    (or: python -m arogya_vahi.extract ...)

Pages are read by arogya_vahi.pages, Gemma is called by arogya_vahi.gemma, the answers
are merged by arogya_vahi.pipeline, and each result is normalised and checked against
the PDF by arogya_vahi.verify, and the report is matched to a family member by
arogya_vahi.patients. This module stores the original, saves the report to SQLite and
prints it as JSON.
"""

import argparse
import hashlib
import json
import logging
import os
import re
import sqlite3
import sys
import tempfile
import time
from contextlib import closing
from datetime import date
from pathlib import Path

from arogya_vahi import cli, db, gemma, patients
from arogya_vahi.config import settings
from arogya_vahi.dates import parse_date
from arogya_vahi.errors import UserError
from arogya_vahi.models import Extraction, PageInput, ReportOutput, ReportRecord, SavedResult
from arogya_vahi.pages import open_pdf, read_pages
from arogya_vahi.pipeline import DATE_FIELDS, extract_pages
from arogya_vahi.text import plural
from arogya_vahi.verify import verify_results

logger = logging.getLogger(__name__)


def run(
    pdf_path: Path,
    *,
    model: str | None = None,
    force: bool = False,
    db_path: Path | None = None,
    originals_dir: Path | None = None,
) -> ReportOutput | None:
    """Extract one report PDF, save it, and return what was saved.

    Returns None, saving nothing, when the same file was already extracted and force
    is False. Raises UserError for problems the user can fix. Nothing is written to
    the database until every page has been read.
    """
    model = model or settings.model
    data = _read(pdf_path)
    sha256 = hashlib.sha256(data).hexdigest()
    with open_pdf(pdf_path, data) as doc:
        if not force and (existing := _saved_report_id(sha256, db_path)) is not None:
            logger.info(
                "%s was already extracted as report #%d. Use --force to redo it.", pdf_path.name, existing
            )
            return None
        stored = _store_original(data, sha256, originals_dir or settings.originals_dir)
        pages = read_pages(doc)
        logger.info(
            "Reading %s: %s with %s. The first page also loads the model, so it takes longer.",
            pdf_path.name, plural(len(pages), "page"), model,
        )  # fmt: skip
        started = time.perf_counter()
        extraction = extract_pages(
            pages,
            extract=lambda page, retry: gemma.extract_results(page, model, retry),
            transcribe=lambda page: gemma.transcribe(page, model),
        )
        seconds = round(time.perf_counter() - started, 1)
        results = verify_results(extraction.results, doc)

    dates, warnings = _dates(extraction, pages)
    record = _record(extraction, dates, pages, stored, sha256, model, seconds)
    report_id, patient_id, patient_note = _save(record, results, db_path, replace=force)
    if patient_note:
        warnings.append(patient_note)
    for warning in warnings:
        logger.warning(warning)
    verified = sum(result.status == "verified" for result in results)
    logger.info(
        "Saved report #%d%s: %s, %d verified against the PDF, %d to check; %s s.",
        report_id, f" for patient #{patient_id}" if patient_id else "", plural(len(results), "result"),
        verified, len(results) - verified, seconds,
    )  # fmt: skip
    header = extraction.header
    return ReportOutput(
        report_id=report_id,
        file=str(pdf_path),
        sha256=sha256,
        model=model,
        seconds=seconds,
        patient_id=patient_id,
        patient_name=header.patient_name,
        age=header.age,
        sex=header.sex,
        lab_name=header.lab_name,
        sample_date=dates["sample_date"],
        sample_date_text=header.sample_date,
        report_date=dates["report_date"],
        report_date_text=header.report_date,
        pages=extraction.pages,
        warnings=warnings,
        results=results,
    )


def _read(pdf_path: Path) -> bytes:
    """The file's bytes, read once: what is hashed, read and stored are the same."""
    if not pdf_path.is_file():
        raise UserError(f"File not found: {pdf_path}")
    return pdf_path.read_bytes()


def _saved_report_id(sha256: str, db_path: Path | None) -> int | None:
    with closing(db.connect(db_path)) as conn:
        return db.find_report_id(conn, sha256)


def _dates(extraction: Extraction, pages: list[PageInput]) -> tuple[dict[str, str | None], list[str]]:
    """The report's dates as ISO dates, and warnings about them and about the report as a whole.

    A sample date is used only when it is printed in the PDF's own text: it orders the
    timeline and is said aloud in the summary, so Gemma's reading alone is not enough.
    """
    header = extraction.header
    dates = {name: parse_date(getattr(header, name)) for name in DATE_FIELDS}
    warnings = list(extraction.warnings)
    if header.sample_date is None:
        warnings.append(
            "No sample collection date found; the report can't go on the timeline until one is added."
        )
    elif dates["sample_date"] is None:
        warnings.append(f"Sample date '{header.sample_date}' could not be read as a date.")
    elif not _printed_in(header.sample_date, pages):
        warnings.append(
            f"Sample date '{header.sample_date}' is not printed in the PDF's text (a scan?); "
            "the report stays off the timeline until it is checked."
        )
        dates["sample_date"] = None
    elif dates["sample_date"] > date.today().isoformat():
        warnings.append(f"Sample date {dates['sample_date']} is in the future; check the report.")
    if header.report_date is not None and dates["report_date"] is None:
        warnings.append(f"Report date '{header.report_date}' could not be read as a date.")
    if not extraction.results:
        warnings.append("None of the supported tests was found on any page.")
    return dates, warnings


def _printed_in(text: str, pages: list[PageInput]) -> bool:
    """True when the text is printed, whole, on a digital page (not just in Gemma's reading of
    a scan): "1/09/2026" is not printed in "21/09/2026"."""
    wanted = re.compile(rf"(?<!\d){re.escape(_comparable(text))}(?!\d)")
    return any(wanted.search(_comparable(page.text)) for page in pages if page.mode == "text")


def _comparable(text: str) -> str:
    return " ".join(text.replace(" | ", " ").casefold().split())


def _record(
    extraction: Extraction,
    dates: dict[str, str | None],
    pages: list[PageInput],
    stored: Path,
    sha256: str,
    model: str,
    seconds: float,
) -> ReportRecord:
    header = extraction.header
    return ReportRecord(
        lab_name=header.lab_name,
        sample_date=dates["sample_date"],
        report_date=dates["report_date"],
        file_path=db.stored_path(stored),
        sha256=sha256,
        is_scanned=any(page.mode == "vision" for page in pages),
        patient_name_raw=header.patient_name,
        patient_age_raw=header.age,
        patient_sex_raw=header.sex,
        extract_model=model,
        extract_seconds=seconds,
        raw_json=json.dumps(
            {"pages": [reply.model_dump(mode="json") for reply in extraction.replies]}, ensure_ascii=False
        ),
    )


def _save(
    record: ReportRecord, results: list[SavedResult], db_path: Path | None, replace: bool
) -> tuple[int, int | None, str | None]:
    """Save the report for the patient it names: (report id, patient id, a note about the patient).

    A report read again stays with the patient it was for, who may have been set by hand.
    The patient and the report are saved in one transaction, under the write lock from
    the start, so another run can't add the same new patient meanwhile.
    """
    try:
        with closing(db.connect(db_path)) as conn, db.write(conn):
            patient_id = db.report_patient_id(conn, record.sha256) if replace else None
            note = None
            if patient_id is None:
                match = patients.match_report(conn, record.person)
                patient_id, note = (match.patient.id if match.patient else None), match.note
                if match.new:
                    logger.info("New patient #%d: %s.", match.patient.id, match.patient.display_name)
            report_id = db.save_report(
                conn, record.model_copy(update={"patient_id": patient_id}), results, replace=replace
            )
            return report_id, patient_id, note
    except sqlite3.IntegrityError as error:
        if "reports.sha256" not in str(error):
            raise
        raise UserError(
            "Another run saved this report while it was being read. Nothing new was saved."
        ) from None


def _store_original(data: bytes, sha256: str, originals_dir: Path) -> Path:
    """Keep a copy of the PDF named by its content, so the same file is stored once.

    Written to a temporary file first and then renamed, so a crash never leaves a
    half-written original; an existing copy is trusted only if its content matches.
    """
    originals_dir.mkdir(parents=True, exist_ok=True)
    stored = originals_dir / f"{sha256}.pdf"
    if stored.is_file() and hashlib.sha256(stored.read_bytes()).hexdigest() == sha256:
        return stored
    with tempfile.NamedTemporaryFile(dir=originals_dir, suffix=".part", delete=False) as part:
        part.write(data)
        part.flush()
        os.fsync(part.fileno())
    try:
        os.replace(part.name, stored)
    except PermissionError:
        raise UserError(
            f"The stored copy {stored.name} is open in another program; close it and try again."
        ) from None
    finally:
        Path(part.name).unlink(missing_ok=True)
    return stored


def _command(args: argparse.Namespace) -> int:
    output = run(args.pdf, model=args.model, force=args.force)
    if output is not None:
        print(output.model_dump_json(indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = cli.parser(
        "extract",
        "Extract the supported lab tests from a lab report PDF with local Gemma, "
        "print them as JSON and save them to the local database.",
    )
    parser.add_argument("pdf", type=Path, help="path to the lab report PDF")
    parser.add_argument(
        "--model",
        default=settings.model,
        help=f"Ollama model (default: {settings.model}; faster fallback: gemma4:e2b)",
    )
    parser.add_argument("--force", action="store_true", help="re-extract a report that is already saved")
    return cli.run_command(parser, _command, argv)


if __name__ == "__main__":
    sys.exit(main())
