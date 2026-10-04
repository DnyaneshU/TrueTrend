"""Asking a question in her own words, and the promise that the answer invents nothing."""

import re

import pytest
from factories import saved_report, saved_result

from arogya_vahi import ask, db
from arogya_vahi.errors import UserError
from arogya_vahi.models import Patient

DIGIT = re.compile(r"[0-9०-९]")  # Latin or Devanagari


class FakeReading:
    """Stands in for Gemma: says what it was told to say, and never sees a number."""

    def __init__(self, test_code, topic):
        self.reading = ask.Reading(test_code=test_code, topic=topic)
        self.prompts = []

    def __call__(self, prompt, schema, model):
        self.prompts.append(prompt)
        return self.reading


@pytest.fixture
def reads(monkeypatch):
    """Make Gemma read every question as (test, topic), and keep the prompt it was sent."""

    def install(test_code, topic):
        fake = FakeReading(test_code, topic)
        monkeypatch.setattr(ask.gemma, "ask_json", fake)
        return fake

    return install


@pytest.fixture
def sugar(conn):
    """Three HbA1c reports for one person, at one lab, rising."""
    patient = db.add_patient(conn, Patient(id=0, display_name="Sunita Patil"))
    for sha, date, value in (("a", "2026-01-12", 6.8), ("b", "2026-05-20", 7.6), ("c", "2026-09-28", 8.9)):
        saved_report(
            conn,
            sha,
            [
                saved_result(
                    "HBA1C", value, ref_text="4.0 - 5.6", ref_low=4.0, ref_high=5.6, ref_verified=True
                )
            ],
            sample_date=date,
            patient_id=patient.id,
        )
    return patient


# ---------------------------------------------------------------- the promise


def test_no_phrase_the_app_can_say_contains_a_number_of_its_own(reads):
    # Every number in an answer must come from a report. A digit written into a phrase
    # here would be spoken as fact without ever having been in one.
    for key, phrase in ask.PHRASES.answers.items():
        assert not DIGIT.search(phrase), f"answers.{key} has a digit of its own"
    for key, phrase in ask.PHRASES.said.items():
        assert not DIGIT.search(phrase), f"said.{key} has a digit of its own"


def test_gemma_is_never_shown_a_value(reads, conn, sugar):
    fake = reads("HBA1C", "latest")
    ask.answer(conn, "माझी साखर किती आहे?")
    (prompt,) = fake.prompts
    assert "6.8" not in prompt and "8.9" not in prompt  # it reads the question, not the reports


def test_every_number_in_an_answer_is_printed_in_a_report(reads, conn, sugar):
    reads("HBA1C", "history")
    said = ask.answer(conn, "सगळे रिपोर्ट दाखवा")
    printed = {p.value_text for p in said.points} | {str(p.sample_date.year) for p in said.points}
    for sentence in said.sentences:
        for number in re.findall(r"[०-९]+(?:\.[०-९]+)?", sentence):
            latin = number.translate(str.maketrans("०१२३४५६७८९", "0123456789"))
            # every number said is a value, a date part, or the count of reports
            assert latin in printed or len(latin) <= 4, f"{latin} is not from a report"


# ---------------------------------------------------------------- what it answers


def test_it_says_the_latest_reading_with_its_date_and_lab(reads, conn, sugar):
    reads("HBA1C", "latest")
    said = ask.answer(conn, "आता किती आहे?")
    assert said.understood and said.test_code == "HBA1C"
    assert "८.९" in said.sentences[0] and "२८ सप्टेंबर २०२६" in said.sentences[0]


def test_it_says_whether_the_value_really_changed(reads, conn, sugar):
    reads("HBA1C", "changed")
    said = ask.answer(conn, "वाढली आहे का?")
    assert "७.६" in said.sentences[0] and "८.९" in said.sentences[0]
    assert "वाढला आहे" in said.sentences[0]


def test_a_change_inside_the_usual_variation_is_said_to_be_so(reads, conn):
    patient = db.add_patient(conn, Patient(id=0, display_name="Sunita Patil"))
    for sha, date, value in (("a", "2026-01-12", 7.0), ("b", "2026-05-20", 7.1)):
        saved_report(conn, sha, [saved_result("HBA1C", value)], sample_date=date, patient_id=patient.id)
    reads("HBA1C", "changed")
    said = ask.answer(conn, "बदललं का?")
    assert any("नेहमीच्या चढ-उतारातच" in line for line in said.sentences)


def test_it_compares_only_with_the_labs_own_printed_range(reads, conn, sugar):
    reads("HBA1C", "in_range")
    said = ask.answer(conn, "नॉर्मल आहे का?")
    assert "वरच्या मर्यादेपेक्षा" in said.sentences[0] and "५.६" in said.sentences[0]


def test_with_no_printed_range_it_says_so_rather_than_judging(reads, conn):
    patient = db.add_patient(conn, Patient(id=0, display_name="Sunita Patil"))
    saved_report(
        conn,
        "a",
        [saved_result("HBA1C", 8.9, ref_verified=False)],
        sample_date="2026-01-12",
        patient_id=patient.id,
    )
    reads("HBA1C", "in_range")
    said = ask.answer(conn, "नॉर्मल आहे का?")
    assert any("मर्यादा छापलेली सापडली नाही" in line for line in said.sentences)


def test_one_report_cannot_be_compared_and_it_says_that(reads, conn):
    patient = db.add_patient(conn, Patient(id=0, display_name="Sunita Patil"))
    saved_report(conn, "a", [saved_result("HBA1C", 8.9)], sample_date="2026-01-12", patient_id=patient.id)
    reads("HBA1C", "changed")
    said = ask.answer(conn, "वाढली का?")
    assert "एकच रिपोर्ट" in said.sentences[0]


def test_the_history_lists_every_reading_with_its_date(reads, conn, sugar):
    reads("HBA1C", "history")
    said = ask.answer(conn, "सगळं दाखवा")
    for value in ("६.८", "७.६", "८.९"):
        assert value in said.sentences[0]


# ---------------------------------------------------------------- when it cannot answer


def test_a_question_it_cannot_place_is_not_guessed_at(reads, conn, sugar):
    reads(None, None)
    said = ask.answer(conn, "आज जेवायला काय करू?")
    assert not said.understood
    assert "समजला नाही" in said.sentences[0]
    assert said.suggestions == ["HbA1c (सरासरी साखर)"]  # what she can actually ask about


def test_a_test_with_no_report_says_so_instead_of_an_empty_answer(reads, conn, sugar):
    reads("TSH", "latest")  # read correctly, but there is no TSH report
    said = ask.answer(conn, "थायरॉईड कसं आहे?")
    assert not said.understood and "अजून या वहीत नाही" in said.sentences[0]


def test_a_topic_outside_the_list_cannot_even_be_read(conn, sugar):
    # The schema sent to Gemma allows only the five topics, so a sixth never reaches the
    # code that builds sentences: there is no path from a made-up topic to a said number.
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        ask.Reading(test_code="HBA1C", topic="what_should_i_eat")


def test_an_empty_question_is_refused(conn):
    with pytest.raises(UserError, match="type a question"):
        ask.answer(conn, "   ")


def test_a_reply_gemma_could_not_make_sense_of_is_not_an_answer(conn, sugar, monkeypatch):
    monkeypatch.setattr(ask.gemma, "ask_json", lambda prompt, schema, model: None)
    said = ask.answer(conn, "काहीतरी")
    assert not said.understood


def test_when_was_my_last_test_needs_no_test_named(reads, conn, sugar):
    reads(None, "when")  # a real question that names no test
    said = ask.answer(conn, "शेवटची तपासणी कधी झाली?")
    assert said.understood and "२८ सप्टेंबर २०२६" in said.sentences[0]
