"""Command line: the Marathi summary of the latest report, made by rules over verified results.

    python -m app.summary [--json]

At most 3 sentences, most important first, each with a question for the doctor:

1. a real change in the same direction three sample dates in a row,
2. a real change since the previous sample (app.change),
3. a result outside that lab's printed normal range, when the range was verified too.

Sentences are templates (data/summary_mr.toml) whose numbers are placeholders, filled
in by code with results exactly as the report prints them. A result that needs checking
is never stated; the summary only says how many there are.
"""

import argparse
import logging
import sys
import tomllib
from collections import Counter
from collections.abc import Iterable
from contextlib import closing
from string import Formatter

from pydantic import BaseModel, ConfigDict, field_validator

from app import db, marathi
from app.change import timeline_changes, timelines
from app.config import settings
from app.console import configure_console
from app.lab_tests import CATALOG, normalise_name
from app.models import Change, Finding, FindingKind, Summary, TimelinePoint
from app.normalize import parse_range

logger = logging.getLogger(__name__)

MAX_SENTENCES = 3
PRIORITY: tuple[FindingKind, ...] = (
    "trend_increase", "trend_decrease", "real_increase", "real_decrease", "above_range", "below_range",
)  # fmt: skip
TREND: dict[str, FindingKind] = {"real_increase": "trend_increase", "real_decrease": "trend_decrease"}
_CATALOG_ORDER = {test.code: index for index, test in enumerate(CATALOG.tests)}


def slots(template: str) -> Counter[str] | None:
    """The {placeholders} of a template and how often each appears; None if it has a digit or bad braces."""
    try:
        parsed = list(Formatter().parse(template))
    except ValueError:
        return None
    if any(char.isdigit() for literal, *_ in parsed for char in literal):
        return None
    if any(spec or conversion for _, field, spec, conversion in parsed if field is not None):
        return None
    return Counter(field for _, field, _, _ in parsed if field is not None)


class Templates(BaseModel):
    """data/summary_mr.toml: the summary's sentences, its doctor questions and labels."""

    model_config = ConfigDict(frozen=True)

    sentences: dict[str, str]
    questions: dict[FindingKind, str]
    labels: dict[str, str]

    @field_validator("sentences", "questions", "labels")
    @classmethod
    def _numbers_come_from_code(cls, templates: dict[str, str]) -> dict[str, str]:
        for name, template in templates.items():
            if slots(template) is None:
                raise ValueError(f"template {name!r} has a digit or a malformed placeholder")
        return templates

    @classmethod
    def load(cls) -> "Templates":
        with (settings.data_dir / "summary_mr.toml").open("rb") as file:
            return cls.model_validate(tomllib.load(file))


TEMPLATES = Templates.load()


def summarise(points: Iterable[TimelinePoint]) -> Summary:
    """The summary of the latest report, judged against the same person's earlier results."""
    points, others = _latest_persons(_known_tests(points))
    by_test = timelines(points)
    if not by_test:
        return Summary(other_people=others)
    changes = {code: timeline_changes(line, CATALOG.test(code)) for code, line in by_test.items()}
    latest_date = max(line[-1].sample_date for line in by_test.values())
    current = {code: line for code, line in by_test.items() if line[-1].sample_date == latest_date}

    findings = [finding for code in current if (finding := _finding(current[code], changes[code]))]
    findings.sort(key=lambda finding: (PRIORITY.index(finding.kind), _CATALOG_ORDER[finding.test_code]))
    to_check = sum(line[-1].status != "verified" for line in current.values())

    told = findings[: MAX_SENTENCES - 1] if to_check else findings[:MAX_SENTENCES]
    sentences = [TEMPLATES.sentences[finding.kind].format(**finding.slots) for finding in told]
    if to_check == 1:
        sentences.append(TEMPLATES.sentences["to_check_one"])
    elif to_check:
        sentences.append(TEMPLATES.sentences["to_check"].format(count=marathi.number(to_check)))
    if not sentences:
        dates = {point.sample_date for line in by_test.values() for point in line}
        sentences.append(TEMPLATES.sentences[_quiet(current, changes, len(dates))])
    return Summary(
        latest_sample_date=latest_date,
        sentences=sentences,
        questions=[TEMPLATES.questions[finding.kind].format(**finding.slots) for finding in told],
        findings=findings,
        to_check=to_check,
        changes=[change for test_changes in changes.values() for change in test_changes],
        other_people=others,
    )


def _known_tests(points: Iterable[TimelinePoint]) -> list[TimelinePoint]:
    """Points of tests in the catalog; results of a test since removed from it are left out."""
    kept, unknown = [], set()
    for point in points:
        if point.test_code in _CATALOG_ORDER:
            kept.append(point)
        else:
            unknown.add(point.test_code)
    if unknown:
        logger.warning("Left out results of tests no longer in the catalog: %s", ", ".join(sorted(unknown)))
    return kept


def _latest_persons(points: list[TimelinePoint]) -> tuple[list[TimelinePoint], list[str]]:
    """The points of the person named on the latest report, and the other names left out.

    Until reports are matched to patients, printed names are compared ignoring case and
    punctuation; a report without a name is kept.
    """
    if not points:
        return [], []
    latest = max(points, key=lambda point: (point.sample_date, point.report_id))
    person = normalise_name(latest.patient_name) if latest.patient_name else None
    kept, others = [], {}
    for point in points:
        name = normalise_name(point.patient_name) if point.patient_name else None
        if person is None or name is None or name == person:
            kept.append(point)
        else:
            others.setdefault(name, point.patient_name)
    return kept, sorted(others.values())


def _finding(timeline: list[TimelinePoint], changes: list[Change]) -> Finding | None:
    """The most important thing to say about one test, from its latest verified result."""
    latest = timeline[-1]
    if latest.status != "verified":
        return None
    named = {"test": CATALOG.test(latest.test_code).name_mr, "after": _shown(latest)}
    last = changes[-1].kind if changes else None
    if last in TREND and len(changes) >= 2 and changes[-2].kind == last:  # three sample dates in a row
        first = changes[-2].before
        values = {**named, "first": _shown(first), "first_date": marathi.day(first.sample_date)}
        return Finding(kind=TREND[last], test_code=latest.test_code, slots=values)
    if last in TREND:
        before = changes[-1].before
        values = {**named, "before": _shown(before), "before_date": marathi.day(before.sample_date)}
        return Finding(kind=last, test_code=latest.test_code, slots=values)
    if outside := _outside_range(latest):
        kind, limit = outside
        values = {**named, "limit": _with_unit(marathi.number(limit), latest.unit)}
        return Finding(kind=kind, test_code=latest.test_code, slots=values)
    return None


def _outside_range(point: TimelinePoint) -> tuple[FindingKind, float] | None:
    """The kind and printed limit when a value is outside its lab's verified normal range.

    Compared in the standard unit. "< 148" and "< 187" are both below a lower limit of 187,
    but "<= 187" may be 187. A limit says nothing about the other side of the range.
    """
    if not point.ref_verified or point.value_std is None:
        return None
    printed_low, printed_high = parse_range(point.ref_text) or (None, None)
    value, low, high, qualifier = point.value_std, point.ref_low, point.ref_high, point.qualifier
    at_most, at_least = qualifier in ("<", "<="), qualifier in (">", ">=")
    if high is not None and not at_most and (value > high or (qualifier == ">" and value >= high)):
        return "above_range", printed_high
    if low is not None and not at_least and (value < low or (qualifier == "<" and value <= low)):
        return "below_range", printed_low
    return None


def _shown(point: TimelinePoint) -> str:
    """A result as its report prints it, in Devanagari digits: '१४१ mg/dL', '< १४८ pg/mL'."""
    number = marathi.number(point.value)
    return _with_unit(f"{point.qualifier} {number}" if point.qualifier else number, point.unit)


def _with_unit(number: str, unit: str | None) -> str:
    return f"{number} {unit}" if unit else number


def _quiet(current: dict[str, list[TimelinePoint]], changes: dict[str, list[Change]], dates: int) -> str:
    """Which sentence to say when nothing stands out."""
    if dates == 1:
        return "first_report"
    latest = [changes[code][-1].kind for code in current if changes[code]]
    if latest and all(kind == "within_normal_variation" for kind in latest):
        return "stable"
    if "within_normal_variation" in latest:
        return "partly_stable"
    return "not_compared"


def main(argv: list[str] | None = None) -> int:
    configure_console()
    parser = argparse.ArgumentParser(
        prog="python -m app.summary",
        description="The Marathi summary of the latest saved report and questions for the doctor.",
    )
    parser.add_argument("--json", action="store_true", help="print the summary, findings and changes as JSON")
    args = parser.parse_args(argv)

    with closing(db.connect()) as conn:
        summary = summarise(db.timeline_points(conn))
    if summary.other_people:
        logger.warning(
            "Left out reports of %s: the summary is for the person on the latest report.",
            ", ".join(summary.other_people),
        )
    if args.json:
        print(summary.model_dump_json(indent=2))
    elif summary.latest_sample_date is None:
        logger.info("No saved report has a sample date yet; extract one with python -m app.extract.")
    else:
        print("\n".join(summary.sentences))
        if summary.questions:
            print(f"\n{TEMPLATES.labels['ask_doctor']}")
            print("\n".join(f"- {question}" for question in summary.questions))
    return 0


if __name__ == "__main__":
    sys.exit(main())
