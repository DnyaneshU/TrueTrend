"""Check each result against the report it came from, in plain Python.

A result is `verified` only when all of these hold; otherwise it stays `needs_check`
with notes saying why:

- Its value is printed on its page as a cell of its own, in a row whose label names
  its test (by the catalog's names and look-alikes). The PDF's own text is used, never
  Gemma's reading of it, so a value on a scanned page always needs a check.
- Normalisation turned it into a number in a known unit, within the test's believable
  limits (data/lab_tests.toml).
- It is not more than a total it is part of (LDL and HDL are parts of total cholesterol).

Two cross-checks only add a note and never change the status: the lab's LDL against
the Friedewald estimate, and HbA1c's average glucose (ADAG) against fasting glucose.
"""

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, field

import pymupdf

from app.config import settings
from app.lab_tests import CATALOG, LabTest
from app.models import Result, SavedResult
from app.normalize import normalize
from app.pages import Cell, looks_scanned, page_rows, page_text
from app.text import NUMBER, QUALIFIER, to_float

# A word that is one value: "7.2", "<148", "7.2%", "13.6*"; not a range, nor a name like "B12".
_VALUE_CELL = re.compile(rf"^(?:{QUALIFIER})?(?P<number>{NUMBER})[%*]?$")

BBox = tuple[float, float, float, float]


@dataclass
class _Check:
    result: SavedResult
    test: LabTest
    problems: list[str] = field(default_factory=list)  # each keeps the result at needs_check
    notes: list[str] = field(default_factory=list)  # for information only
    bbox: BBox | None = None

    @property
    def amount(self) -> float | None:
        """The value in the standard unit."""
        return self.result.value_std

    def __str__(self) -> str:
        """The value as notes print it: "100.39 mg/dL"."""
        return f"{self.result.value_std:g} {self.result.unit_std}"


def verify_results(results: Sequence[Result], doc: pymupdf.Document | None) -> list[SavedResult]:
    """Normalise one report's results and check them against its PDF.

    doc is the report's original PDF, or None when it could not be opened.
    """
    checks = [_check(result, doc) for result in results]
    _check_parts(checks)
    _check_friedewald(checks)
    _check_adag(checks)
    return [
        check.result.model_copy(
            update={
                "status": "needs_check" if check.problems else "verified",
                "notes": check.problems + check.notes,
                "bbox": check.bbox,
            }
        )
        for check in checks
    ]


def printed_number(text: str) -> float | None:
    """The number in a printed word that is one value ("7.2", "<148", "७.२", "7.2%"), else None."""
    match = _VALUE_CELL.match(text)
    return to_float(match["number"]) if match else None


def _check(result: Result, doc: pymupdf.Document | None) -> _Check:
    normalized = normalize(result)
    check = _Check(
        result=SavedResult(**result.model_dump(), **normalized.model_dump()),
        test=CATALOG.test(result.test_code),
        problems=list(normalized.notes),
    )
    if normalized.value is not None:
        check.bbox, problem = _find_on_page(doc, result, normalized.value, check.test)
        if problem:
            check.problems.append(problem)
    low, high = check.test.plausible
    if check.amount is not None and not low <= check.amount <= high:
        check.problems.append(
            f"{check} is outside the believable limits for {check.test.name} "
            f"({low:g}–{high:g} {check.test.unit}); probably misread"
        )
    return check


def _find_on_page(
    doc: pymupdf.Document | None, result: Result, value: float, test: LabTest
) -> tuple[BBox | None, str | None]:
    """Where the value is printed in a row of its test, or why it can't be found."""
    if doc is None:
        return None, "the original PDF could not be opened, so the value was not checked against it"
    if not 1 <= result.page <= doc.page_count:
        return None, f"page {result.page} is not in the PDF"
    page = doc[result.page - 1]
    for row in page_rows(page):
        for cell_index, cell in enumerate(row):
            for word_index, word in enumerate(cell):
                number = printed_number(word.text)
                if number is None or not math.isclose(number, value):
                    continue
                label = _label(row[:cell_index], cell[:word_index])
                if 0 < len(label.split()) <= settings.max_label_words and test.is_named_by(label):
                    return word.bbox, None
    if looks_scanned(page, page_text(page)):
        return None, (
            f"page {result.page} is scanned, so the value can't be checked against the PDF's text; "
            "compare it with the original"
        )
    return None, f"{result.value_text} is not printed on page {result.page} in a row naming {test.name}"


def _label(cells_left: list[Cell], words_before: Cell) -> str:
    """The text that names a value: the words before it in its cell, then the cells to its left.

    Either stops at a number, which belongs to the column or test to the left ("Hemoglobin |
    14.5 | g/dL | 13.0 - 16.5 | Platelets | 250" labels 250 "Platelets"). A name's own
    number is kept when it shares a cell with the name ("Vitamin D, 25 Hydroxy | 150.00").
    """
    words: list[str] = []
    for word in reversed(words_before):
        if _is_number(word.text):
            return " ".join(words)
        words.insert(0, word.text)
    for cell in reversed(cells_left):
        texts = [word.text for word in cell]
        if _is_number(" ".join(texts)):
            break
        words[:0] = texts
    return " ".join(words)


def _is_number(text: str) -> bool:
    """Digits but no letters: "14.5", "13.0 - 16.5", "<200"; not "B12" or "25(OH)"."""
    return any(char.isdigit() for char in text) and not any(char.isalpha() for char in text)


# ---------------------------------------------------------------- results checked against each other
# The formulas below are in the catalog's standard units: mg/dL for lipids and glucose, % for HbA1c.


def _single(checks: list[_Check], code: str) -> _Check | None:
    """The report's one comparable result for a test; None if absent, repeated or inexact ("< 50")."""
    found = [
        check
        for check in checks
        if check.result.test_code == code and check.amount is not None and check.result.qualifier is None
    ]
    return found[0] if len(found) == 1 else None


def _check_parts(checks: list[_Check]) -> None:
    for total_code, part_codes in CATALOG.checks.parts_of.items():
        if (total := _single(checks, total_code)) is None:
            continue
        for part_code in part_codes:
            part = _single(checks, part_code)
            if part is not None and part.amount > total.amount:
                problem = (
                    f"{part.test.name} {part} is more than {total.test.name} {total}, which includes it; "
                    "one of them is probably misread"
                )
                part.problems.append(problem)
                total.problems.append(problem)


def _check_friedewald(checks: list[_Check]) -> None:
    total, hdl, tg, ldl = (_single(checks, code) for code in ("CHOL", "HDL", "TG", "LDL"))
    if None in (total, hdl, tg, ldl) or tg.amount >= CATALOG.checks.friedewald_max_tg:
        return
    estimate = total.amount - hdl.amount - tg.amount / 5
    if abs(ldl.amount - estimate) > CATALOG.checks.friedewald_tolerance * abs(estimate):
        ldl.notes.append(
            f"{ldl.test.name} {ldl} differs from the Friedewald estimate of {estimate:.0f} mg/dL "
            "(total cholesterol - HDL - triglycerides / 5)"
        )


def _check_adag(checks: list[_Check]) -> None:
    hba1c, fasting = _single(checks, "HBA1C"), _single(checks, "GLU_F")
    if hba1c is None or fasting is None:
        return
    average = 28.7 * hba1c.amount - 46.7
    if abs(fasting.amount - average) > CATALOG.checks.adag_tolerance * average:
        hba1c.notes.append(
            f"{hba1c.test.name} {hba1c} corresponds to an average glucose of about {average:.0f} mg/dL "
            f"(ADAG), while fasting glucose on this report is {fasting}; worth asking the doctor about"
        )
