"""Small text helpers shared by the extraction and normalisation steps."""

import re

from app.lab_tests import CATALOG

# The lab's high/low mark printed in the value's cell, before or after it: "H 168.0", "7.2 L".
_FLAG_WORDS = "|".join(map(re.escape, sorted(CATALOG.report.flags, key=len, reverse=True)))
_FLAG_FIRST = re.compile(rf"^({_FLAG_WORDS})\s+(.*\d.*)$")
_FLAG_LAST = re.compile(rf"^(.*\d.*?)\s+({_FLAG_WORDS})$")


def clean_text(text: str | None) -> str | None:
    """Collapse whitespace and drop the " | " column markers page_text added.

    Empty text, and the words Gemma writes for "not printed" ("null", "n/a", ...), become None.
    """
    if text is None:
        return None
    cleaned = " ".join(text.replace(" | ", " ").split())
    return None if not cleaned or cleaned.casefold() in CATALOG.report.empty_words else cleaned


def split_flag(value_text: str) -> tuple[str | None, str]:
    """('H', '168.0') from 'H 168.0' or '168.0 H'; (None, value) when no high/low mark is printed."""
    if match := _FLAG_FIRST.match(value_text):
        return match[1], match[2]
    if match := _FLAG_LAST.match(value_text):
        return match[2], match[1]
    return None, value_text


def plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"
