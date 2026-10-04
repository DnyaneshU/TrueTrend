"""The person a report is for, as printed: names, sex and age, and whether two are the same.

Names are compared ignoring case, punctuation, word order and titles ("Mrs. Sunita Patil"
= "PATIL SUNITA"); a middle initial only one of them prints is ignored when both have at
least two full words ("Sunita R. Patil" = "Sunita Patil"). Nothing else is guessed:
"S. Patil" and "Sunita Ramesh Patil" are other names, and "Sunita R. Patil" and
"Sunita K. Patil" are other people (the initial is a father's or husband's name). A name
with a relation marker ("B/O Sunita Patil": Sunita's baby) is only ever the same as one
with the same marker. The words labs print are in data/people.toml.
"""

import re
import unicodedata
from collections import Counter
from typing import NamedTuple

from pydantic import BaseModel, ConfigDict, Field, field_validator

from truetrend.models import PrintedPerson, Sex
from truetrend.resources import load_toml
from truetrend.text import words

# A four-digit year, as in a printed date of birth: "DOB 12/03/1962 (63 Y)".
_YEAR = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
# A number and what is printed right after it: "62 Y", "62Y", "8 Months", "६२ वर्षे".
_NUMBER_UNIT = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s*(\S*)")


class PeopleVocabulary(BaseModel):
    """data/people.toml: the words labs print around a person's name, sex and age."""

    model_config = ConfigDict(frozen=True)

    titles: dict[str, Sex | None]
    sexes: dict[str, Sex]
    relation_markers: tuple[str, ...]
    placeholders: frozenset[str]
    year_units: frozenset[str]
    under_a_year_units: frozenset[str]
    max_age: int = Field(gt=0)

    @field_validator("titles", mode="before")
    @classmethod
    def _no_sex_is_empty(cls, titles: dict[str, str]) -> dict[str, str | None]:
        return {title: sex or None for title, sex in titles.items()}

    @classmethod
    def load(cls) -> "PeopleVocabulary":
        data = load_toml("people.toml")
        return cls.model_validate(
            {"titles": data["titles"], "sexes": data["sexes"], **data["names"], **data["ages"]}
        )


def _marker_pattern(marker: str) -> str:
    """A relation marker however it is spaced: "b/o" matches "B/O", "b / o"; "baby of", "Baby  of"."""
    spaced = r"\s*/\s*".join(r"\s+".join(map(re.escape, part.split())) for part in marker.split("/"))
    return r"(?<!\w)" + spaced + r"(?!\w)"


VOCABULARY = PeopleVocabulary.load()
_RELATION = re.compile("|".join(map(_marker_pattern, VOCABULARY.relation_markers)), re.IGNORECASE)


class NameKey(NamedTuple):
    """What a printed name is compared by."""

    relation: str | None  # "b/o" for "B/O Sunita Patil"
    full_words: Counter[str]
    initials: Counter[str]


def name_key(name: str) -> NameKey:
    """The name without titles or relation markers, as full words and initials in any order."""
    relation = _RELATION.search(name)
    rest = _RELATION.sub(" ", name)
    name_words = _without_titles(words(rest))
    return NameKey(
        relation=" ".join(words(relation[0])) if relation else None,
        full_words=Counter(word for word in name_words if len(word) > 1),
        initials=Counter(word for word in name_words if len(word) == 1),
    )


def is_a_name(printed: str | None) -> bool:
    """True when the text names someone: a full word that isn't a title or a placeholder ("Patient")."""
    if not printed:
        return False
    full_words = name_key(printed).full_words
    return bool(full_words) and not set(full_words) <= VOCABULARY.placeholders


def same_person_name(a: str | None, b: str | None) -> bool:
    """Two printed names are one person's (see the module docstring); a non-name never is."""
    if not is_a_name(a) or not is_a_name(b):
        return False
    key_a, key_b = name_key(a), name_key(b)
    if key_a.relation != key_b.relation or key_a.full_words != key_b.full_words:
        return False
    if key_a.initials == key_b.initials:
        return True
    one_has_none = not key_a.initials or not key_b.initials
    return one_has_none and key_a.full_words.total() >= 2


def initials_clash(a: str, b: str) -> bool:
    """True when two names differ only in the middle initials both print: two people."""
    key_a, key_b = name_key(a), name_key(b)
    return (
        key_a.relation == key_b.relation
        and key_a.full_words == key_b.full_words
        and bool(key_a.initials)
        and bool(key_b.initials)
        and key_a.initials != key_b.initials
    )


def display_name(printed: str) -> str:
    """How a patient is shown: the printed name without its titles, in title case if it was all capitals."""
    parts = [part for part in re.split(r"[\s.,]+", printed) if part]
    while parts and "".join(words(parts[0])) in VOCABULARY.titles:
        parts = parts[1:]
    name = " ".join(parts) or printed.strip()
    return name.title() if name.isupper() else name


def sex_of(person: PrintedPerson) -> Sex | None:
    """The printed sex ("F", "Female", "स्त्री"; also in the age field, "62 Y / F"),
    or the one the name's first title with a sex says ("Mrs.", "Dr. Mrs.")."""
    for field in (person.sex, person.age):
        for word in words(field or ""):
            if word in VOCABULARY.sexes:
                return VOCABULARY.sexes[word]
    for word in words(person.name or ""):
        if word not in VOCABULARY.titles:
            break
        if sex := VOCABULARY.titles[word]:
            return sex
    return None


def age_of(text: str | None) -> int | None:
    """Whole years from a printed age: "62 Y" -> 62, "62Y 3M" -> 62, "8 Months" -> 0.

    None when it can't be told: no number, "62 M" (male or months?), over the oldest age.
    """
    if not text:
        return None
    found = [
        (float(number), _leading_letters(after).casefold()) for number, after in _NUMBER_UNIT.findall(text)
    ]
    for number, unit in found:
        if unit in VOCABULARY.year_units:
            return int(number) if number <= VOCABULARY.max_age else None
    if any(unit in VOCABULARY.under_a_year_units for _, unit in found):
        return 0
    if len(found) == 1 and not found[0][1] and found[0][0] <= VOCABULARY.max_age:
        return int(found[0][0])  # a lone number: "62", "62/F"
    return None


def birth_year_of(person: PrintedPerson) -> int | None:
    """The year of birth: as printed in a date of birth, or from the age and the year of the sample."""
    when = person.sample_date or person.report_date
    if person.age and (year := _YEAR.search(person.age)) and (not when or int(year[0]) <= int(when[:4])):
        return int(year[0])
    age = age_of(person.age)
    return int(when[:4]) - age if age is not None and when else None


def _leading_letters(text: str) -> str:
    """The letters a word starts with, Devanagari vowel signs included: "वर्षे/F" -> "वर्षे"."""
    for end, char in enumerate(text):
        if unicodedata.category(char)[0] not in "LM":
            return text[:end]
    return text


def _without_titles(name_words: list[str]) -> list[str]:
    while name_words and name_words[0] in VOCABULARY.titles:
        name_words = name_words[1:]
    return name_words
