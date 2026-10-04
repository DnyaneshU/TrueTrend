"""Text helpers shared by every step: how numbers, qualifiers, flags and names are printed."""

import re
import unicodedata
from typing import Literal

from arogya_vahi.lab_tests import CATALOG

# A printed number: "7.2", "1,234", "1,23,456", ".5". \d is any script's digits, so
# Devanagari "७.२" matches too, and float() reads it.
NUMBER = r"\d+(?:,\d+)*(?:\.\d+)?|\.\d+"
QUALIFIER = r"<=|>=|≤|≥|<|>"  # longest first
Qualifier = Literal["<", ">", "<=", ">="]
QUALIFIERS: dict[str, Qualifier] = {"<": "<", ">": ">", "<=": "<=", ">=": ">=", "≤": "<=", "≥": ">="}

# A word that is one value: "7.2", "<148", "7.2%", "13.6*"; not a range, nor a name like "B12".
_VALUE_WORD = re.compile(rf"^(?P<qualifier>{QUALIFIER})?(?P<number>{NUMBER})[%*]?$")

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


def printed_number(word: str) -> tuple[Qualifier | None, float] | None:
    """(qualifier, number) for a printed word that is one value: "<148" -> ('<', 148.0); else None."""
    match = _VALUE_WORD.match(word)
    return (QUALIFIERS.get(match["qualifier"]), to_float(match["number"])) if match else None


def words(text: str) -> list[str]:
    """A printed name's words: Unicode-normalised, casefolded, without punctuation.

    Letters, Devanagari vowel signs and digits are kept; invisible joiners are dropped, so
    one spelling written two ways is one word.
    """
    normal = unicodedata.normalize("NFKC", text).casefold()
    kept = "".join(
        char if unicodedata.category(char)[0] in "LMN" else "" if unicodedata.category(char) == "Cf" else " "
        for char in normal
    )
    return kept.split()


def same_name(a: str | None, b: str | None) -> bool:
    """Two printed names of a lab are the same, ignoring case, spacing and punctuation.

    An unknown name is never the same as another. People's names: arogya_vahi.people.
    """
    return bool(a and b) and words(a) == words(b)


def to_float(number: str) -> float:
    """A NUMBER as a float: '1,234' and Indian '1,23,456' group thousands; '7,2' has a decimal comma."""
    if re.fullmatch(r"\d+,\d{1,2}", number):
        return float(number.replace(",", "."))
    return float(number.replace(",", ""))


def plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"
