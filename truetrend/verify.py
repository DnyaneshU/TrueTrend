"""Check each result against the report it came from, in plain Python.

A result is `verified` only when all of these hold; otherwise it stays `needs_check`
with notes saying why:

- Its value is printed on its page with the same qualifier ("< 148"), followed in its
  cell by nothing but its unit or a high/low mark, in a row whose label names its test
  (by the catalog's names and look-alikes); and its unit is printed in that row. The
  PDF's own text is used, never Gemma's reading of it, so a value on a scanned page
  always needs a check.
- Normalisation turned it into a number in a known unit, within the test's believable
  limits (truetrend/data/lab_tests.toml).
- It is not more than a total it is part of (LDL and HDL are parts of total cholesterol).

The lab's normal range is checked separately: `ref_verified` is true only when every
limit read from it is printed in the value's own row, apart from the value itself.
Only a verified range is used to say a result is outside it.

Two cross-checks only add a note and never change the status: the lab's LDL against
the Friedewald estimate, and HbA1c's average glucose (ADAG) against fasting glucose.
"""

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, field

import pymupdf

from truetrend.config import settings
from truetrend.lab_tests import CATALOG, LabTest, normalize_unit
from truetrend.models import Normalized, Result, SavedResult
from truetrend.normalize import normalize, parse_range
from truetrend.pages import Cell, Row, Word, looks_scanned, page_rows, rows_text
from truetrend.text import NUMBER, QUALIFIERS, printed_number, to_float

BBox = tuple[float, float, float, float]


@dataclass
class _Check:
    result: SavedResult
    test: LabTest
    problems: list[str] = field(default_factory=list)  # each keeps the result at needs_check
    notes: list[str] = field(default_factory=list)  # for information only
    bbox: BBox | None = None
    ref_verified: bool = False

    @property
    def amount(self) -> float | None:
        """The value in the standard unit."""
        return self.result.value_std

    def __str__(self) -> str:
        """The value as notes print it: "100.39 mg/dL"."""
        return f"{self.result.value_std:g} {self.result.unit_std}"


@dataclass(frozen=True)
class _Printed:
    """Where a value is printed: the word, and the row it is in."""

    word: Word
    row: Row


class _PdfText:
    """The report PDF's text layer, each page read into rows once."""

    def __init__(self, doc: pymupdf.Document | None) -> None:
        self.doc = doc
        self._rows: dict[int, list[Row]] = {}

    def rows(self, page: int) -> list[Row]:
        if page not in self._rows:
            self._rows[page] = page_rows(self.doc[page - 1])
        return self._rows[page]


def verify_results(results: Sequence[Result], doc: pymupdf.Document | None) -> list[SavedResult]:
    """Normalise one report's results and check them against its PDF.

    doc is the report's original PDF, or None when it could not be opened.
    """
    pdf = _PdfText(doc)
    checks = [_check(result, pdf) for result in results]
    _check_parts(checks)
    _check_friedewald(checks)
    _check_adag(checks)
    return [
        check.result.model_copy(
            update={
                "status": "needs_check" if check.problems else "verified",
                "notes": check.problems + check.notes,
                "bbox": check.bbox,
                "ref_verified": check.ref_verified,
            }
        )
        for check in checks
    ]


def _check(result: Result, pdf: _PdfText) -> _Check:
    test = CATALOG.test(result.test_code)
    normalized = normalize(result, test)
    check = _Check(
        result=SavedResult(**result.model_dump(), **normalized.model_dump()),
        test=test,
        problems=list(normalized.notes),
    )
    if normalized.value is not None:
        printed = _find_on_page(pdf, result, normalized, test)
        if isinstance(printed, str):
            check.problems.append(printed)
        else:
            check.bbox = printed.word.bbox
            check.ref_verified = _range_printed(printed, result.ref_text)
            if result.unit and not _unit_printed(printed.row, result.unit):
                check.problems.append(f"unit {result.unit!r} is not printed in the value's row")
    low, high = test.plausible
    if check.amount is not None and not low <= check.amount <= high:
        check.problems.append(
            f"{check} is outside the believable limits for {test.name} "
            f"({low:g}–{high:g} {test.unit}); probably misread"
        )
    return check


def _find_on_page(pdf: _PdfText, result: Result, normalized: Normalized, test: LabTest) -> _Printed | str:
    """Where the value is printed, with its qualifier, in a row naming its test; or why it isn't."""
    if pdf.doc is None:
        return "the original PDF could not be opened, so the value was not checked against it"
    if not 1 <= result.page <= pdf.doc.page_count:
        return f"page {result.page} is not in the PDF"
    rows = pdf.rows(result.page)
    for row in rows:
        for cell_index, cell in enumerate(row):
            for word_index, word in enumerate(cell):
                if not (
                    _reads_as(cell, word_index, normalized)
                    and _ends_its_cell(cell[word_index + 1 :], result.unit)
                ):
                    continue
                label = _label(row[:cell_index], cell[:word_index])
                if len(label.split()) <= settings.max_label_words and test.is_named_by(label):
                    return _Printed(word, row)
    if looks_scanned(pdf.doc[result.page - 1], rows_text(rows)):
        return (
            f"page {result.page} is scanned, so the value can't be checked against the PDF's text; "
            "compare it with the original"
        )
    return f"{result.value_text} is not printed on page {result.page} in a row naming {test.name}"


def _reads_as(cell: Cell, index: int, normalized: Normalized) -> bool:
    """True when the word at `index` is the value with the same qualifier ("<148" or "<" "148")."""
    printed = printed_number(cell[index].text)
    if printed is None or not math.isclose(printed[1], normalized.value):
        return False
    qualifier = printed[0]
    if qualifier is None and index > 0:  # the qualifier printed as a word of its own
        qualifier = QUALIFIERS.get(cell[index - 1].text)
    return qualifier == normalized.qualifier


def _ends_its_cell(words_after: Cell, unit: str | None) -> bool:
    """True when nothing but the unit or a high/low mark follows the value in its cell.

    "7.2 %" and "18.0 L" are values; "6.5 Diabetes" is a sentence that mentions a number.
    """
    unit_key = normalize_unit(unit) if unit else None
    flags = set(CATALOG.report.flags)
    return all(
        word.text in flags
        or normalize_unit(word.text) == unit_key
        or not any(char.isalpha() for char in word.text)
        for word in words_after
    )


def _unit_printed(row: Row, unit: str) -> bool:
    """True when the unit is printed in the row: as a cell, a word, or after the value ("7.2%")."""
    wanted = normalize_unit(unit)
    texts = [" ".join(word.text for word in cell) for cell in row]
    texts += [word.text for cell in row for word in cell]
    texts += [re.sub(rf"^[<>=≤≥]*(?:{NUMBER})", "", text) for text in texts]
    return any(normalize_unit(text) == wanted for text in texts)


def _range_printed(printed: _Printed, ref_text: str | None) -> bool:
    """True when the normal range reads as limits, each printed in the value's row (not as the value)."""
    limits = [limit for limit in parse_range(ref_text) or () if limit is not None]
    numbers = [
        to_float(number)
        for cell in printed.row
        for word in cell
        if word is not printed.word
        for number in re.findall(NUMBER, word.text)
    ]
    return bool(limits) and all(any(math.isclose(limit, number) for number in numbers) for limit in limits)


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
