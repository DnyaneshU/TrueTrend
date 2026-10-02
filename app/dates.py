"""Read the dates printed on lab reports."""
import re
import warnings
from datetime import date, datetime

from dateutil import parser as dateparser

_ISO_DATE = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
_DEFAULT_A, _DEFAULT_B = datetime(2000, 1, 1), datetime(2001, 2, 2)
# a two-digit year closing a day-month-year group: "12/09/26", "02-Oct-26"
_SHORT_YEAR = re.compile(r"\b\d{1,2}[\s/.,-]+(?:\d{1,2}|[A-Za-z]{3,9})[\s/.,-]+(\d{2})\b")


def parse_date(text: str | None) -> str | None:
    """A printed date as ISO YYYY-MM-DD, read day-first (Indian DD/MM/YYYY).

    Returns None for missing, partial ("Sep 2026") or unreadable text, including a
    blanked-out year ("02 Dec, 2X", which dateutil would read as 2002). Parsing
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
        with warnings.catch_warnings():  # e.g. "2X" read as an unknown timezone
            warnings.simplefilter("ignore")
            first = dateparser.parse(text, dayfirst=True, fuzzy=True, default=_DEFAULT_A)
            second = dateparser.parse(text, dayfirst=True, fuzzy=True, default=_DEFAULT_B)
    except (ValueError, OverflowError):
        return None
    if first.date() != second.date():  # day, month or year was missing and came from the default
        return None
    if not _year_is_printed(text, first.year):
        return None
    return first.date().isoformat()


def _year_is_printed(text: str, year: int) -> bool:
    """The year really is in the text, not something dateutil assembled from other digits."""
    if str(year) in re.findall(r"\d{4}", text):
        return True
    return any(short == f"{year % 100:02d}" for short in _SHORT_YEAR.findall(text))
