"""Answering a question she asks in her own words, without ever inventing a number.

    truetrend-ask "माझी साखर वाढली आहे का?"        (or: python -m truetrend.ask)

This is the part of the app she talks to, so it is also where the project's one promise
is easiest to break. It is kept by taking the writing away from the model entirely:

    Gemma reads the question and answers only two things -- which test she means, and
    what she is asking about it (the latest value, whether it changed, the range, the
    history). That answer is a choice from a fixed list, checked against the catalog.

    Code then builds the sentence from saved, verified results, through the same
    templates the summary uses, with every number inserted by truetrend.marathi.

So the model never sees a number and never writes one. The worst a wrong reading of the
question can do is answer about the wrong test -- which she can see, because the answer
names the test, the date and the lab it came from.

A question it cannot place is not guessed at: it says it did not understand, and offers
the tests this person actually has.

The answer comes back in the language the question was asked in: she asks in Marathi and
is answered in Marathi, and anyone checking the app in English is answered in English.
The two sets of phrases are data/ask_mr.toml and data/ask_en.toml, and they must carry
the same keys and the same placeholders -- there is a test for that.
"""

import argparse
import logging
import re
import sqlite3
import sys
from contextlib import closing
from datetime import date
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field

from truetrend import cli, db, gemma, marathi
from truetrend.change import every_timeline
from truetrend.config import settings
from truetrend.errors import UserError
from truetrend.lab_tests import CATALOG
from truetrend.models import Change, Timeline, TimelinePoint
from truetrend.resources import load_toml, read_text

logger = logging.getLogger(__name__)

# What a question can be about. Each one has a template in data/ask_mr.toml, and code
# that fills it from saved results: adding one means adding both, never a free sentence.
Language = Literal["mr", "en"]  # answered in the language the question was asked in

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
    language: Language = "mr"  # the language it is answered in: the one it was asked in
    test_code: str | None = None
    test_name: str | None = None  # as the answer names it
    topic: Topic | None = None
    sentences: list[str] = Field(default_factory=list)  # every number in them is from a report
    points: list[TimelinePoint] = Field(default_factory=list)  # what the answer is built from
    suggestions: list[str] = Field(default_factory=list)  # tests this person has, when lost


class Phrases(BaseModel):
    """One language's worth of what the answer can say. Every number in them is a placeholder."""

    model_config = ConfigDict(frozen=True)

    answers: dict[str, str]
    said: dict[str, str]

    @classmethod
    def load(cls, language: str) -> "Phrases":
        return cls.model_validate(load_toml(f"ask_{language}.toml"))


PHRASES: dict[str, Phrases] = {language: Phrases.load(language) for language in get_args(Language)}
# Month names that never change with the computer's locale.
_MONTHS_EN = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_DEVANAGARI = re.compile(r"[\u0900-\u097f]")


def language_of(question: str) -> Language:
    """Which language to answer in: the one the question is written in.

    One Devanagari letter is enough. A question typed in Marathi often carries an English
    test name ("HbA1c वाढलं का?"), and it should still be answered in Marathi; an English
    question has no Devanagari in it at all.
    """
    return "mr" if _DEVANAGARI.search(question) else "en"


class Voice:
    """The phrases and the number-writing of one language, so nothing has to pass a flag."""

    def __init__(self, language: Language):
        self.language = language
        self.phrases = PHRASES[language]

    def say(self, key: str, **slots: str) -> str:
        """One phrase, with its placeholders filled by code."""
        return self.phrases.answers[key].format(**slots)

    def moved(self, kind: str) -> str:
        """Which way a value moved: "वाढला आहे", or "gone up"."""
        return self.phrases.said[kind]

    def number(self, text: str) -> str:
        """A number as this language writes it: Devanagari digits for Marathi."""
        return marathi.digits(text) if self.language == "mr" else text

    def day(self, when: date) -> str:
        """A date as this language writes it: "१५ एप्रिल २०२६", or "15 Apr 2026"."""
        if self.language == "mr":
            return marathi.day(when)
        # Built by hand rather than with strftime: "%-d" is not portable, and the month
        # names must not change with the computer's locale.
        return f"{when.day} {_MONTHS_EN[when.month - 1]} {when.year}"

    def value(self, point: TimelinePoint) -> str:
        """A saved value exactly as its report prints it."""
        unit = point.unit or point.unit_std or ""
        return f"{self.number(point.value_text)} {unit}".strip()

    def test_name(self, code: str) -> str:
        """How this language names a test."""
        test = CATALOG.test(code)
        return test.name_mr if self.language == "mr" else test.name


def _known(code: str) -> bool:
    """Whether the catalog has this test code: Gemma can reply with anything at all."""
    return any(test.code == code for test in CATALOG.tests)


def _latest(voice: Voice, points: list[TimelinePoint]) -> str:
    last = points[-1]
    return voice.say(
        "latest", value=voice.value(last), date=voice.day(last.sample_date), lab=last.lab_name or ""
    )


def _changed(voice: Voice, points: list[TimelinePoint], changes: list[Change]) -> list[str]:
    """What the newest comparison says, in the words the summary already uses."""
    if not changes:
        return [voice.say("only_one")]
    change = changes[-1]
    lines = [
        voice.say(
            "changed",
            before=voice.value(change.before),
            before_date=voice.day(change.before.sample_date),
            after=voice.value(change.after),
            said=voice.moved(change.kind),
        )
    ]
    if change.kind == "within_normal_variation":
        lines.append(voice.say("within_variation"))
    elif change.kind == "not_judged":
        lines.append(voice.say("not_judged"))
    return lines


def _in_range(voice: Voice, points: list[TimelinePoint]) -> list[str]:
    """Against the lab's own printed range -- never against anyone else's idea of normal."""
    last = points[-1]
    if not last.ref_verified or last.value_std is None:
        return [_latest(voice, points), voice.say("no_range")]
    low, high = last.ref_low, last.ref_high
    if high is not None and last.value_std > high:
        return [voice.say("above", value=voice.value(last), limit=voice.number(_plain(high)))]
    if low is not None and last.value_std < low:
        return [voice.say("below", value=voice.value(last), limit=voice.number(_plain(low)))]
    return [voice.say("inside", value=voice.value(last), range=voice.number(last.ref_text or ""))]


def _history(voice: Voice, points: list[TimelinePoint]) -> list[str]:
    """Every reading, oldest first, each with its date: the whole answer is quotation."""
    said = ", ".join(f"{voice.day(p.sample_date)} — {voice.value(p)}" for p in points)
    return [voice.say("history", count=voice.number(str(len(points))), readings=said)]


def _when(voice: Voice, points: list[TimelinePoint]) -> list[str]:
    last = points[-1]
    return [voice.say("when", date=voice.day(last.sample_date), lab=last.lab_name or "")]


def _plain(value: float) -> str:
    """A number without trailing zeros: 5.60 -> "5.6"."""
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return text or "0"


def _answer_about(voice: Voice, topic: str, timeline: Timeline) -> list[str]:
    """The sentences for one topic, built from this test's saved results."""
    points = timeline.points
    if topic == "latest":
        return [_latest(voice, points)]
    if topic == "changed":
        return _changed(voice, points, timeline.changes)
    if topic == "in_range":
        return _in_range(voice, points)
    if topic == "history":
        return _history(voice, points)
    return _when(voice, points)


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

    voice = Voice(language_of(question))  # answered in the language it was asked in
    patient_id = patient_id if patient_id is not None else db.latest_patient_id(conn)
    timelines = every_timeline(db.timeline_points(conn, patient_id))
    have = {timeline.code: timeline for timeline in timelines}

    read = read_question(question, model)
    timeline = have.get(read.test_code or "")
    lost = Answer(
        question=question,
        understood=False,
        language=voice.language,
        # What she can actually ask about, named the way this answer names things.
        suggestions=[voice.test_name(t.code) for t in timelines],
    )

    if read.topic == "when" and read.test_code is None and timelines:
        # "When was I last tested?" names no test, and does not need one: it is about
        # the newest report there is.
        newest = max((p for line in timelines for p in line.points), key=lambda p: p.sample_date)
        return Answer(
            question=question,
            understood=True,
            language=voice.language,
            topic="when",
            sentences=_when(voice, [newest]),
            points=[newest],
        )

    if read.test_code is None or read.topic is None:
        # The question could not be placed. Saying so is the right answer; guessing a
        # test and quoting its numbers would answer a question she did not ask.
        return lost.model_copy(update={"sentences": [voice.say("not_understood")]})

    if timeline is None:
        # The test was understood, but there is no report of it: a different thing, and
        # she should hear which, or she will think the app did not follow her.
        named = voice.test_name(read.test_code) if _known(read.test_code) else None
        said = voice.say("no_results")
        return lost.model_copy(
            update={"sentences": [f"{named}: {said}" if named else said], "test_code": read.test_code}
        )

    named = voice.test_name(timeline.code)
    return Answer(
        question=question,
        understood=True,
        language=voice.language,
        test_code=timeline.code,
        test_name=named,
        topic=read.topic,
        sentences=[f"{named}: {line}" for line in _answer_about(voice, read.topic, timeline)],
        points=timeline.points,
    )


def _command(args: argparse.Namespace) -> int:
    question = " ".join(args.question)
    with closing(db.connect()) as conn:
        said = answer(conn, question, patient_id=args.patient, model=args.model)
    if args.json:
        print(said.model_dump_json(indent=2))
        return 0
    for line in said.sentences:
        print(line)
    if said.suggestions:
        print("\n" + Voice(said.language).say("you_could_ask") + " " + ", ".join(said.suggestions))
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
