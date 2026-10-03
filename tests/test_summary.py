"""The Marathi summary: rules over verified results, numbers filled in by code only.

Changes are judged with the catalog's real, sourced constants: HbA1c from one lab is a
real change above +4.2 % or below -4.1 %; post-prandial glucose has none and is never judged.
"""

import json
import re
import typing
from contextlib import closing
from datetime import date

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app import db, marathi
from app.lab_tests import CATALOG
from app.models import FindingKind, ReportRecord, SavedResult, TimelinePoint
from app.summary import TEMPLATES, Templates, main, slots, summarise

DEVANAGARI_NUMBER = re.compile(r"[०-९]+(?:\.[०-९]+)?")


def point(code, value, month=1, low=None, high=None, **overrides):
    unit = CATALOG.test(code).unit
    if low is not None and high is not None:
        ref_text = f"{low:g} - {high:g}"
    elif low is not None or high is not None:
        ref_text = f"> {low:g}" if low is not None else f"< {high:g}"
    else:
        ref_text = None
    fields = {
        "result_id": month * 100 + len(code), "report_id": month, "patient_name": "Sunita Patil",
        "test_code": code, "sample_date": date(2026, month, 15), "lab_name": "Sunrise",
        "value_text": f"{value}", "value": value, "unit": unit, "qualifier": None, "value_std": value,
        "unit_std": unit, "ref_text": ref_text, "ref_low": low, "ref_high": high, "ref_verified": True,
        "status": "verified", "page": 1, "file_path": "storage/a.pdf",
    }  # fmt: skip
    return TimelinePoint(**{**fields, **overrides})


# ---------------------------------------------------------------- templates


def test_slots_counts_placeholders():
    assert slots("{test}: {before} ते {after}") == {"test": 1, "before": 1, "after": 1}


@pytest.mark.parametrize("template", ["{test} ७ आहे", "{test} 7 आहे", "{test!r}", "{after:.2f}", "{test"])
def test_a_template_with_a_number_or_odd_placeholder_is_refused(template):
    assert slots(template) is None


def test_every_finding_has_a_sentence_and_a_question():
    kinds = set(typing.get_args(FindingKind))
    assert kinds <= set(TEMPLATES.sentences) and kinds == set(TEMPLATES.questions)


def test_templates_with_digits_do_not_load():
    with pytest.raises(ValueError, match="has a digit"):
        Templates.model_validate({"sentences": {"x": "{test} 7"}, "questions": {}, "labels": {}})


# ---------------------------------------------------------------- what is said


def test_no_reports_no_summary():
    assert summarise([]).sentences == []


def test_a_first_report_within_range_says_so():
    summary = summarise([point("HBA1C", 5.4, low=4.0, high=5.6)])
    assert (summary.sentences, summary.questions) == ([TEMPLATES.sentences["first_report"]], [])


def test_a_result_above_its_labs_range():
    summary = summarise([point("CHOL", 220.0, high=200.0)])
    assert summary.sentences == ["एकूण कोलेस्टेरॉल: आकडा २२० mg/dL आहे, लॅबच्या वरच्या मर्यादेपेक्षा (२०० mg/dL) जास्त."]
    assert summary.questions == ["एकूण कोलेस्टेरॉल: आकडा मर्यादेपेक्षा जास्त आहे, याबद्दल काय करावे?"]


def test_a_range_not_found_in_the_pdf_is_never_used():
    # the range Gemma read was not printed in the value's row: maybe "70-100" misread as "70-10"
    assert summarise([point("GLU_F", 90.0, low=70.0, high=10.0, ref_verified=False)]).findings == []


def test_values_are_shown_as_printed():
    # 7.8 mmol/L, compared as 140.5 mg/dL, is said as the report prints it
    glucose = point("GLU_F", 7.8, low=3.9, high=5.5, unit="mmol/L")
    glucose = glucose.model_copy(update={"value_std": 140.525, "ref_low": 70.2624, "ref_high": 99.088})
    (finding,) = summarise([glucose]).findings
    assert (finding.slots["after"], finding.slots["limit"]) == ("७.८ mmol/L", "५.५ mmol/L")


@pytest.mark.parametrize(
    "qualifier, value, kind",
    [
        ("<", 148.0, "below_range"),
        ("<", 187.0, "below_range"),
        ("<=", 187.0, None),
        ("<=", 150.0, "below_range"),
    ],
)
def test_a_value_printed_as_a_limit(qualifier, value, kind):
    b12 = point("B12", value, low=187.0, high=833.0, qualifier=qualifier, value_text=f"{qualifier} {value:g}")
    findings = summarise([b12]).findings
    assert [f.kind for f in findings] == ([kind] if kind else [])
    if kind:
        assert findings[0].slots["after"] == f"{qualifier} {marathi.number(value)} pg/mL"


def test_a_limit_says_nothing_about_the_other_side_of_the_range():
    assert summarise([point("TSH", 5.0, low=0.4, high=4.0, qualifier="<")]).findings == []


def test_a_real_increase():
    summary = summarise([point("HBA1C", 7.0, month=1), point("HBA1C", 7.6, month=4)])
    assert summary.sentences == [
        "HbA1c (सरासरी साखर): १५ जानेवारी २०२६ रोजी ७ %, आता ७.६ %. ही वाढ नेहमीच्या चढ-उतारापेक्षा जास्त आहे."
    ]
    assert summary.questions == ["HbA1c (सरासरी साखर): ही वाढ महत्त्वाची आहे का?"]


def test_three_sample_dates_rising_in_a_row_is_a_trend():
    summary = summarise(
        [point("HBA1C", 6.5, month=1), point("HBA1C", 7.0, month=4), point("HBA1C", 7.6, month=7)]
    )
    assert summary.findings[0].kind == "trend_increase"
    assert summary.sentences[0] == (
        "HbA1c (सरासरी साखर): गेल्या तीन रिपोर्टमध्ये आकडा सतत वाढत गेला आहे; १५ जानेवारी २०२६ रोजी ६.५ %, आता ७.६ %."
    )


def test_the_same_sample_uploaded_twice_is_not_a_trend():
    again = point("HBA1C", 7.6, month=4).model_copy(update={"report_id": 9, "result_id": 999})
    summary = summarise([point("HBA1C", 7.0, month=1), point("HBA1C", 7.6, month=4), again])
    assert [f.kind for f in summary.findings] == ["real_increase"]


def test_normal_variation_is_stable():
    summary = summarise([point("HBA1C", 7.0, month=1), point("HBA1C", 7.2, month=4)])  # +2.9 %
    assert summary.sentences == [TEMPLATES.sentences["stable"]]
    assert [c.kind for c in summary.changes] == ["within_normal_variation"]


def test_changes_that_cannot_be_judged_say_so():
    summary = summarise([point("GLU_PP", 120.0, month=1), point("GLU_PP", 300.0, month=4)])
    assert summary.sentences == [TEMPLATES.sentences["not_compared"]]


def test_stable_is_not_said_when_some_changes_were_not_judged():
    points = [point("HBA1C", 7.0, month=1), point("HBA1C", 7.2, month=4)]
    points += [point("GLU_PP", 120.0, month=1), point("GLU_PP", 300.0, month=4)]
    assert summarise(points).sentences == [TEMPLATES.sentences["partly_stable"]]


def test_a_new_test_on_a_later_report_is_not_called_a_first_report():
    summary = summarise([point("CHOL", 180.0, month=1), point("HBA1C", 5.4, month=4)])
    assert summary.sentences == [TEMPLATES.sentences["not_compared"]]


def test_a_result_that_needs_checking_is_counted_never_stated():
    summary = summarise([point("HBA1C", 7.0, month=1), point("HBA1C", 9.9, month=4, status="needs_check")])
    assert summary.to_check == 1 and summary.findings == []
    assert summary.sentences == [TEMPLATES.sentences["to_check_one"]]
    assert "९.९" not in " ".join(summary.sentences + summary.questions)


def test_several_results_to_check_are_counted():
    points = [point(code, 1.0, month=4, status="needs_check") for code in ("CHOL", "HDL")]
    assert summarise(points).sentences == ["या रिपोर्टमधील २ आकडे अजून मूळ रिपोर्टशी तपासायचे आहेत."]


def test_at_most_three_sentences_most_important_first():
    points = [
        point("CHOL", 250.0, month=4, high=200.0),
        point("TG", 250.0, month=4, high=150.0),
        point("LDL", 190.0, month=4, high=100.0),
        point("HBA1C", 7.0, month=1),
        point("HBA1C", 7.6, month=4),
    ]
    summary = summarise(points)
    assert [f.kind for f in summary.findings] == [
        "real_increase",
        "above_range",
        "above_range",
        "above_range",
    ]
    assert [f.test_code for f in summary.findings[1:]] == ["CHOL", "LDL", "TG"]  # catalog order
    assert len(summary.sentences) == len(summary.questions) == 3


def test_a_result_to_check_takes_the_last_sentence():
    points = [point(code, 300.0, month=4, high=200.0) for code in ("CHOL", "TG", "LDL")]
    points.append(point("HDL", 40.0, month=4, status="needs_check"))
    summary = summarise(points)
    assert len(summary.sentences) == 3 and len(summary.questions) == 2
    assert summary.sentences[-1] == TEMPLATES.sentences["to_check_one"]


def test_tests_missing_from_the_latest_report_are_not_summarised():
    summary = summarise([point("CHOL", 250.0, month=1, high=200.0), point("HBA1C", 5.4, month=4)])
    assert summary.findings == [] and summary.latest_sample_date == date(2026, 4, 15)


def test_another_persons_reports_are_left_out():
    father = point("HBA1C", 9.0, month=1, patient_name="Mr. Ramesh Patil")
    summary = summarise([father, point("HBA1C", 7.0, month=4, patient_name="SUNITA PATIL.")])
    assert summary.other_people == ["Mr. Ramesh Patil"]
    assert summary.changes == [] and summary.sentences == [TEMPLATES.sentences["first_report"]]


def test_a_test_no_longer_in_the_catalog_is_left_out(caplog):
    psa = point("HBA1C", 4.0, month=4).model_copy(update={"test_code": "PSA"})
    assert summarise([psa, point("HBA1C", 5.4, month=4)]).findings == []
    assert "no longer in the catalog: PSA" in caplog.text


# ---------------------------------------------------------------- the tagline


values = st.floats(min_value=0, max_value=20, allow_nan=False).map(lambda v: round(v, 1))


@given(
    history=st.lists(
        st.tuples(values, st.sampled_from(["verified", "needs_check"]), st.sampled_from([None, "<"])),
        min_size=1,
        max_size=4,
    ),
    code=st.sampled_from([test.code for test in CATALOG.tests]),
    low=st.none() | st.just(4.0),
    high=st.none() | st.just(5.6),
)
def test_every_number_said_is_printed_on_a_report(history, code, low, high):
    points = [
        point(code, value, month=month, low=low, high=high, status=status, qualifier=qualifier)
        for month, (value, status, qualifier) in enumerate(history, start=1)
    ]
    summary = summarise(points)
    printed = [p.value for p in points] + [limit for limit in (low, high) if limit is not None]
    known = {marathi.number(number) for number in printed} | {marathi.day(p.sample_date) for p in points}
    known_numbers = {n for text in known for n in DEVANAGARI_NUMBER.findall(text)}
    known_numbers.add(marathi.number(summary.to_check))  # "२ आकडे अजून तपासायचे आहेत"
    said = " ".join(summary.sentences + summary.questions).replace(CATALOG.test(code).name_mr, "")
    assert set(DEVANAGARI_NUMBER.findall(said)) <= known_numbers, said
    assert not re.search("[0-9]", said), said  # every number is in Devanagari


# ---------------------------------------------------------------- the command


def save_report(sha256, sample_date, *results, patient="Sunita Patil"):
    report = ReportRecord(
        lab_name="Sunrise", sample_date=sample_date, report_date=None, file_path="a.pdf", sha256=sha256,
        is_scanned=False, patient_name_raw=patient, patient_age_raw=None, patient_sex_raw=None,
        extract_model="gemma4:e4b", extract_seconds=1.0, raw_json="{}",
    )  # fmt: skip
    with closing(db.connect()) as conn:
        db.save_report(conn, report, list(results))


def saved_result(code, value, **fields):
    unit = CATALOG.test(code).unit
    return SavedResult(
        page=1, test_code=code, raw_name=code, value_text=f"{value:g}", value=value, unit=unit,
        value_std=value,
        unit_std=unit, status="verified", **fields,
    )  # fmt: skip


def test_main_prints_the_summary_and_questions(storage, capsys):
    chol = saved_result("CHOL", 220.0, ref_text="< 200", ref_high=200.0, ref_verified=True)
    save_report("a", "2026-04-15", chol)
    assert main([]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "एकूण कोलेस्टेरॉल: आकडा २२० mg/dL आहे, लॅबच्या वरच्या मर्यादेपेक्षा (२०० mg/dL) जास्त.",
        "",
        "डॉक्टरांना विचारा:",
        "- एकूण कोलेस्टेरॉल: आकडा मर्यादेपेक्षा जास्त आहे, याबद्दल काय करावे?",
    ]


def test_main_prints_json(storage, capsys):
    save_report("a", "2026-01-15", saved_result("HBA1C", 7.0))
    save_report("b", "2026-04-15", saved_result("HBA1C", 7.1))
    assert main(["--json"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["latest_sample_date"] == "2026-04-15"
    assert [(c["before"]["value"], c["kind"]) for c in summary["changes"]] == [
        (7.0, "within_normal_variation")
    ]


def test_main_warns_about_reports_of_other_people(storage, caplog):
    save_report("a", "2026-01-15", saved_result("HBA1C", 9.0), patient="Ramesh Patil")
    save_report("b", "2026-04-15", saved_result("HBA1C", 7.0))
    assert main([]) == 0
    assert "Left out reports of Ramesh Patil" in caplog.text


def test_main_with_nothing_saved_says_how_to_start(storage, capsys, caplog):
    caplog.set_level("INFO", logger="app")
    assert main([]) == 0
    assert capsys.readouterr().out == ""
    assert "No saved report has a sample date yet" in caplog.text
