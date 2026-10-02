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


class LabTest(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    name: str
    prompt: str
    names: tuple[str, ...] = Field(min_length=1)
    not_names: tuple[str, ...] = ()

    @field_validator("names", "not_names")
    @classmethod
    def _normalise(cls, phrases: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(normalise_name(phrase) for phrase in phrases)

    def conflict(self, raw_name: str) -> str | None:
        """Why the printed name can't be this test, or None if it fits."""
        name = normalise_name(raw_name)
        if any(_contains(name, p) for p in self.names) and not any(
            _contains(name, p) for p in self.not_names
        ):
            return None
        return f"'{raw_name}' is not {self.code}"


class ReportVocabulary(BaseModel):
    model_config = ConfigDict(frozen=True)

    flags: tuple[str, ...]
    empty_words: frozenset[str]

    @field_validator("empty_words")
    @classmethod
    def _casefold(cls, words: frozenset[str]) -> frozenset[str]:
        return frozenset(word.casefold() for word in words)


class Catalog(BaseModel):
    model_config = ConfigDict(frozen=True)

    tests: tuple[LabTest, ...] = Field(min_length=1)
    prompt_exclusions: tuple[str, ...]
    report: ReportVocabulary

    @model_validator(mode="after")
    def _unique_codes(self) -> "Catalog":
        codes = [test.code for test in self.tests]
        if len(codes) != len(set(codes)):
            raise ValueError(f"duplicate test codes in the catalog: {codes}")
        return self

    @classmethod
    def load(cls, path: Path) -> "Catalog":
        with path.open("rb") as file:
            return cls.model_validate(tomllib.load(file))

    def test(self, code: str) -> LabTest:
        return next(test for test in self.tests if test.code == code)


CATALOG = Catalog.load(settings.data_dir / "lab_tests.toml")
LAB_TESTS = CATALOG.tests
TestCode = Literal[tuple(test.code for test in LAB_TESTS)]


def name_conflict(test_code: str, raw_name: str) -> str | None:
    """Why the printed test name can't be `test_code`, or None if it fits."""
    return CATALOG.test(test_code).conflict(raw_name)
