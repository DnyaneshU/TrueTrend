"""Answering a question she asks in her own words, without ever inventing a number.

    arogya-ask "माझी साखर वाढली आहे का?"        (or: python -m arogya_vahi.ask)

This is the part of the app she talks to, so it is also where the project's one promise
is easiest to break. It is kept by taking the writing away from the model entirely:

    Gemma reads the question and answers only two things -- which test she means, and
    what she is asking about it (the latest value, whether it changed, the range, the
    history). That answer is a choice from a fixed list, checked against the catalog.

    Code then builds the sentence from saved, verified results, through the same
    templates the summary uses, with every number inserted by arogya_vahi.marathi.

So the model never sees a number and never writes one. The worst a wrong reading of the
question can do is answer about the wrong test -- which she can see, because the answer
names the test, the date and the lab it came from.

A question it cannot place is not guessed at: it says it did not understand, and offers
the tests this person actually has.
"""

import argparse
import logging
import sqlite3
import sys
from contextlib import closing
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field

from arogya_vahi import cli, db, gemma, marathi
from arogya_vahi.change import every_timeline
from arogya_vahi.config import settings
from arogya_vahi.errors import UserError
from arogya_vahi.lab_tests import CATALOG
from arogya_vahi.models import Change, Timeline, TimelinePoint
from arogya_vahi.resources import load_toml, read_text

logger = logging.getLogger(__name__)

# What a question can be about. Each one has a template in data/ask_mr.toml, and code
# that fills it from saved results: adding one means adding both, never a free sentence.
Topic = Literal[
    "latest",  # "what is my sugar now?"
    "changed",  # "has it gone up?"
    "in_range",  # "is it normal?"
    "history",  # "what has it been doing?"
    "when",  # "when was I last tested?"
]
TOPICS: tuple[str, ...] = get_args(Topic)


class Reading(BaseModel):
    """What Gemma understood the question to be: a test, and what is asked about it.

    No docstrings on the fields: this model's schema is sent to Gemma, and the schema
    must stay the one that was tested.
    """

    test_code: str | None
    topic: Topic | None


class Answer(BaseModel):
    """What the app says back, and everything it is built from."""

    model_config = ConfigDict(frozen=True)

    question: str  # as she asked it
    understood: bool  # False: the app says so rather than guessing
    test_code: str | None = None
    test_name: str | None = None  # in Marathi, as the answer says it
    topic: Topic | None = None
    sentences: list[str] = Field(default_factory=list)  # Marathi, every number from a report
    points: list[TimelinePoint] = Field(default_factory=list)  # what the answer is built from
    suggestions: list[str] = Field(default_factory=list)  # tests this person has, when lost


class Phrases(BaseModel):
    """data/ask_mr.toml: what the answer can say. Every number in them is a placeholder."""

    model_config = ConfigDict(frozen=True)

    answers: dict[str, str]
    said: dict[str, str]

    @classmethod
    def load(cls) -> "Phrases":
        return cls.model_validate(load_toml("ask_mr.toml"))


PHRASES = Phrases.load()


def _known(code: str) -> bool:
    """Whether the catalog has this test code: Gemma can reply with anything at all."""
    return any(test.code == code for test in CATALOG.tests)


def _value(point: TimelinePoint) -> str:
    """A saved value exactly as its report prints it, in Devanagari digits."""
    unit = point.unit or point.unit_std or ""
    return f"{marathi.digits(point.value_text)} {unit}".strip()


def _say(key: str, **slots: str) -> str:
    """One phrase, with its placeholders filled by code."""
    return PHRASES.answers[key].format(**slots)


def _latest(points: list[TimelinePoint]) -> str:
    last = points[-1]
    return _say("latest", value=_value(last), date=marathi.day(last.sample_date), lab=last.lab_name or "")


def _changed(points: list[TimelinePoint], changes: list[Change]) -> list[str]:
    """What the newest comparison says, in the words the summary already uses."""
    if not changes:
        return [_say("only_one")]
    change = changes[-1]
    said = PHRASES.said[change.kind]  # "वाढला आहे" / "कमी झाला आहे" / "तेवढाच आहे"
    lines = [
        _say(
            "changed",
            before=_value(change.before),
            before_date=marathi.day(change.before.sample_date),
            after=_value(change.after),
            said=said,
        )
    ]
    if change.kind == "within_normal_variation":
        lines.append(_say("within_variation"))
    elif change.kind == "not_judged":
        lines.append(_say("not_judged"))
    return lines


def _in_range(points: list[TimelinePoint]) -> list[str]:
    """Against the lab's own printed range -- never against anyone else's idea of normal."""
    last = points[-1]
    if not last.ref_verified or last.value_std is None:
        return [_latest(points), _say("no_range")]
    low, high = last.ref_low, last.ref_high
    if high is not None and last.value_std > high:
        return [_say("above", value=_value(last), limit=marathi.number(high))]
    if low is not None and last.value_std < low:
        return [_say("below", value=_value(last), limit=marathi.number(low))]
    return [_say("inside", value=_value(last), range=marathi.digits(last.ref_text or ""))]


def _history(points: list[TimelinePoint]) -> list[str]:
    """Every reading, oldest first, each with its date: the whole answer is quotation."""
    said = ", ".join(f"{marathi.day(p.sample_date)} — {_value(p)}" for p in points)
    return [_say("history", count=marathi.number(len(points)), readings=said)]


def _when(points: list[TimelinePoint]) -> list[str]:
    last = points[-1]
    return [_say("when", date=marathi.day(last.sample_date), lab=last.lab_name or "")]


def _answer_about(topic: str, timeline: Timeline) -> list[str]:
    """The sentences for one topic, built from this test's saved results."""
    points = timeline.points
    if topic == "latest":
        return [_latest(points)]
    if topic == "changed":
        return _changed(points, timeline.changes)
    if topic == "in_range":
        return _in_range(points)
    if topic == "history":
        return _history(points)
    return _when(points)


def read_question(question: str, model: str | None = None) -> Reading:
    """What Gemma makes of the question: which test, and what is asked. No numbers."""
    tests = "\n".join(f"- {test.code}: {test.name} ({test.name_mr})" for test in CATALOG.tests)
    prompt = read_text("prompts", "ask_question.txt").format(
        tests=tests, topics="\n".join(f"- {topic}" for topic in TOPICS), question=question
    )
    reply = gemma.ask_json(prompt, Reading, model=model or settings.model)
    return reply or Reading(test_code=None, topic=None)


def answer(
    conn: sqlite3.Connection, question: str, patient_id: int | None = None, model: str | None = None
) -> Answer:
    """Her question, answered only from results this app has found in her reports."""
    if not question.strip():
        raise UserError("Please type a question.")

    patient_id = patient_id if patient_id is not None else db.latest_patient_id(conn)
    timelines = every_timeline(db.timeline_points(conn, patient_id))
    have = {timeline.code: timeline for timeline in timelines}

    read = read_question(question, model)
    timeline = have.get(read.test_code or "")
    lost = Answer(
        question=question,
        understood=False,
        suggestions=[t.name_mr for t in timelines],  # what she can actually ask about
    )

    if read.topic == "when" and read.test_code is None and timelines:
        # "When was I last tested?" names no test, and does not need one: it is about
        # the newest report there is.
        newest = max((p for line in timelines for p in line.points), key=lambda p: p.sample_date)
        return Answer(
            question=question,
            understood=True,
            topic="when",
            sentences=[_say("when", date=marathi.day(newest.sample_date), lab=newest.lab_name or "")],
            points=[newest],
        )

    if read.test_code is None or read.topic is None:
        # The question could not be placed. Saying so is the right answer; guessing a
        # test and quoting its numbers would answer a question she did not ask.
        return lost.model_copy(update={"sentences": [_say("not_understood")]})

    if timeline is None:
        # The test was understood, but there is no report of it: a different thing, and
        # she should hear which, or she will think the app did not follow her.
        named = CATALOG.test(read.test_code).name_mr if _known(read.test_code) else None
        said = _say("no_results")
        return lost.model_copy(
            update={"sentences": [f"{named}: {said}" if named else said], "test_code": read.test_code}
        )

    return Answer(
        question=question,
        understood=True,
        test_code=timeline.code,
        test_name=timeline.name_mr,
        topic=read.topic,
        sentences=[f"{timeline.name_mr}: {line}" for line in _answer_about(read.topic, timeline)],
        points=timeline.points,
    )


def _command(args: argparse.Namespace) -> int:
    with closing(db.connect()) as conn:
        said = answer(conn, " ".join(args.question), patient_id=args.patient, model=args.model)
    if args.json:
        print(said.model_dump_json(indent=2))
        return 0
    for line in said.sentences:
        print(line)
    if said.suggestions:
        print("\n" + _say("you_could_ask") + " " + ", ".join(said.suggestions))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = cli.parser("ask", "Ask a question about the saved reports, in Marathi or English.")
    parser.add_argument("question", nargs="+", help="the question, in her own words")
    parser.add_argument("--patient", type=int, help="whose reports to answer about")
    parser.add_argument("--model", default=settings.model, help=f"Ollama model (default: {settings.model})")
    parser.add_argument("--json", action="store_true", help="print the whole answer as JSON")
    return cli.run_command(parser, _command, argv)


if __name__ == "__main__":
    sys.exit(main())
