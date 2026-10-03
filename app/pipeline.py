"""Merge Gemma's answers for each page into one report.

Page numbers come from code, report details are taken from the first page that
prints them, and rows that can't be trusted are dropped with a warning. Every row
is still `needs_check` until verification exists.
"""

import logging
import re
import time
from collections.abc import Callable

from pydantic import ValidationError

from app.dates import parse_date
from app.errors import ExtractError
from app.lab_tests import CATALOG
from app.models import Extraction, Header, PageExtraction, PageInput, PageReply, PageSummary, Result
from app.text import clean_text, plural, split_flag

logger = logging.getLogger(__name__)

ExtractResults = Callable[[PageInput, bool], PageExtraction]  # (page, retry) -> Gemma's reply
Transcribe = Callable[[PageInput], str]  # scanned page -> its text

DATE_FIELDS = ("sample_date", "report_date")
# Not worth a warning when pages differ: labs stamp each section with its own report
# time, and print the lab as brand, centre or reference lab on different pages.
QUIET_FIELDS = ("report_date", "lab_name")


def extract_pages(pages: list[PageInput], extract: ExtractResults, transcribe: Transcribe) -> Extraction:
    """Ask Gemma about each page and merge the answers into one report.

    A scanned page is transcribed once first; a retry reuses the transcription.
    Raises ExtractError when no page could be read.
    """
    out = Extraction()
    header_page: dict[str, int] = {}  # which page each header value came from
    for page in pages:
        started = time.perf_counter()
        if page.mode == "vision":
            page = page.model_copy(update={"text": transcribe(page)})
        reply = _extract_with_retry(extract, page)
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


def _extract_with_retry(extract: ExtractResults, page: PageInput) -> PageExtraction | None:
    for retry in (False, True):
        try:
            return extract(page, retry)
        except ValidationError:
            continue
    return None


def _merge_header(
    header: Header, header_page: dict[str, int], reply: PageExtraction, page_number: int
) -> list[str]:
    """Fill header fields this page prints for the first time; warn where it disagrees."""
    warnings = []
    for name in Header.model_fields:
        value = clean_text(getattr(reply, name))
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
    seen: set[tuple[str, str, str | None]] = set()
    for row in reply.results:
        raw_name, value_text, unit = (
            clean_text(row.raw_name),
            clean_text(row.value_text),
            clean_text(row.unit),
        )
        if value_text is None:
            problem = f"{row.test_code} had no value"
        elif raw_name is None:
            problem = f"{row.test_code} had no test name"
        else:
            problem = CATALOG.test(row.test_code).conflict(raw_name)
        if problem:
            warnings.append(f"page {page_number}: {problem}; row dropped")
            continue
        if (row.test_code, value_text, unit) in seen:
            warnings.append(f"page {page_number}: {row.test_code} {value_text} was listed twice; kept once")
            continue
        seen.add((row.test_code, value_text, unit))
        flag, value_text = split_flag(value_text)
        rows.append(
            Result(
                page=page_number,
                test_code=row.test_code,
                raw_name=raw_name,
                value_text=value_text,
                flag=flag,
                unit=unit,
                ref_text=clean_text(row.ref_text),
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


def _same(field_name: str, a: str, b: str) -> bool:
    """Equal ignoring case; the same day however it is written; the same age in years."""
    if field_name in DATE_FIELDS and (day := parse_date(a)) is not None:
        return day == parse_date(b)
    if field_name == "age" and (years := re.findall(r"\d+", a)):
        return years[:1] == re.findall(r"\d+", b)[:1]
    return a.casefold() == b.casefold()
