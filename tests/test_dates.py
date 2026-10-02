"""parse_date: dates printed on lab reports -> ISO YYYY-MM-DD, day-first.

The property tests generate dates in every common Indian lab format and check each
reads back exactly; the examples at the end are the inputs that must NOT become a date.
"""

from datetime import date, datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.dates import parse_date

# How labs print the date part (day always before month), the time part, and a label.
DATE_FORMATS = [
    "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y", "%d-%m-%y",
    "%d-%b-%Y", "%d-%b-%y", "%d %b %Y", "%d %B %Y", "%d/%b/%Y", "%d-%B-%Y", "%Y-%m-%d",
]  # fmt: skip
TIME_FORMATS = ["", " %H:%M", " %H:%M:%S", " %I:%M %p", " %I:%M:%S%p"]
LABELS = ["", "Collected: ", "Sample Collected On : ", "Reported on "]

report_dates = st.dates(min_value=date(1990, 1, 1), max_value=date(2035, 12, 31))


@given(
    day=report_dates,
    clock=st.times(),
    date_format=st.sampled_from(DATE_FORMATS),
    time_format=st.sampled_from(TIME_FORMATS),
    label=st.sampled_from(LABELS),
)
def test_a_printed_date_reads_back_exactly(day, clock, date_format, time_format, label):
    printed = label + datetime.combine(day, clock).strftime(date_format + time_format)
    assert parse_date(printed) == day.isoformat(), printed


@given(day=report_dates, partial_format=st.sampled_from(["%b %Y", "%B %Y", "%m/%Y", "%d %b", "%d/%m"]))
def test_a_date_missing_its_day_month_or_year_is_never_guessed(day, partial_format):
    printed = day.strftime(partial_format)
    assert parse_date(printed) is None, printed


@pytest.mark.parametrize(
    "printed",
    [
        "31/02/2026",  # no 31 February
        "2026-02-31",
        "02 Dec, 2X",  # a blanked-out year must not become 2002
        "03:11 PM 02 Dec, 2X",
        "08:10",  # time only
        "garbage",
        "",
        None,
    ],
)
def test_text_that_is_not_a_full_date_gives_none(printed):
    assert parse_date(printed) is None
