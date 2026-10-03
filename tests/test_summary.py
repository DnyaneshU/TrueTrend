"""The Marathi summary: rules over verified results, numbers filled in by code only.

Changes are judged with the catalog's real, sourced constants: HbA1c from one lab is a
real change above +4.2 % or below -4.1 %; post-prandial glucose has none and is never judged.
Expected sentences are built from the templates, so rewording a template needs no test changes;
test_the_wording_as_her_report_reads pins one rendering exactly.
"""

import json
import re
from datetime import date

import pytest
from factories import report_record, saved_result, timeline_point
from hypothesis import given
from hypothesis import strategies as st

from arogya_vahi import db, marathi
from arogya_vahi.lab_tests import CATALOG
from arogya_vahi.summary import PRIORITY, TEMPLATES, Templates, main, summarize, template_placeholders

DEVANAGARI_NUMBER = re.compile(r"[०-९]+(?:\.[०-९]+)?")
JAN, APR, JUL = date(2026, 1, 15), date(2026, 4, 15), date(2026, 7, 15)


def sentence(kind, **values):
    return TEMPLATES.sentences[kind].format(**values)


def question(kind, code):
    return TEMPLATES.questions[kind].format(test=CATALOG.test(code).name_mr)


# ---------------------------------------------------------------- templates


def test_template_placeholders_are_counted():
    assert template_placeholders("{test}: {before} ते {after}") == {"test": 1, "before": 1, "after": 1}


@pytest.mark.parametrize("template", ["{test} ७ आहे", "{test} 7 आहे", "{test!r}", "{after:.2f}", "{test"])
def test_a_template_with_a_number_or_odd_placeholder_is_refused(template):
    assert template_placeholders(template) is None


def test_templates_with_digits_do_not_load():
    sentences = {**TEMPLATES.sentences, "stable": "{test} 7"}
    with pytest.raises(ValueError, match="stable has a digit"):
        Templates.model_validate({**TEMPLATES.model_dump(), "sentences": sentences})


def test_templates_missing_a_sentence_do_not_load():
    sentences = {name: text for name, text in TEMPLATES.sentences.items() if name != "first_report"}
    with pytest.raises(ValueError, match=r"missing \['first_report'\]"):
        Templates.model_validate({**TEMPLATES.model_dump(), "sentences": sentences})


# ---------------------------------------------------------------- what is said


def test_the_wording_as_her_report_reads():
    summary = summarize([timeline_point("CHOL", 220.0, high=200.0)])
    assert summary.sentences == ["एकूण कोलेस्टेरॉल: आकडा २२० mg/dL आहे, लॅबच्या वरच्या मर्यादेपेक्षा (२०० mg/dL) जास्त."]
    assert summary.questions == ["एकूण कोलेस्टेरॉल: आकडा मर्यादेपेक्षा जास्त आहे, याबद्दल काय करावे?"]


def test_no_reports_no_summary():
    assert summarize([]).sentences == []


def test_a_first_report_within_range_says_so():
    summary = summarize([timeline_point("HBA1C", 5.4, low=4.0, high=5.6)])
    assert (summary.sentences, summary.questions) == ([sentence("first_report")], [])


@pytest.mark.parametrize(
    "code, value, low, high, kind, limit",
    [
        ("CHOL", 220.0, None, 200.0, "above_range", "२०० mg/dL"),
        ("UREA", 18.0, 19.3, 43.0, "below_range", "१९.३ mg/dL"),
    ],
)
def test_a_result_outside_its_labs_range(code, value, low, high, kind, limit):
    summary = summarize([timeline_point(code, value, low=low, high=high)])
    test = CATALOG.test(code)
    after = f"{marathi.number(value)} {test.unit}"
    assert summary.sentences == [sentence(kind, test=test.name_mr, after=after, limit=limit)]
    assert summary.questions == [question(kind, code)]


def test_a_range_not_found_in_the_pdf_is_never_used():
    # the range Gemma read was not printed in the value's row: maybe "70-100" misread as "70-10"
    glucose = timeline_point("GLU_F", 90.0, low=70.0, high=10.0, ref_verified=False)
    assert summarize([glucose]).findings == []


def test_values_are_shown_as_printed():
    # 7.8 mmol/L, compared as 140.5 mg/dL, is said as the report prints it
    glucose = timeline_point(
        "GLU_F", 7.8, unit="mmol/L", value_std=140.525, ref_text="3.9 - 5.5", ref_low=70.26, ref_high=99.088,
        ref_verified=True,
    )  # fmt: skip
    (finding,) = summarize([glucose]).findings
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
    b12 = timeline_point("B12", value, low=187.0, high=833.0, qualifier=qualifier)
    findings = summarize([b12]).findings
    assert [f.kind for f in findings] == ([kind] if kind else [])
    if kind:
        assert findings[0].slots["after"] == f"{qualifier} {marathi.number(value)} pg/mL"


def test_a_limit_says_nothing_about_the_other_side_of_the_range():
    assert summarize([timeline_point("TSH", 5.0, low=0.4, high=4.0, qualifier="<")]).findings == []


@pytest.mark.parametrize("before, after, kind", [(7.0, 7.6, "real_increase"), (7.6, 7.0, "real_decrease")])
def test_a_real_change(before, after, kind):
    summary = summarize([timeline_point("HBA1C", before, JAN), timeline_point("HBA1C", after, APR)])
    expected = sentence(
        kind,
        test=CATALOG.test("HBA1C").name_mr,
        before=f"{marathi.number(before)} %",
        before_date=marathi.day(JAN),
        after=f"{marathi.number(after)} %",
    )
    assert (summary.sentences, summary.questions) == ([expected], [question(kind, "HBA1C")])


@pytest.mark.parametrize(
    "values, kind", [((6.5, 7.0, 7.6), "trend_increase"), ((7.6, 7.0, 6.5), "trend_decrease")]
)
def test_three_sample_dates_in_a_row_is_a_trend(values, kind):
    points = [
        timeline_point("HBA1C", value, when) for value, when in zip(values, (JAN, APR, JUL), strict=True)
    ]
    (finding,) = summarize(points).findings
    assert finding.kind == kind
    assert (finding.slots["first"], finding.slots["first_date"]) == (
        f"{marathi.number(values[0])} %",
        marathi.day(JAN),
    )


def test_the_same_sample_uploaded_twice_is_not_a_trend():
    again = timeline_point("HBA1C", 7.6, APR, report_id=99)
    summary = summarize([timeline_point("HBA1C", 7.0, JAN), timeline_point("HBA1C", 7.6, APR), again])
    assert [f.kind for f in summary.findings] == ["real_increase"]


def test_a_change_across_two_labs_needs_more_to_be_real():
    # +7.1 % is real within one lab (4.2 %) but normal variation across two (9.7 %)
    other_lab = timeline_point("HBA1C", 7.5, APR, lab_name="Metro Labs")
    summary = summarize([timeline_point("HBA1C", 7.0, JAN), other_lab])
    assert [c.kind for c in summary.changes] == ["within_normal_variation"]
    assert summary.sentences == [sentence("stable")]


def test_normal_variation_is_stable():
    summary = summarize([timeline_point("HBA1C", 7.0, JAN), timeline_point("HBA1C", 7.2, APR)])  # +2.9 %
    assert summary.sentences == [sentence("stable")]


def test_changes_that_cannot_be_judged_say_so():
    summary = summarize([timeline_point("GLU_PP", 120.0, JAN), timeline_point("GLU_PP", 300.0, APR)])
    assert summary.sentences == [sentence("not_compared")]


def test_stable_is_not_said_when_some_changes_were_not_judged():
    points = [timeline_point("HBA1C", 7.0, JAN), timeline_point("HBA1C", 7.2, APR)]
    points += [timeline_point("GLU_PP", 120.0, JAN), timeline_point("GLU_PP", 300.0, APR)]
    assert summarize(points).sentences == [sentence("partly_stable")]


def test_a_new_test_on_a_later_report_is_not_called_a_first_report():
    summary = summarize([timeline_point("CHOL", 180.0, JAN), timeline_point("HBA1C", 5.4, APR)])
    assert summary.sentences == [sentence("not_compared")]


def test_a_result_that_needs_checking_is_counted_never_stated():
    unverified = timeline_point("HBA1C", 9.9, APR, status="needs_check")
    summary = summarize([timeline_point("HBA1C", 7.0, JAN), unverified])
    assert summary.to_check == 1 and summary.findings == []
    assert summary.sentences == [sentence("to_check_one")]
    assert "९.९" not in " ".join(summary.sentences + summary.questions)


def test_several_results_to_check_are_counted():
    points = [timeline_point(code, 1.0, APR, status="needs_check") for code in ("CHOL", "HDL")]
    assert summarize(points).sentences == [sentence("to_check", count="२")]


def test_at_most_three_sentences_most_important_first():
    points = [
        timeline_point("CHOL", 250.0, APR, high=200.0),
        timeline_point("TG", 250.0, APR, high=150.0),
        timeline_point("LDL", 190.0, APR, high=100.0),
        timeline_point("HBA1C", 7.0, JAN),
        timeline_point("HBA1C", 7.6, APR),
    ]
    summary = summarize(points)
    assert [f.kind for f in summary.findings] == [
        "real_increase",
        "above_range",
        "above_range",
        "above_range",
    ]
    assert [f.test_code for f in summary.findings[1:]] == ["CHOL", "LDL", "TG"]  # catalog order
    assert len(summary.sentences) == len(summary.questions) == 3


def test_a_result_to_check_takes_the_last_sentence():
    points = [timeline_point(code, 300.0, APR, high=200.0) for code in ("CHOL", "TG", "LDL")]
    points.append(timeline_point("HDL", 40.0, APR, status="needs_check"))
    summary = summarize(points)
    assert len(summary.sentences) == 3 and len(summary.questions) == 2
    assert summary.sentences[-1] == sentence("to_check_one")


def test_tests_missing_from_the_latest_report_are_not_summarised():
    summary = summarize([timeline_point("CHOL", 250.0, JAN, high=200.0), timeline_point("HBA1C", 5.4, APR)])
    assert summary.findings == [] and summary.latest_sample_date == APR


def test_another_persons_reports_are_left_out():
    father = timeline_point("HBA1C", 9.0, JAN, patient_id=2, patient_name="Mr. Ramesh Patil")
    summary = summarize([father, timeline_point("HBA1C", 7.0, APR, patient_name="SUNITA PATIL.")])
    assert (summary.other_people, summary.reports_left_out) == (["Mr. Ramesh Patil"], 1)
    assert summary.changes == [] and summary.sentences == [sentence("first_report")]


@pytest.mark.parametrize("earlier, latest", [(None, 1), (1, None)])
def test_a_report_matched_to_no_one_is_compared_with_nothing(earlier, latest):
    earlier_point = timeline_point("HBA1C", 5.2, JAN, patient_id=earlier)
    summary = summarize([earlier_point, timeline_point("HBA1C", 9.1, APR, patient_id=latest)])
    assert summary.changes == [] and summary.reports_left_out == 1


def test_a_test_no_longer_in_the_catalog_is_left_out(caplog):
    psa = timeline_point("HBA1C", 4.0, APR, test_code="PSA")
    assert summarize([psa, timeline_point("HBA1C", 5.4, APR)]).findings == []
    assert "no longer in the catalog: PSA" in caplog.text


def test_findings_are_ranked_in_the_order_of_their_kinds():
    assert PRIORITY[0] == "trend_increase" and PRIORITY[-1] == "below_range"


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
        timeline_point(
            code, value, date(2026, month, 15), low=low, high=high, status=status, qualifier=qualifier
        )
        for month, (value, status, qualifier) in enumerate(history, start=1)
    ]
    summary = summarize(points)
    printed = [p.value for p in points] + [limit for limit in (low, high) if limit is not None]
    known = {marathi.number(number) for number in printed} | {marathi.day(p.sample_date) for p in points}
    known_numbers = {n for text in known for n in DEVANAGARI_NUMBER.findall(text)}
    known_numbers.add(marathi.number(summary.to_check))  # "२ आकडे अजून तपासायचे आहेत"
    # a test's own name may hold digits ("व्हिटॅमिन बी१२", "HbA1c"); it is not a number said
    said = " ".join(summary.sentences + summary.questions).replace(CATALOG.test(code).name_mr, "")
    assert set(DEVANAGARI_NUMBER.findall(said)) <= known_numbers, said
    assert not re.search("[0-9]", said), said  # every number is in Devanagari


# ---------------------------------------------------------------- the command


def save(conn, sha256, sample_date, *results, patient="Sunita Patil"):
    record = report_record(sha256=sha256, sample_date=sample_date, patient_name_raw=patient)
    db.save_report(conn, record, list(results))


def test_main_prints_the_summary_and_questions(conn, capsys):
    save(
        conn,
        "a",
        "2026-04-15",
        saved_result("CHOL", 220.0, ref_text="< 200", ref_high=200.0, ref_verified=True),
    )
    assert main([]) == 0
    name = CATALOG.test("CHOL").name_mr
    assert capsys.readouterr().out.splitlines() == [
        sentence("above_range", test=name, after="२२० mg/dL", limit="२०० mg/dL"),
        "",
        TEMPLATES.labels["ask_doctor"],
        f"- {question('above_range', 'CHOL')}",
    ]


def test_main_prints_json(conn, capsys):
    save(conn, "a", "2026-01-15", saved_result("HBA1C", 7.0))
    save(conn, "b", "2026-04-15", saved_result("HBA1C", 7.1))
    assert main(["--json"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["latest_sample_date"] == "2026-04-15"
    assert [(c["before"]["value"], c["kind"]) for c in summary["changes"]] == [
        (7.0, "within_normal_variation")
    ]


def test_main_warns_about_reports_left_out(conn, caplog):
    save(conn, "a", "2026-01-15", saved_result("HBA1C", 9.0), patient="Ramesh Patil")
    save(conn, "b", "2026-04-15", saved_result("HBA1C", 7.0))
    assert main([]) == 0
    assert "Left out 1 earlier report(s) for someone else or no one (Ramesh Patil)" in caplog.text


def test_main_with_nothing_saved_says_how_to_start(capsys, caplog):
    caplog.set_level("INFO", logger="arogya_vahi")
    assert main([]) == 0
    assert capsys.readouterr().out == ""
    assert "No saved report has a sample date yet" in caplog.text
