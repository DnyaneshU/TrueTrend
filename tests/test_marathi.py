from datetime import date

from hypothesis import given
from hypothesis import strategies as st

from truetrend import marathi

ASCII = str.maketrans("०१२३४५६७८९", "0123456789")


def test_digits_change_and_nothing_else():
    assert marathi.digits("HbA1c 7.1 %") == "HbA१c ७.१ %"


@given(st.floats(min_value=0, max_value=1e6, allow_nan=False))
def test_a_number_reads_back_as_the_same_value(value):
    shown = marathi.number(value)
    assert not any(char in "0123456789" for char in shown)
    assert float(shown.translate(ASCII)) == round(value, 6)
    assert "e" not in shown  # never "१.२e+०६"


def test_numbers_drop_trailing_zeros():
    assert (marathi.number(7.10), marathi.number(141.0)) == ("७.१", "१४१")


@given(st.dates())
def test_a_date_has_its_marathi_month(when):
    day, month, year = marathi.day(when).split(" ")
    assert (int(day.translate(ASCII)), int(year.translate(ASCII))) == (when.day, when.year)
    assert month == marathi.MONTHS[when.month - 1]


def test_a_date():
    assert marathi.day(date(2023, 2, 20)) == "२० फेब्रुवारी २०२३"
