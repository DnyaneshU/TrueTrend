"""Merge Gemma's answers for each page into one report.

Page numbers come from code, report details are taken from the first page that
prints them, and rows that can't be trusted are dropped with a warning. Every row
is still `needs_check` until verification exists.
"""

import logging
import re
import time
from collections.abc import Callable
from dataclasses import replace

from pydantic import ValidationError

from app.dates import parse_date
from app.errors import ExtractError
from app.gemma import PageExtraction
from app.lab_tests import CATALOG, name_conflict
from app.models import Extraction, Header, PageReply, PageSummary, Result
from app.pages import PageInput

logger = logging.getLogger(__name__)

Ask = Callable[[PageInput, bool], PageExtraction]  # (page, retry) -> reply
Transcribe = Callable[[PageInput], str]  # scanned page -> its text

DATE_FIELDS = ("sample_date", "report_date")
# Not worth a warning when pages differ: labs stamp each section with its own report
# time, and print the lab as brand, centre or reference lab on different pages.
QUIET_FIELDS = ("report_date", "lab_name")

_FLAG_WORDS = "|".join(map(re.escape, sorted(CATALOG.report.flags, key=len, reverse=True)))
_FLAG_FIRST = re.compile(rf"^({_FLAG_WORDS})\s+(.*\d.*)$")  # "H 168.0"
_FLAG_LAST = re.compile(rf"^(.*\d.*?)\s+({_FLAG_WORDS})$")  # "168.0 H"


def extract_pages(pages: list[PageInput], ask: Ask, transcribe: Transcribe) -> Extraction:
    """Ask Gemma about each page and merge the answers into one report.

    A scanned page is transcribed once first; a retry reuses the transcription.
    """
    out = Extraction()
    header_page: dict[str, int] = {}  # which page each header value came from
    for page in pages:
        started = time.perf_counter()
        if page.mode == "vision":
            page = replace(page, text=transcribe(page))
        reply = _ask_with_retry(ask, page)
        seconds = round(time.perf_counter() - started, 1)
        out.replies.append(
            PageReply(
                page=page.number,
                mode=page.mode,
                transcription=page.text if page.mode == "vision" else None,
                reply=reply,
            )
        )
        if reply is None:
            out.pages.append(PageSummary(page=page.number, mode="failed", results=0, seconds=seconds))
            out.warnings.append(f"page {page.number}: Gemma's answer was unreadable twice; page skipped")
            logger.info("page %d/%d · %s · FAILED · %s s", page.number, page.total, page.mode, seconds)
            continue
        out.warnings += _merge_header(out.header, header_page, reply, page.number)
        rows, row_warnings = _clean_rows(reply, page.number)
        out.results += rows
        out.warnings += row_warnings
        out.pages.append(PageSummary(page=page.number, mode=page.mode, results=len(rows), seconds=seconds))
        logger.info(
            "page %d/%d · %s · %s · %s s",
            page.number,
            page.total,
            page.mode,
            plural(len(rows), "result"),
            seconds,
        )

    if all(summary.mode == "failed" for summary in out.pages):
        raise ExtractError(
            "Gemma's answer was unreadable for every page, so nothing was saved. "
            "Try again, or try --model gemma4:e2b."
        )
    out.warnings += _repeat_warnings(out.results)
    return out


def plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _ask_with_retry(ask: Ask, page: PageInput) -> PageExtraction | None:
    for retry in (False, True):
        try:
            return ask(page, retry)
        except ValidationError:
            continue
    return None


def _merge_header(
    header: Header, header_page: dict[str, int], reply: PageExtraction, page_number: int
) -> list[str]:
    """Fill header fields this page prints for the first time; warn where it disagrees."""
    warnings = []
    for name in Header.model_fields:
        value = _clean(getattr(reply, name))
        if value is None:
            continue
        current = getattr(header, name)
        if current is None:
            setattr(header, name, value)
            header_page[name] = page_number
        elif name not in QUIET_FIELDS and not _same(name, current, value):
            first = header_page[name]
            warnings.append(
                f"page {page_number}: {name} '{value}' differs from page {first} ('{current}'); "
                f"kept page {first}"
            )
    return warnings


def _clean_rows(reply: PageExtraction, page_number: int) -> tuple[list[Result], list[str]]:
    """The page's results, tidied, and warnings for the rows dropped as untrustworthy."""
    rows: list[Result] = []
    warnings: list[str] = []
    seen: set[tuple] = set()
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
        flag, value_text = _split_flag(value_text)
        rows.append(
            Result(
                page=page_number,
                test_code=row.test_code,
                raw_name=raw_name,
                value_text=value_text,
                flag=flag,
                unit=unit,
                ref_text=_clean(row.ref_text),
            )
        )
    return rows, warnings


def _repeat_warnings(results: list[Result]) -> list[str]:
    """A test found more than once is kept each time; say so."""
    pages_by_code: dict[str, list[int]] = {}
    for result in results:
        pages_by_code.setdefault(result.test_code, []).append(result.page)
    return [
        f"{code} appears {len(on_pages)} times (pages {', '.join(map(str, on_pages))}); all kept"
        for code, on_pages in pages_by_code.items()
        if len(on_pages) > 1
    ]


def _clean(text: str | None) -> str | None:
    """Collapse whitespace and drop the " | " column markers page_text added; empty becomes None."""
    if text is None:
        return None
    cleaned = " ".join(text.replace(" | ", " ").split())
    return None if not cleaned or cleaned.casefold() in CATALOG.report.empty_words else cleaned


def _split_flag(value_text: str) -> tuple[str | None, str]:
    """('H', '168.0') from 'H 168.0' or '168.0 H'; (None, value) when no high/low mark is printed."""
    if match := _FLAG_FIRST.match(value_text):
        return match[1], match[2]
    if match := _FLAG_LAST.match(value_text):
        return match[2], match[1]
    return None, value_text


def _same(field_name: str, a: str, b: str) -> bool:
    """Equal ignoring case; the same day however it is written; the same age in years."""
    if field_name in DATE_FIELDS and (day := parse_date(a)) is not None:
        return day == parse_date(b)
    if field_name == "age" and (years := re.findall(r"\d+", a)):
        return years[:1] == re.findall(r"\d+", b)[:1]
    return a.casefold() == b.casefold()
