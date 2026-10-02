"""Command line: extract the MVP lab tests from a report PDF and save them.

    python -m app.extract path/to/report.pdf [--force] [--model gemma4:e2b]

Pages are read by app.pages, Gemma is called by app.gemma, and the answers are
merged by app.pipeline. This module stores the original, saves the report to
SQLite and prints it as JSON.
"""

import argparse
import hashlib
import json
import logging
import shutil
import sys
import time
from contextlib import closing, suppress
from datetime import date
from pathlib import Path

from app import db
from app.config import ROOT, settings
from app.dates import parse_date
from app.errors import ExtractError
from app.gemma import ask_gemma, transcribe
from app.models import Extraction, ReportOutput, SavedResult
from app.normalize import normalize
from app.pages import open_pdf, read_pages
from app.pipeline import DATE_FIELDS, extract_pages, plural

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
    is False. Raises ExtractError for problems the user can fix. Nothing is written
    to the database until every page has been read.
    """
    model = model or settings.model
    with open_pdf(pdf_path) as doc, closing(db.connect(db_path)) as conn:
        sha256 = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
        existing = db.find_report_id(conn, sha256)
        if existing is not None and not force:
            logger.info(
                "%s was already extracted as report #%d. Use --force to redo it.", pdf_path.name, existing
            )
            return None

        pages = read_pages(doc)
        logger.info(
            "Reading %s: %s with %s. The first page also loads the model, so it takes longer.",
            pdf_path.name, plural(len(pages), "page"), model,
        )  # fmt: skip
        started = time.perf_counter()
        extraction = extract_pages(
            pages,
            ask=lambda page, retry: ask_gemma(page, model, retry),
            transcribe=lambda page: transcribe(page, model),
        )
        seconds = round(time.perf_counter() - started, 1)

        header = extraction.header
        dates = {name: parse_date(getattr(header, name)) for name in DATE_FIELDS}
        warnings = extraction.warnings + _report_warnings(extraction, dates)
        stored = _store_original(pdf_path, sha256, originals_dir or settings.originals_dir)
        report = {
            "lab_name": header.lab_name,
            "sample_date": dates["sample_date"],
            "report_date": dates["report_date"],
            "source": "upload",
            "file_path": _repo_relative(stored),
            "sha256": sha256,
            "is_scanned": int(any(page.mode == "vision" for page in pages)),
            "patient_name_raw": header.patient_name,
            "patient_age_raw": header.age,
            "patient_sex_raw": header.sex,
            "extract_model": model,
            "extract_seconds": seconds,
            "raw_json": json.dumps(
                {"pages": [reply.model_dump(mode="json") for reply in extraction.replies]}, ensure_ascii=False
            ),
        }
        results = [
            SavedResult(**result.model_dump(), **normalize(result).model_dump())
            for result in extraction.results
        ]
        rows = [
            {
                **result.model_dump(),
                "raw_value_text": result.value_text,
                "check_notes": "; ".join(["not verified yet", *result.notes]),
            }
            for result in results
        ]
        report_id = db.save_report(conn, report, rows, replace=force)

    for warning in warnings:
        logger.warning(warning)
    logger.info("Saved report #%d: %s, %s s.", report_id, plural(len(results), "result"), seconds)
    return ReportOutput(
        report_id=report_id,
        file=str(pdf_path),
        sha256=sha256,
        model=model,
        seconds=seconds,
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


def _report_warnings(extraction: Extraction, dates: dict[str, str | None]) -> list[str]:
    """Problems with the report as a whole: its dates, or nothing found at all."""
    printed = extraction.header
    warnings = []
    if printed.sample_date is None:
        warnings.append(
            "No sample collection date found; the report can't go on the timeline until one is added."
        )
    elif dates["sample_date"] is None:
        warnings.append(f"Sample date '{printed.sample_date}' could not be read as a date.")
    elif dates["sample_date"] > date.today().isoformat():
        warnings.append(f"Sample date {dates['sample_date']} is in the future; check the report.")
    if printed.report_date is not None and dates["report_date"] is None:
        warnings.append(f"Report date '{printed.report_date}' could not be read as a date.")
    if not extraction.results:
        warnings.append("No MVP tests found on any page.")
    return warnings


def _store_original(pdf_path: Path, sha256: str, originals_dir: Path) -> Path:
    """Keep a copy of the PDF named by its content, so the same file is stored once."""
    originals_dir.mkdir(parents=True, exist_ok=True)
    stored = originals_dir / f"{sha256}.pdf"
    if not stored.exists():
        shutil.copyfile(pdf_path, stored)
    return stored


def _repo_relative(path: Path) -> str:
    """How a stored file is recorded in the DB: relative to the repo root when inside it."""
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path.resolve())


class _ConsoleFormatter(logging.Formatter):
    """Progress lines as they are; warnings and errors prefixed 'warning:' / 'error:'."""

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        return message if record.levelno < logging.WARNING else f"{record.levelname.lower()}: {message}"


def _configure_console() -> None:
    """UTF-8 output (Marathi names, µIU/mL survive a Windows code page) and logging to stderr."""
    for stream in (sys.stdout, sys.stderr):
        with suppress(AttributeError, ValueError):  # not a real console, e.g. under pytest
            stream.reconfigure(encoding="utf-8")
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_ConsoleFormatter())
    app_logger = logging.getLogger("app")
    app_logger.handlers = [handler]
    app_logger.setLevel(logging.INFO)


def main(argv: list[str] | None = None) -> int:
    _configure_console()
    parser = argparse.ArgumentParser(
        prog="python -m app.extract",
        description="Extract the MVP lab tests from a lab report PDF with local Gemma, "
        "print them as JSON and save them to the local database.",
    )
    parser.add_argument("pdf", type=Path, help="path to the lab report PDF")
    parser.add_argument(
        "--model",
        default=settings.model,
        help=f"Ollama model (default: {settings.model}; faster fallback: gemma4:e2b)",
    )
    parser.add_argument("--force", action="store_true", help="re-extract a report that is already saved")
    args = parser.parse_args(argv)
    try:
        output = run(args.pdf, model=args.model, force=args.force)
    except ExtractError as error:
        logger.error("%s", error)
        return 1
    except KeyboardInterrupt:
        logger.error("Cancelled. Nothing was saved.")
        return 130
    if output is not None:
        print(output.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
