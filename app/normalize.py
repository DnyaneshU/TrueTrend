"""Turn printed results into numbers in each test's standard unit.

Nothing is guessed: a value that is not one number, a unit the catalog does not list,
or a range that is not one normal range is left empty with a note saying why.
Units and conversions come from data/lab_tests.toml.
"""

import re

from app.lab_tests import CATALOG
from app.models import Normalized, Result
from app.text import NUMBER, QUALIFIER, to_float

_QUALIFIER = {"<": "<", ">": ">", "<=": "<=", ">=": ">=", "≤": "<=", "≥": ">="}
# one number, optionally after <, >, <=, >= and before non-numeric text such as a unit
_VALUE = re.compile(rf"^\s*(?P<qualifier>{QUALIFIER})?\s*(?P<number>{NUMBER})\D*$")

SIGNIFICANT_DIGITS = 6  # enough for any printed lab value; removes floating-point noise

_RANGE_NUMBER = r"\d+(?:\.\d+)?"
_BETWEEN = re.compile(rf"^(?P<low>{_RANGE_NUMBER})\s*(?:-|–|—|to)\s*(?P<high>{_RANGE_NUMBER})\D*$", re.I)
_BELOW = re.compile(rf"^(?:<=?|≤|up\s*to|less\s+than|below)\s*(?P<high>{_RANGE_NUMBER})\D*$", re.I)
_ABOVE = re.compile(rf"^(?:>=?|≥|more\s+than|greater\s+than|above)\s*(?P<low>{_RANGE_NUMBER})\D*$", re.I)
_LABEL = re.compile(r"^(?P<label>[^\W\d][^\W\d .]*(?: [^\W\d][^\W\d.]*)*)\s*[:\-–]\s*(?P<rest>.+)$")


def parse_value(text: str | None) -> tuple[str | None, float] | None:
    """('<', 148.0) from '< 148'; (None, 7.2) from '7.2 %'; None unless the text is one number."""
    match = _VALUE.match(text or "")
    if not match:
        return None
    return _QUALIFIER.get(match["qualifier"]), to_float(match["number"])


def parse_range(text: str | None) -> tuple[float | None, float | None] | None:
    """(74.0, 106.0) from '74 - 106'; (None, 200.0) from 'Normal : <200'.

    None unless the text is one normal range: a label other than a normal-range label
    ("Low: <40", "Deficiency: <10") marks a risk category, and a list of categories is
    not one range.
    """
    text = (text or "").strip()
    if label := _LABEL.match(text):
        if label["label"].casefold() not in CATALOG.report.normal_range_labels:
            return None
        text = label["rest"].strip()
    if match := _BETWEEN.match(text):
        return float(match["low"]), float(match["high"])
    if match := _BELOW.match(text):
        return None, float(match["high"])
    if match := _ABOVE.match(text):
        return float(match["low"]), None
    return None


def normalize(result: Result) -> Normalized:
    """The result's value and normal range as numbers, converted to the test's standard unit."""
    test = CATALOG.test(result.test_code)
    notes = []
    parsed = parse_value(result.value_text)
    if parsed is None:
        notes.append(f"value {result.value_text!r} is not a number")
    qualifier, value = parsed or (None, None)

    conversion = test.conversion(result.unit)
    if result.unit is None:
        notes.append("no unit printed")
    elif conversion is None:
        notes.append(f"unit {result.unit!r} is not a known unit for {test.code}")

    def standard(number: float | None) -> float | None:
        if number is None or conversion is None:
            return None
        return float(f"{conversion.apply(number):.{SIGNIFICANT_DIGITS}g}")

    low, high = parse_range(result.ref_text) or (None, None)
    return Normalized(
        value=value,
        qualifier=qualifier,
        value_std=standard(value),
        unit_std=test.unit if conversion else None,
        ref_low=standard(low),
        ref_high=standard(high),
        notes=notes,
    )
