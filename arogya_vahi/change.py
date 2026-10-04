"""Is the difference between two results a real change, or normal variation?

A result varies from one sample to the next with no real change: the body varies (CVi)
and so does the lab's measurement (CVa). The Reference Change Value is the smallest
difference that is bigger than both together at 95 % confidence. Results vary in
proportion to their size, so the RCV is the log-normal one EFLM recommends, which is
asymmetric: a rise must be bigger than a fall to count. For a total CV of
CV = √(CVa² + CVi²):

    σ = √ln(1 + CV²)        rise: e^(1.96·√2·σ) − 1        fall: 1 − e^(−1.96·√2·σ)

For two different labs CVa is widened by the between-lab variation,
CVa_eff = √(CVa² + between_lab_CV²). The CVs per test, with their sources, are in
arogya_vahi/data/lab_tests.toml; a test without them is never judged.

Only verified, exact values are compared; any other pair is `not_judged`, with why.
"""

import math
from collections.abc import Iterable, Sequence
from itertools import pairwise

from arogya_vahi.lab_tests import CATALOG, LabTest, Variation
from arogya_vahi.models import Change, Timeline, TimelinePoint
from arogya_vahi.text import same_name

Z_95 = 1.96  # two-sided 95 %


def rcv_limits(variation: Variation, same_lab: bool) -> tuple[float, float] | None:
    """(rise, fall) in %: the smallest real increase and decrease.

    None for two labs when no between-lab CV is known.
    """
    cva = variation.cva
    if not same_lab:
        if variation.between_lab_cv is None:
            return None
        cva = math.hypot(cva, variation.between_lab_cv)
    sigma = math.sqrt(math.log1p((math.hypot(cva, variation.cvi) / 100) ** 2))
    spread = Z_95 * math.sqrt(2) * sigma
    return math.expm1(spread) * 100, -math.expm1(-spread) * 100


def timelines(points: Iterable[TimelinePoint]) -> dict[str, list[TimelinePoint]]:
    """Points grouped by test, oldest first, one per sample date.

    Two results of one test from the same sample date (printed twice, or the same sample
    in two uploaded reports) are one result: the first verified one is kept, else the first.
    """
    by_test: dict[str, dict] = {}
    for point in sorted(points, key=lambda point: (point.sample_date, point.report_id, point.result_id)):
        per_date = by_test.setdefault(point.test_code, {})
        kept = per_date.get(point.sample_date)
        if kept is None or (kept.status != "verified" and point.status == "verified"):
            per_date[point.sample_date] = point
    return {code: list(per_date.values()) for code, per_date in by_test.items()}


def every_timeline(points: Iterable[TimelinePoint]) -> list[Timeline]:
    """Each catalog test's timeline, in catalog order, with its changes judged.

    Results of a test since removed from the catalog are left out.
    """
    by_test = timelines(points)
    return [
        Timeline(
            code=test.code,
            name=test.name,
            name_mr=test.name_mr,
            unit=test.unit,
            points=by_test[test.code],
            changes=timeline_changes(by_test[test.code], test),
        )
        for test in CATALOG.tests
        if test.code in by_test
    ]


def timeline_changes(points: Sequence[TimelinePoint], test: LabTest) -> list[Change]:
    """Each consecutive pair of one test's timeline (oldest first), judged."""
    return [judge(before, after, test) for before, after in pairwise(points)]


def judge(before: TimelinePoint, after: TimelinePoint, test: LabTest) -> Change:
    """Whether `after` differs from `before` by more than normal variation."""
    same_lab = same_name(before.lab_name, after.lab_name)  # an unknown lab counts as a different one
    change = Change(before=before, after=after, kind="not_judged", same_lab=same_lab)
    if reason := _not_comparable(before, after):
        return change.model_copy(update={"reason": reason})
    percent = (after.value_std - before.value_std) / before.value_std * 100
    limits = rcv_limits(test.variation, same_lab) if test.variation else None
    if limits is None:
        if test.variation is None:
            reason = f"no sourced variation constants for {test.name}"
        else:
            reason = f"different labs, and no sourced between-lab variation for {test.name}"
        return change.model_copy(update={"percent": percent, "reason": reason})
    rise, fall = limits
    rcv = rise if percent > 0 else fall
    if abs(percent) <= rcv:
        kind = "within_normal_variation"
    else:
        kind = "real_increase" if percent > 0 else "real_decrease"
    return change.model_copy(update={"kind": kind, "percent": percent, "rcv_percent": rcv})


def _not_comparable(before: TimelinePoint, after: TimelinePoint) -> str | None:
    """Why two values can't be compared as numbers, or None if they can."""
    for point in (before, after):
        if point.status != "verified":
            return f"the value of {point.sample_date} needs checking against its report"
        if point.qualifier is not None:
            return f"'{point.value_text}' on {point.sample_date} is a limit, not an exact value"
        if point.value_std is None:
            return f"the value of {point.sample_date} is not in a known unit"
    if before.unit_std != after.unit_std:
        return f"the values are in different units ({before.unit_std}, {after.unit_std})"
    if before.value_std == 0:
        return "the earlier value is 0, so a change can't be expressed in %"
    return None
