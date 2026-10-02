"""Extract lab test results from a lab report PDF with local Gemma.

    python -m app.extract path/to/report.pdf [--force] [--model gemma4:e2b]

One Gemma call per page (app.pages, app.gemma). This module merges the pages'
answers into one report: page numbers come from code, report details are taken
from the first page that prints them, and rows that can't be trusted are dropped
with a warning. Every saved row is `needs_check` until verify.py exists.
"""
import sys
import time
from dataclasses import dataclass, field
from typing import Callable

from pydantic import ValidationError

from app.dates import parse_date
from app.errors import ExtractError
from app.gemma import PageExtraction
from app.lab_tests import name_conflict
from app.pages import PageInput

HEADER_FIELDS = ("patient_name", "age", "sex", "lab_name", "sample_date", "report_date")
DATE_FIELDS = ("sample_date", "report_date")

Ask = Callable[[PageInput, bool], PageExtraction]  # (page, retry) -> reply


@dataclass
class Extraction:
    header: dict[str, str | None]
    results: list[dict] = field(default_factory=list)
    pages: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    replies: list[dict] = field(default_factory=list)   # stored as reports.raw_json


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def extract_pages(pages: list[PageInput], ask: Ask) -> Extraction:
    """Ask Gemma about each page and merge the answers into one report."""
    out = Extraction(header=dict.fromkeys(HEADER_FIELDS))
    header_page: dict[str, int] = {}   # which page each header value came from
    for page in pages:
        started = time.perf_counter()
        reply = _ask_with_retry(ask, page)
        seconds = round(time.perf_counter() - started, 1)
        out.replies.append({"page": page.number, "mode": page.mode, "reply": reply and reply.model_dump()})
        if reply is None:
            out.pages.append({"page": page.number, "mode": "failed", "results": 0, "seconds": seconds})
            out.warnings.append(f"page {page.number}: Gemma's answer was unreadable twice; page skipped")
            log(f"page {page.number}/{page.total} · {page.mode} · FAILED · {seconds} s")
            continue
        out.warnings += _merge_header(out.header, header_page, reply, page.number)
        rows = _clean_rows(reply, page.number, out.warnings)
        out.results += rows
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


def _clean_rows(reply: PageExtraction, page_number: int, warnings: list[str]) -> list[dict]:
    """The page's results, tidied; rows that can't be trusted are dropped with a warning."""
    rows, seen = [], set()
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
    return rows


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
