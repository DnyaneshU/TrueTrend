"""The change engine: real change or normal variation, by the (log-normal) Reference Change Value."""

import math
from datetime import date

import pytest
from factories import timeline_point
from hypothesis import given
from hypothesis import strategies as st

from truetrend.change import judge, rcv_limits, timeline_changes, timelines
from truetrend.lab_tests import CATALOG, Variation

# Example constants for the tests only; real ones live in truetrend/data/lab_tests.toml with their source.
VARIATION = Variation(cvi=2.0, cva=1.0, between_lab_cv=3.0, source="test")
HBA1C = CATALOG.test("HBA1C").model_copy(update={"variation": VARIATION})

cvs = st.floats(min_value=0.1, max_value=60)


def lognormal(cv_percent):
    spread = 1.96 * math.sqrt(2) * math.sqrt(math.log(1 + (cv_percent / 100) ** 2))
    return (math.exp(spread) - 1) * 100, (1 - math.exp(-spread)) * 100


SAME_LAB = lognormal(math.hypot(1.0, 2.0))  # rise 6.39 %, fall 6.01 %
CROSS_LAB = lognormal(math.hypot(math.hypot(1.0, 3.0), 2.0))


def point(value, day=1, lab="Sunrise Diagnostics", report=None, **overrides):
    """An HbA1c result on 2026-01-<day>."""
    return timeline_point(
        "HBA1C", value, date(2026, 1, day), lab_name=lab, report_id=report or day, **overrides
    )


# ---------------------------------------------------------------- the threshold


def test_rcv_for_one_lab():
    assert rcv_limits(VARIATION, same_lab=True) == pytest.approx(SAME_LAB)
    assert rcv_limits(VARIATION, same_lab=True) == pytest.approx((6.39, 6.01), abs=0.01)


def test_rcv_for_two_labs_adds_between_lab_variation():
    assert rcv_limits(VARIATION, same_lab=False) == pytest.approx(CROSS_LAB)


def test_rcv_for_two_labs_is_unknown_without_a_between_lab_cv():
    assert rcv_limits(VARIATION.model_copy(update={"between_lab_cv": None}), same_lab=False) is None


@given(cvi=cvs, cva=cvs, between=cvs)
def test_rcv_properties(cvi, cva, between):
    variation = Variation(cvi=cvi, cva=cva, between_lab_cv=between, source="test")
    (rise, fall), (rise_2, fall_2) = rcv_limits(variation, True), rcv_limits(variation, False)
    assert rise_2 >= rise and fall_2 >= fall  # two labs never need a smaller change than one
    assert rise > fall  # log-normal: a rise must be bigger than a fall
    assert (1 + rise / 100) * (1 - fall / 100) == pytest.approx(1)  # the same ratio up and down


# ---------------------------------------------------------------- judging a pair


@pytest.mark.parametrize(
    "before, after, kind",
    [
        (7.0, 7.4, "within_normal_variation"),  # +5.7 %, under the 6.39 % rise
        (7.0, 7.5, "real_increase"),  # +7.1 %
        (7.0, 6.55, "real_decrease"),  # -6.4 %, over the 6.01 % fall
        (7.0, 6.6, "within_normal_variation"),  # -5.7 %
        (7.0, 7.0, "within_normal_variation"),
    ],
)
def test_a_pair_from_one_lab(before, after, kind):
    change = judge(point(before, day=1), point(after, day=2), HBA1C)
    rcv = SAME_LAB[0] if after > before else SAME_LAB[1]
    assert (change.kind, change.same_lab, change.rcv_percent) == (kind, True, pytest.approx(rcv))
    assert change.percent == pytest.approx((after - before) / before * 100)


def test_a_change_real_in_one_lab_can_be_normal_across_two():
    # +7.1 % is above the one-lab rise (6.4 %) but within the two-lab one
    change = judge(point(7.0, day=1, lab="Sunrise"), point(7.5, day=2, lab="Metro Labs"), HBA1C)
    assert (change.kind, change.same_lab) == ("within_normal_variation", False)
    assert change.rcv_percent == pytest.approx(CROSS_LAB[0])


def test_lab_names_match_ignoring_case_and_punctuation():
    assert judge(point(7.0, lab="SUNRISE DIAGNOSTICS."), point(7.1, day=2), HBA1C).same_lab


def test_an_unknown_lab_counts_as_a_different_lab():
    assert not judge(point(7.0, lab=None), point(7.1, day=2, lab=None), HBA1C).same_lab


NOT_COMPARABLE = [
    (point(7.5, day=2, status="needs_check"), "the value of 2026-01-02 needs checking against its report"),
    (
        point(7.5, day=2, qualifier="<", value_text="< 7.5"),
        "'< 7.5' on 2026-01-02 is a limit, not an exact value",
    ),
    (point(7.5, day=2, value_std=None, unit_std=None), "the value of 2026-01-02 is not in a known unit"),
    (point(7.5, day=2, unit_std="mmol/mol"), "the values are in different units (%, mmol/mol)"),
]


@pytest.mark.parametrize("after, reason", NOT_COMPARABLE)
def test_a_value_that_is_not_a_verified_exact_number_is_not_judged(after, reason):
    change = judge(point(7.0), after, HBA1C)
    assert (change.kind, change.reason, change.percent) == ("not_judged", reason, None)


def test_a_test_without_sourced_constants_is_not_judged():
    change = judge(point(7.0), point(9.0, day=2), HBA1C.model_copy(update={"variation": None}))
    assert (change.kind, change.reason) == ("not_judged", "no sourced variation constants for HbA1c")
    assert change.percent == pytest.approx(28.571, abs=0.001)  # still shown, just not judged


def test_two_labs_without_a_between_lab_cv_are_not_judged():
    test = HBA1C.model_copy(update={"variation": VARIATION.model_copy(update={"between_lab_cv": None})})
    change = judge(point(7.0, lab="Sunrise"), point(9.0, day=2, lab="Metro"), test)
    assert change.reason == "different labs, and no sourced between-lab variation for HbA1c"


def test_an_earlier_value_of_zero_is_not_judged():
    change = judge(point(0.0), point(1.0, day=2), HBA1C)
    assert change.kind == "not_judged" and "is 0" in change.reason


# ---------------------------------------------------------------- timelines


def test_timelines_group_by_test_oldest_first():
    glucose = point(110.0, day=3, test_code="GLU_F", unit_std="mg/dL")
    grouped = timelines([point(7.2, day=2), glucose, point(7.0, day=1)])
    assert [p.value_std for p in grouped["HBA1C"]] == [7.0, 7.2]
    assert grouped["GLU_F"] == [glucose]


def test_a_test_printed_twice_for_one_sample_keeps_its_verified_value():
    unverified = point(7.9, day=1, report=1, status="needs_check")
    verified = point(7.2, day=1, report=1).model_copy(update={"result_id": 2})
    assert timelines([unverified, verified])["HBA1C"] == [verified]


def test_two_reports_of_one_sample_date_are_one_result():
    # the same April sample uploaded twice: not a 0 % change, nor a third report
    first = point(7.0, day=1)
    april, again = point(7.6, day=4, report=2), point(7.6, day=4, report=3)
    assert timelines([first, april, again])["HBA1C"] == [first, april]


def test_timeline_changes_judge_each_consecutive_pair():
    kinds = [
        c.kind for c in timeline_changes([point(7.0, day=1), point(7.6, day=2), point(7.6, day=3)], HBA1C)
    ]
    assert kinds == ["real_increase", "within_normal_variation"]
