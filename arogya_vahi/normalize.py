"""Turn printed results into numbers in each test's standard unit.

Nothing is guessed: a value that is not one number, a unit the catalog does not list,
or a range that is not one normal range is left empty with a note saying why.
Units and conversions come from arogya_vahi/data/lab_tests.toml.
"""

import re

from arogya_vahi.lab_tests import CATALOG, LabTest
from arogya_vahi.models import Normalized, Result
from arogya_vahi.text import NUMBER, QUALIFIER, QUALIFIERS, Qualifier, to_float

# one number, optionally after <, >, <=, >= and before non-numeric text such as a unit
_VALUE = re.compile(rf"^\s*(?P<qualifier>{QUALIFIER})?\s*(?P<number>{NUMBER})\D*$")

SIGNIFICANT_DIGITS = 6  # enough for any printed lab value; removes floating-point noise

_BETWEEN = re.compile(rf"^(?P<low>{NUMBER})\s*(?:-|–|—|to)\s*(?P<high>{NUMBER})\D*$", re.I)
_BELOW = re.compile(rf"^(?:<=?|≤|up\s*to|less\s+than|below)\s*(?P<high>{NUMBER})\D*$", re.I)
_ABOVE = re.compile(rf"^(?:>=?|≥|more\s+than|greater\s+than|above)\s*(?P<low>{NUMBER})\D*$", re.I)
_LABEL = re.compile(r"^(?P<label>[^\W\d][^\W\d .]*(?: [^\W\d][^\W\d.]*)*)\s*[:\-–]\s*(?P<rest>.+)$")


def parse_value(text: str | None) -> tuple[Qualifier | None, float] | None:
    """('<', 148.0) from '< 148'; (None, 7.2) from '7.2 %'; None unless the text is one number."""
    match = _VALUE.match(text or "")
    if not match:
        return None
    return QUALIFIERS.get(match["qualifier"]), to_float(match["number"])


def parse_range(text: str | None) -> tuple[float | None, float | None] | None:
    """(74.0, 106.0) from '74 - 106'; (None, 200.0) from 'Normal : <200'.

    None unless the text is one normal range: a label other than a normal-range label
    ("Low: <40", "Deficiency: <10") marks a risk category, and a list of categories is
    not one range, nor is a low limit above the high one.
    """
    text = (text or "").strip()
    if label := _LABEL.match(text):
        if label["label"].casefold() not in CATALOG.report.normal_range_labels:
            return None
        text = label["rest"].strip()
    if match := _BETWEEN.match(text):
        low, high = to_float(match["low"]), to_float(match["high"])
        return (low, high) if low <= high else None
    if match := _BELOW.match(text):
        return None, to_float(match["high"])
    if match := _ABOVE.match(text):
        return to_float(match["low"]), None
    return None


def normalize(result: Result, test: LabTest) -> Normalized:
    """The result's value and normal range as numbers, converted to the test's standard unit."""
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
