"""Read the dates printed on lab reports."""
import re
from datetime import date, datetime

from dateutil import parser as dateparser

_ISO_DATE = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
_DEFAULT_A, _DEFAULT_B = datetime(2000, 1, 1), datetime(2001, 2, 2)


def parse_date(text: str | None) -> str | None:
    """A printed date as ISO YYYY-MM-DD, read day-first (Indian DD/MM/YYYY).

    Returns None for missing, partial ("Sep 2026") or unreadable text. Parsing
    twice with different defaults catches parts dateutil would silently fill in.
    """
    if not text or not text.strip():
        return None
    iso = _ISO_DATE.search(text)
    if iso:
        try:
            return date(*map(int, iso.groups())).isoformat()
        except ValueError:
            return None
    try:
        first = dateparser.parse(text, dayfirst=True, fuzzy=True, default=_DEFAULT_A)
        second = dateparser.parse(text, dayfirst=True, fuzzy=True, default=_DEFAULT_B)
    except (ValueError, OverflowError):
        return None
    if first.date() != second.date():  # day, month or year was missing and came from the default
        return None
    return first.date().isoformat()
