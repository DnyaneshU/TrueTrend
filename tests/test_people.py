"""The person a report is for, as printed: names, sex and age."""

import pytest
from factories import printed_person
from hypothesis import given
from hypothesis import strategies as st

from truetrend.people import (
    VOCABULARY,
    age_of,
    birth_year_of,
    display_name,
    initials_clash,
    is_a_name,
    same_person_name,
    sex_of,
)


@pytest.mark.parametrize(
    "a, b",
    [
        ("Mrs. Sunita Patil", "SUNITA PATIL."),
        ("Patil Sunita", "Sunita Patil"),  # surname first, as many labs print it
        ("Sunita R. Patil", "Sunita Patil"),  # a middle initial only one prints
        ("Mrs.Sunita Patil", "Smt Sunita Patil"),
        ("श्रीमती सुनीता पाटील", "सुनीता पाटील"),
        ("Dr. Anil Patil", "Mr Anil Patil"),
        ("B/O Sunita Patil", "b / o SUNITA PATIL"),  # the same baby
        ("Sunita‍ Patil", "Sunita Patil"),  # an invisible joiner is not a space
    ],
)
def test_same_person_name(a, b):
    assert same_person_name(a, b) and same_person_name(b, a)


@pytest.mark.parametrize(
    "a, b",
    [
        ("S. Patil", "Sunita Patil"),  # an initial is not a name
        ("Sunita Ramesh Patil", "Sunita Patil"),  # nor is a missing middle name guessed
        ("Ramesh Patil", "Sunita Ramesh Patil"),  # ... which would match a father to his daughter
        ("Sunita R Patil", "Sunita K Patil"),
        ("Sunita", "Sunita R"),  # one word is too little to ignore an initial
        ("B/O Sunita Patil", "Sunita Patil"),  # her baby
        ("S/O Anil Patil", "D/O Anil Patil"),
        ("Sunita Patil", None),
        ("Mrs.", "Mr."),  # only titles: no name at all
        ("Patient", "Patient"),  # a placeholder is nobody's name
    ],
)
def test_different_person_names(a, b):
    assert not same_person_name(a, b) and not same_person_name(b, a)


def test_initials_clash_only_when_both_print_different_ones():
    assert initials_clash("Sunita R. Patil", "Sunita K Patil")
    assert not initials_clash("Sunita R. Patil", "Sunita Patil")
    assert not initials_clash("Sunita R. Patil", "SUNITA R PATIL")


@pytest.mark.parametrize("printed", [None, "", "Mrs.", "Dr. Mr.", "Patient", "Demo Patient Name", "NA"])
def test_text_that_names_no_one(printed):
    assert not is_a_name(printed)


@pytest.mark.parametrize(
    "printed, shown",
    [
        ("MRS. SUNITA PATIL", "Sunita Patil"),
        ("Mrs.Sunita R. Patil", "Sunita R Patil"),
        ("श्री अनिल पाटील", "अनिल पाटील"),
    ],
)
def test_display_name_drops_titles(printed, shown):
    assert display_name(printed) == shown


@pytest.mark.parametrize(
    "printed, years",
    [
        ("62", 62), ("62 Y", 62), ("62 Years", 62), ("62Y 3M", 62), ("62/F", 62), ("६२ वर्षे", 62),
        ("1.5 Years", 1), ("DOB 12/03/1962 (63 Y)", 63), ("8 Months", 0), ("12 Days", 0),
        ("62 M", None),  # male, or 62 months? Not guessed.
        ("150 Y", None), ("NA", None), (None, None),
    ],
)  # fmt: skip
def test_age_of(printed, years):
    assert age_of(printed) == years


@given(st.integers(min_value=0, max_value=VOCABULARY.max_age), st.sampled_from(sorted(VOCABULARY.year_units)))
def test_any_age_in_years_reads_back(years, unit):
    assert age_of(f"{years} {unit}") == age_of(f"{years}{unit.upper()}") == years


def test_birth_year_from_a_printed_date_of_birth_or_from_the_age():
    assert birth_year_of(printed_person(age="DOB 12/03/1962 (63 Y)", sample_date="2026-01-15")) == 1962
    assert birth_year_of(printed_person(age="62 Y", sample_date="2026-01-15")) == 1964
    assert birth_year_of(printed_person(age="62 Y", sample_date=None, report_date="2025-06-01")) == 1963
    assert birth_year_of(printed_person(age="62 M")) is None


@pytest.mark.parametrize(
    "name, sex, age, expected",
    [
        ("Sunita Patil", "Female", None, "F"),
        ("Sunita Patil", "स्त्री", None, "F"),
        ("Anil Patil", "M", None, "M"),
        ("Sunita Patil", None, "62 Y / F", "F"),  # printed in the age field
        ("Mrs. Sunita Patil", None, None, "F"),  # from the title when no sex is printed
        ("Dr. Mrs. Anil Joshi", None, None, "F"),  # the first title that says a sex
        ("Dr. Anil Patil", None, None, None),
        ("Mr. Anil Patil", "F", None, "F"),  # what is printed wins over the title
    ],
)
def test_sex_of(name, sex, age, expected):
    assert sex_of(printed_person(name, sex=sex, age=age)) == expected
