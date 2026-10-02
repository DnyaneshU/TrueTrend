"""Extract lab test results from a lab report PDF with local Gemma.

    python -m app.extract path/to/report.pdf [--force] [--model gemma4:e2b]

One Gemma call per page (app.pages, app.gemma). This module merges the pages'
answers into one report: page numbers come from code, report details are taken
from the first page that prints them, and rows that can't be trusted are dropped
with a warning. Every saved row is `needs_check` until verify.py exists.
"""
import argparse
import hashlib
import json
import shutil
import sys
import time
from contextlib import closing
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path
from typing import Callable

from pydantic import ValidationError

from app import db
from app.dates import parse_date
from app.errors import ExtractError
from app.gemma import DEFAULT_MODEL, PageExtraction, ask_gemma, transcribe
from app.lab_tests import name_conflict
from app.pages import PageInput, open_pdf, read_pages

HEADER_FIELDS = ("patient_name", "age", "sex", "lab_name", "sample_date", "report_date")
DATE_FIELDS = ("sample_date", "report_date")

Ask = Callable[[PageInput, bool], PageExtraction]  # (page, retry) -> reply
Transcribe = Callable[[PageInput], str]            # scanned page -> its text


@dataclass
class Extraction:
    header: dict[str, str | None]
    results: list[dict] = field(default_factory=list)
    pages: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    replies: list[dict] = field(default_factory=list)   # stored as reports.raw_json


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def extract_pages(pages: list[PageInput], ask: Ask, transcribe: Transcribe) -> Extraction:
    """Ask Gemma about each page and merge the answers into one report.

    A scanned page is transcribed once first; a retry reuses the transcription.
    """
    out = Extraction(header=dict.fromkeys(HEADER_FIELDS))
    header_page: dict[str, int] = {}   # which page each header value came from
    for page in pages:
        started = time.perf_counter()
        if page.mode == "vision":
            page = replace(page, text=transcribe(page))
        reply = _ask_with_retry(ask, page)
        seconds = round(time.perf_counter() - started, 1)
        out.replies.append({"page": page.number, "mode": page.mode,
                            "transcription": page.text if page.mode == "vision" else None,
                            "reply": reply.model_dump() if reply else None})
        if reply is None:
            out.pages.append({"page": page.number, "mode": "failed", "results": 0, "seconds": seconds})
            out.warnings.append(f"page {page.number}: Gemma's answer was unreadable twice; page skipped")
            log(f"page {page.number}/{page.total} · {page.mode} · FAILED · {seconds} s")
            continue
        out.warnings += _merge_header(out.header, header_page, reply, page.number)
        rows, row_warnings = _clean_rows(reply, page.number)
        out.results += rows
        out.warnings += row_warnings
        out.pages.append({"page": page.number, "mode": page.mode, "results": len(rows), "seconds": seconds})
        log(f"page {page.number}/{page.total} · {page.mode} · {_count(len(rows), 'result')} · {seconds} s")

    if all(p["mode"] == "failed" for p in out.pages):
        raise ExtractError(
            "Gemma's answer was unreadable for every page, so nothing was saved. "
            "Try again, or try --model gemma4:e2b."
        )
    out.warnings += _repeat_warnings(out.results)
    return out


def _ask_with_retry(ask: Ask, page: PageInput) -> PageExtraction | None:
    for retry in (False, True):
        try:
            return ask(page, retry)
        except ValidationError:
            continue
    return None


def _merge_header(header: dict, header_page: dict, reply: PageExtraction, page_number: int) -> list[str]:
    """Fill header fields this page prints for the first time; warn where it disagrees."""
    warnings = []
    for name in HEADER_FIELDS:
        value = _clean(getattr(reply, name))
        if value is None:
            continue
        if header[name] is None:
            header[name], header_page[name] = value, page_number
        elif not _same(name, header[name], value):
            first = header_page[name]
            warnings.append(f"page {page_number}: {name} '{value}' differs from page {first} "
                            f"('{header[name]}'); kept page {first}")
    return warnings


def _clean_rows(reply: PageExtraction, page_number: int) -> tuple[list[dict], list[str]]:
    """The page's results, tidied, and warnings for the rows dropped as untrustworthy."""
    rows, warnings, seen = [], [], set()
    for row in reply.results:
        raw_name, value_text, unit = _clean(row.raw_name), _clean(row.value_text), _clean(row.unit)
        if value_text is None:
            problem = f"{row.test_code} had no value"
        elif raw_name is None:
            problem = f"{row.test_code} had no test name"
        else:
            problem = name_conflict(row.test_code, raw_name)
        if problem:
            warnings.append(f"page {page_number}: {problem}; row dropped")
            continue
        if (row.test_code, value_text, unit) in seen:
            warnings.append(f"page {page_number}: {row.test_code} {value_text} was listed twice; kept once")
            continue
        seen.add((row.test_code, value_text, unit))
        rows.append({
            "page": page_number,
            "test_code": row.test_code,
            "raw_name": raw_name,
            "value_text": value_text,
            "unit": unit,
            "ref_text": _clean(row.ref_text),
        })
    return rows, warnings


def _repeat_warnings(results: list[dict]) -> list[str]:
    """A test found more than once is kept each time; say so."""
    pages_by_code: dict[str, list[int]] = {}
    for result in results:
        pages_by_code.setdefault(result["test_code"], []).append(result["page"])
    return [f"{code} appears {len(on_pages)} times (pages {', '.join(map(str, on_pages))}); all kept"
            for code, on_pages in pages_by_code.items() if len(on_pages) > 1]


def _clean(text: str | None) -> str | None:
    """Collapse whitespace; empty text becomes None."""
    if text is None:
        return None
    return " ".join(text.split()) or None


def _same(field_name: str, a: str, b: str) -> bool:
    """Equal ignoring case, or (for dates) the same day however it is written."""
    if field_name in DATE_FIELDS and (day := parse_date(a)) is not None:
        return day == parse_date(b)
    return a.casefold() == b.casefold()


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


# ---------------------------------------------------------------- command line

def run(pdf_path: Path, *, model: str = DEFAULT_MODEL, force: bool = False,
        db_path: Path | None = None, originals_dir: Path | None = None) -> dict | None:
    """Extract one report PDF, save it, and return the output JSON as a dict.

    Returns None, saving nothing, when the same file was already extracted and
    force is False. Raises ExtractError for problems the user can fix. Nothing is
    written to the database until every page has been read.
    """
    with open_pdf(pdf_path) as doc, closing(db.connect(db_path)) as conn:
        sha256 = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
        existing = db.find_report_id(conn, sha256)
        if existing is not None and not force:
            log(f"{pdf_path.name} was already extracted as report #{existing}. Use --force to redo it.")
            return None

        pages = read_pages(doc)
        log(f"Reading {pdf_path.name}: {_count(len(pages), 'page')} with {model}. "
            "The first page also loads the model, so it takes longer.")
        started = time.perf_counter()
        extraction = extract_pages(pages, lambda page, retry: ask_gemma(page, model, retry),
                                   lambda page: transcribe(page, model))
        seconds = round(time.perf_counter() - started, 1)

        dates = {name: parse_date(extraction.header[name]) for name in DATE_FIELDS}
        warnings = extraction.warnings + _report_warnings(extraction, dates)
        stored = _store_original(pdf_path, sha256, originals_dir or db.ORIGINALS_DIR)
        report = {
            "lab_name": extraction.header["lab_name"],
            "sample_date": dates["sample_date"],
            "report_date": dates["report_date"],
            "source": "upload",
            "file_path": _repo_relative(stored),
            "sha256": sha256,
            "is_scanned": int(any(page.mode == "vision" for page in pages)),
            "patient_name_raw": extraction.header["patient_name"],
            "patient_age_raw": extraction.header["age"],
            "patient_sex_raw": extraction.header["sex"],
            "extract_model": model,
            "extract_seconds": seconds,
            "raw_json": json.dumps({"pages": extraction.replies}, ensure_ascii=False),
        }
        rows = [{
            "test_code": r["test_code"], "raw_name": r["raw_name"], "raw_value_text": r["value_text"],
            "unit": r["unit"], "ref_text": r["ref_text"], "page": r["page"],
            "status": "needs_check", "check_notes": "not verified yet",
        } for r in extraction.results]
        report_id = db.save_report(conn, report, rows, replace=force)

    for warning in warnings:
        log(f"warning: {warning}")
    log(f"Saved report #{report_id}: {_count(len(rows), 'result')}, {seconds} s.")
    header = extraction.header
    return {
        "report_id": report_id,
        "file": str(pdf_path),
        "sha256": sha256,
        "model": model,
        "seconds": seconds,
        "patient_name": header["patient_name"],
        "age": header["age"],
        "sex": header["sex"],
        "lab_name": header["lab_name"],
        "sample_date": dates["sample_date"],
        "sample_date_text": header["sample_date"],
        "report_date": dates["report_date"],
        "report_date_text": header["report_date"],
        "pages": extraction.pages,
        "warnings": warnings,
        "results": [{**result, "status": "needs_check"} for result in extraction.results],
    }


def _report_warnings(extraction: Extraction, dates: dict[str, str | None]) -> list[str]:
    """Problems with the report as a whole: its dates, or nothing found at all."""
    printed = extraction.header
    warnings = []
    if printed["sample_date"] is None:
        warnings.append("No sample collection date found; the report can't go on the timeline until one is added.")
    elif dates["sample_date"] is None:
        warnings.append(f"Sample date '{printed['sample_date']}' could not be read as a date.")
    elif dates["sample_date"] > date.today().isoformat():
        warnings.append(f"Sample date {dates['sample_date']} is in the future; check the report.")
    if printed["report_date"] is not None and dates["report_date"] is None:
        warnings.append(f"Report date '{printed['report_date']}' could not be read as a date.")
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
        return path.resolve().relative_to(db.ROOT).as_posix()
    except ValueError:
        return str(path.resolve())


def _utf8_console() -> None:
    """Patient names and units (µ, Devanagari) must print even on a Windows code page."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


def main(argv: list[str] | None = None) -> int:
    _utf8_console()
    parser = argparse.ArgumentParser(
        prog="python -m app.extract",
        description="Extract the MVP lab tests from a lab report PDF with local Gemma, "
                    "print them as JSON and save them to storage/arogya.db.",
    )
    parser.add_argument("pdf", type=Path, help="path to the lab report PDF")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help=f"Ollama model (default: {DEFAULT_MODEL}; faster fallback: gemma4:e2b)")
    parser.add_argument("--force", action="store_true",
                        help="re-extract a report that is already saved, replacing its rows")
    args = parser.parse_args(argv)
    try:
        output = run(args.pdf, model=args.model, force=args.force)
    except ExtractError as error:
        log(f"error: {error}")
        return 1
    except KeyboardInterrupt:
        log("Cancelled. Nothing was saved.")
        return 130
    if output is not None:
        print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
