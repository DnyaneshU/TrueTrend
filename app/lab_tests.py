"""The lab test catalog (data/lab_tests.toml) and the check of Gemma's test codes.

Code checks Gemma's test code against the printed test name because Gemma (vision
mode especially) sometimes files a look-alike under an MVP code: seen "Estimated
Average Glucose" as GLU_F, "Total T4" as FT4 and "Hb A 84.4 %" as haemoglobin.
"""

import re
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.config import settings

_DOTTED_ABBREVIATION = re.compile(r"\b(?:[^\W\d_]\.){2,}")  # "t.s.h." but not "s.creatinine"
_LETTER_DIGIT_BOUNDARY = re.compile(r"(?<=[^\W\d_])(?=\d)|(?<=\d)(?=[^\W\d_])")


def normalise_name(text: str) -> str:
    """Spelling-insensitive form of a test name: 'Vitamin B-12' and 'vitamin b12' -> 'vitamin b 12'."""
    text = _DOTTED_ABBREVIATION.sub(lambda match: match[0].replace(".", ""), text.casefold())
    text = re.sub(r"[\W_]+", " ", text)
    return " ".join(_LETTER_DIGIT_BOUNDARY.sub(" ", text).split())


def _contains(name: str, phrase: str) -> bool:
    """Whole-word phrase match on normalised text."""
    return f" {phrase} " in f" {name} "


_MICRO = re.compile(r"micro|[µμ]")  # µIU/mL = uIU/mL = microIU/mL
_GRAM = re.compile(r"\bgms?\b|gms?(?=/)")  # gm/dL = g/dL
_LITRE = re.compile(r"lit(?:re|er)s?|ltr")


def normalise_unit(text: str) -> str:
    """Case-, spacing- and spelling-insensitive form of a unit: 'gm/dl' and 'g / dL' -> 'g/dl'."""
    text = _MICRO.sub("u", text.casefold()).replace("mcg", "ug")
    text = _LITRE.sub("l", _GRAM.sub("g", text))
    return re.sub(r"[\s.]+", "", text)


class Conversion(BaseModel):
    """standard = printed × multiply ÷ divide + offset."""

    model_config = ConfigDict(frozen=True)

    multiply: float = 1.0
    divide: float = 1.0
    offset: float = 0.0

    def apply(self, value: float) -> float:
        return value * self.multiply / self.divide + self.offset


class Variation(BaseModel):
    """How much a result varies with no real change, as CVs in %, for the Reference Change Value."""

    model_config = ConfigDict(frozen=True)

    cvi: float = Field(gt=0)  # within-person biological variation
    cva: float = Field(gt=0)  # one lab's analytical variation
    between_lab_cv: float | None = Field(default=None, gt=0)  # added when two labs are compared
    source: str = Field(min_length=1)  # where every number above comes from


class LabTest(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    name: str
    name_mr: str  # how the Marathi summary names it
    prompt: str
    names: tuple[str, ...] = Field(min_length=1)
    not_names: tuple[str, ...] = ()
    unit: str  # the standard unit
    units: dict[str, Conversion]  # normalised printed unit -> conversion to the standard unit
    plausible: tuple[float, float]  # believable limits in the standard unit, far wider than normal
    variation: Variation | None = None  # None until sourced constants are found: changes aren't judged

    @field_validator("names", "not_names")
    @classmethod
    def _normalise(cls, phrases: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(normalise_name(phrase) for phrase in phrases)

    @field_validator("units", mode="before")
    @classmethod
    def _read_units(cls, units: dict) -> dict:
        """A bare number in the catalog means 'multiply by'; unit spellings are normalised."""
        return {
            normalise_unit(unit): {"multiply": rule} if isinstance(rule, int | float) else rule
            for unit, rule in units.items()
        }

    @model_validator(mode="after")
    def _check(self) -> "LabTest":
        if self.units.get(normalise_unit(self.unit)) != Conversion():
            raise ValueError(f"{self.code}: its standard unit {self.unit!r} must be listed with factor 1")
        low, high = self.plausible
        if not 0 <= low < high:
            raise ValueError(f"{self.code}: plausible must be [low, high] with 0 <= low < high")
        return self

    def conversion(self, printed_unit: str | None) -> Conversion | None:
        """How to turn a value in `printed_unit` into the standard unit, or None if the unit is unknown."""
        return self.units.get(normalise_unit(printed_unit)) if printed_unit else None

    def is_named_by(self, printed_name: str) -> bool:
        """True when a printed name contains one of this test's names and none of its look-alikes."""
        name = normalise_name(printed_name)
        return any(_contains(name, p) for p in self.names) and not any(
            _contains(name, p) for p in self.not_names
        )

    def conflict(self, raw_name: str) -> str | None:
        """Why the printed name can't be this test, or None if it fits."""
        return None if self.is_named_by(raw_name) else f"'{raw_name}' is not {self.code}"


class ReportVocabulary(BaseModel):
    model_config = ConfigDict(frozen=True)

    flags: tuple[str, ...]
    empty_words: frozenset[str]
    normal_range_labels: frozenset[str]

    @field_validator("empty_words", "normal_range_labels")
    @classmethod
    def _casefold(cls, words: frozenset[str]) -> frozenset[str]:
        return frozenset(word.casefold() for word in words)


class CrossChecks(BaseModel):
    """How results of one report are checked against each other (see app.verify)."""

    model_config = ConfigDict(frozen=True)

    parts_of: dict[str, tuple[str, ...]]  # each listed test can't be more than its total
    friedewald_max_tg: float
    friedewald_tolerance: float
    adag_tolerance: float


class Catalog(BaseModel):
    model_config = ConfigDict(frozen=True)

    tests: tuple[LabTest, ...] = Field(min_length=1)
    prompt_exclusions: tuple[str, ...]
    report: ReportVocabulary
    checks: CrossChecks

    @model_validator(mode="after")
    def _known_codes(self) -> "Catalog":
        codes = [test.code for test in self.tests]
        if len(codes) != len(set(codes)):
            raise ValueError(f"duplicate test codes in the catalog: {codes}")
        named = set(self.checks.parts_of).union(*self.checks.parts_of.values())
        if unknown := named - set(codes):
            raise ValueError(f"checks.parts_of names unknown test codes: {sorted(unknown)}")
        return self

    @classmethod
    def load(cls, path: Path) -> "Catalog":
        with path.open("rb") as file:
            return cls.model_validate(tomllib.load(file))

    def test(self, code: str) -> LabTest:
        for test in self.tests:
            if test.code == code:
                return test
        raise KeyError(f"unknown test code: {code!r}")


CATALOG = Catalog.load(settings.data_dir / "lab_tests.toml")
TestCode = Literal[tuple(test.code for test in CATALOG.tests)]
