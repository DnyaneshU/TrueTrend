"""Turn printed results into numbers in each test's standard unit.

    python -m app.normalize        # re-normalise every saved result, e.g. after editing units

Nothing is guessed: a value that is not one number, a unit the catalog does not list,
or a range that is not one normal range is left empty with a note saying why.
Units and conversions come from data/lab_tests.toml.
"""

import logging
import re
import sqlite3
import sys
from contextlib import closing

from app import db
from app.console import configure_console
from app.lab_tests import CATALOG
from app.models import Normalized, Result
from app.text import split_flag

logger = logging.getLogger(__name__)

_NUMBER = r"\d[\d,]*(?:\.\d+)?|\.\d+"
_QUALIFIER = {"<": "<", ">": ">", "<=": "<=", ">=": ">=", "≤": "<=", "≥": ">="}
# one number, optionally after <, >, <=, >= and before non-numeric text such as a unit
_VALUE = re.compile(rf"^\s*(?P<qualifier><=|>=|≤|≥|<|>)?\s*(?P<number>{_NUMBER})\D*$")

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
    return _QUALIFIER.get(match["qualifier"]), _to_float(match["number"])


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


def renormalize(conn: sqlite3.Connection) -> int:
    """Recompute the normalised columns of every saved result; returns how many were updated."""
    rows = conn.execute(
        "SELECT id, page, test_code, raw_name, raw_value_text, unit, ref_text, flag FROM results"
    ).fetchall()
    with conn:
        for row in rows:
            flag, value_text = split_flag(row["raw_value_text"])  # rows saved before flags were split out
            result = Result(
                page=row["page"],
                test_code=row["test_code"],
                raw_name=row["raw_name"],
                value_text=value_text,
                unit=row["unit"],
                ref_text=row["ref_text"],
                flag=row["flag"] or flag,
            )
            conn.execute(
                "UPDATE results SET raw_value_text = :value_text, flag = :flag, value = :value, "
                "qualifier = :qualifier, value_std = :value_std, unit_std = :unit_std, "
                "ref_low = :ref_low, ref_high = :ref_high WHERE id = :id",
                {
                    **normalize(result).model_dump(exclude={"notes"}),
                    "value_text": result.value_text,
                    "flag": result.flag,
                    "id": row["id"],
                },
            )
    return len(rows)


def _to_float(number: str) -> float:
    """'1,234' and Indian '1,23,456' are thousands separators; '7,2' is a decimal comma."""
    if re.fullmatch(r"\d+,\d{1,2}", number):
        return float(number.replace(",", "."))
    return float(number.replace(",", ""))


def main() -> int:
    configure_console()
    with closing(db.connect()) as conn:
        logger.info("Re-normalised %d saved results.", renormalize(conn))
    return 0


if __name__ == "__main__":
    sys.exit(main())
