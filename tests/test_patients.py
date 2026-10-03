"""Matching reports to family members, and correcting it with arogya-patients."""

import json

import pytest
from factories import report_record, rows, saved_result

from arogya_vahi import db, patients
from arogya_vahi.errors import UserError
from arogya_vahi.models import ReportPerson
from arogya_vahi.patients import age_of, display_name, main, match_report, same_person_name, sex_of
from arogya_vahi.summary import main as summary_main


def person(name="Sunita Patil", age=None, sex=None, sample_date="2026-01-15", **overrides) -> ReportPerson:
    fields = {"report_id": 0, "patient_id": None, "report_date": None, "lab_name": None}
    return ReportPerson(name=name, age=age, sex=sex, sample_date=sample_date, **{**fields, **overrides})


def save(conn, sha256, name="Sunita Patil", sample_date="2026-01-15", age=None, sex=None, patient_id=None):
    record = report_record(
        sha256=sha256,
        file_path=f"{sha256}.pdf",
        sample_date=sample_date,
        patient_name_raw=name,
        patient_age_raw=age,
        patient_sex_raw=sex,
        patient_id=patient_id,
    )
    return db.save_report(conn, record, [saved_result()])


def patient_of_reports(conn):
    return rows(conn, "SELECT id, patient_id FROM reports ORDER BY id")


# ---------------------------------------------------------------- comparing what is printed


@pytest.mark.parametrize(
    "a, b",
    [
        ("Mrs. Sunita Patil", "SUNITA PATIL."),
        ("Patil Sunita", "Sunita Patil"),  # surname first, as many labs print it
        ("Sunita R. Patil", "Sunita Patil"),  # a middle initial only one prints
        ("Mrs.Sunita Patil", "Smt Sunita Patil"),
        ("श्रीमती सुनीता पाटील", "सुनीता पाटील"),
        ("Dr. Anil Patil", "Mr Anil Patil"),
    ],
)
def test_same_person_name(a, b):
    assert same_person_name(a, b) and same_person_name(b, a)


@pytest.mark.parametrize(
    "a, b",
    [
        ("S. Patil", "Sunita Patil"),  # an initial is not a name
        ("Sunita Ramesh Patil", "Sunita Patil"),  # nor is a missing middle name guessed
        ("Ramesh Patil", "Sunita Ramesh Patil"),  # ... which would match a father to his daughter
        ("Sunita R Patil", "Sunita K Patil"),
        ("Sunita", "Sunita R"),  # one word is too little to ignore an initial
        ("Sunita Patil", None),
        ("Mrs.", "Mr."),  # only titles: no name at all
    ],
)
def test_different_person_names(a, b):
    assert not same_person_name(a, b) and not same_person_name(b, a)


@pytest.mark.parametrize(
    "printed, shown",
    [
        ("MRS. SUNITA PATIL", "Sunita Patil"),
        ("Mrs.Sunita R. Patil", "Sunita R Patil"),
        ("श्री अनिल पाटील", "अनिल पाटील"),
    ],
)
def test_display_name_drops_titles(printed, shown):
    assert display_name(printed) == shown


@pytest.mark.parametrize(
    "printed, years",
    [
        ("62", 62), ("62 Y", 62), ("62 Years", 62), ("62Y 3M", 62), ("62/F", 62), ("६२ वर्षे", 62),
        ("8 Months", 0), ("12 Days", 0), ("150 Y", None), ("NA", None), (None, None),
    ],
)  # fmt: skip
def test_age_of(printed, years):
    assert age_of(printed) == years


@pytest.mark.parametrize(
    "name, sex, expected",
    [
        ("Sunita Patil", "Female", "F"),
        ("Sunita Patil", "स्त्री", "F"),
        ("Anil Patil", "M", "M"),
        ("Mrs. Sunita Patil", None, "F"),  # from the title when no sex is printed
        ("Mr. Anil Patil", None, "M"),
        ("Dr. Anil Patil", None, None),
        ("Mr. Anil Patil", "F", "F"),  # what is printed wins over the title
    ],
)
def test_sex_of(name, sex, expected):
    assert sex_of(person(name, sex=sex)) == expected


# ---------------------------------------------------------------- matching


def test_a_new_name_is_a_new_patient(conn):
    match = match_report(conn, person("Mrs. Sunita Patil", age="62 Y", sample_date="2026-01-15"))
    assert match.new and match.note is None
    assert db.patients(conn) == [match.patient]
    assert (match.patient.display_name, match.patient.sex, match.patient.birth_year) == (
        "Sunita Patil",
        "F",
        1964,
    )


def test_the_same_name_is_the_same_patient_and_its_new_spelling_is_remembered(conn):
    first = match_report(conn, person("Mrs. Sunita Patil"))
    again = match_report(conn, person("PATIL SUNITA", age="62", sex="F"))
    assert not again.new and again.patient.id == first.patient.id
    (patient,) = db.patients(conn)
    assert patient.aliases == []  # same name, in another order: nothing new to remember
    assert (patient.sex, patient.birth_year) == ("F", 1964)  # learned from the second report
    match_report(conn, person("Sunita R. Patil"))
    assert db.patients(conn)[0].aliases == ["Sunita R. Patil"]


@pytest.mark.parametrize(
    "earlier, later, reason",
    [
        (person("Anil Patil", sex="M"), person("Anil Patil", sex="F"), "of the other sex"),
        (person("Anil Patil", age="70"), person("Anil Patil", age="8"), "born about 1956, not 2018"),
    ],
)
def test_the_same_name_of_someone_else_is_a_new_patient_with_a_note(conn, earlier, later, reason):
    first = match_report(conn, earlier)
    second = match_report(conn, later)
    assert second.new and second.patient.id != first.patient.id
    assert (
        reason in second.note
        and f"arogya-patients merge {first.patient.id} {second.patient.id}" in second.note
    )


def test_an_age_a_year_apart_is_the_same_patient(conn):
    first = match_report(conn, person(age="62", sample_date="2026-01-15"))
    assert match_report(conn, person(age="62", sample_date="2027-01-10")).patient.id == first.patient.id


def test_a_report_that_could_be_two_patients_is_matched_to_no_one(conn):
    match_report(conn, person("Anil Patil", sex="M", age="70"))
    match_report(conn, person("Anil Patil", sex="M", age="8"))
    match = match_report(conn, person("Anil Patil"))  # no age, no sex: either of them
    assert match.patient is None and "could be patient #1 or #2" in match.note


def test_a_report_without_a_name_is_matched_to_no_one(conn):
    match = match_report(conn, person(None))
    assert match.patient is None and db.patients(conn) == []


def test_reports_saved_before_matching_are_matched(conn):
    save(conn, "b", "Sunita Patil", "2026-04-15")
    save(conn, "a", "Mrs. Sunita Patil", "2026-01-15")
    save(conn, "c", "Ramesh Patil", "2026-02-15")
    save(conn, "d", None, "2026-03-15")
    matches = patients.match_unmatched(conn)
    assert [person.report_id for person, _ in matches] == [2, 3, 1]  # oldest sample first
    assert patient_of_reports(conn) == [(1, 1), (2, 1), (3, 2), (4, None)]
    assert db.patients(conn)[0].display_name == "Sunita Patil"
    assert patients.match_unmatched(conn) == []  # nothing left but the report without a name


# ---------------------------------------------------------------- corrections


def test_merge_joins_reports_and_remembers_the_names(conn):
    save(conn, "a", "Sunita Patil")
    save(conn, "b", "Sunita Ramesh Patil")
    patients.match_unmatched(conn)
    kept = patients.merge(conn, 1, 2)
    assert kept.aliases == ["Sunita Ramesh Patil"]
    assert patient_of_reports(conn) == [(1, 1), (2, 1)] and len(db.patients(conn)) == 1
    assert match_report(conn, person("SUNITA RAMESH PATIL")).patient.id == 1  # the next report


def test_assign_moves_a_report_and_deletes_a_patient_left_without_reports(conn):
    save(conn, "a", "Sunita Patil")
    save(conn, "b", "Sunita Patel")  # a misprint
    patients.match_unmatched(conn)
    assert patients.assign(conn, 2, "1").id == 1
    assert patient_of_reports(conn) == [(1, 1), (2, 1)]
    assert [patient.id for patient in db.patients(conn)] == [1]
    assert db.patients(conn)[0].aliases == []  # a misprint isn't remembered


def test_assign_to_a_new_patient(conn):
    save(conn, "a", "Anil Patil", age="70")
    save(conn, "b", "Anil Patil", age="70")  # the grandson's report, his age misread
    patients.match_unmatched(conn)
    new = patients.assign(conn, 2, "new")
    assert new.id == 2 and patient_of_reports(conn) == [(1, 1), (2, 2)]


@pytest.mark.parametrize(
    "call, message",
    [
        (lambda conn: patients.assign(conn, 9, "1"), "There is no report #9"),
        (lambda conn: patients.assign(conn, 1, "9"), "There is no patient #9"),
        (lambda conn: patients.assign(conn, 1, "sunita"), "is not a patient number"),
        (lambda conn: patients.assign(conn, 2, "new"), "prints no name"),
        (lambda conn: patients.merge(conn, 1, 1), "two different patients"),
        (lambda conn: patients.merge(conn, 1, 9), "There is no patient #9"),
    ],
)
def test_corrections_that_cant_be_made_are_clear_errors(conn, call, message):
    save(conn, "a", "Sunita Patil")
    save(conn, "b", None)
    patients.match_unmatched(conn)
    with pytest.raises(UserError, match=message):
        call(conn)


# ---------------------------------------------------------------- the commands


def test_main_lists_patients_and_reports_matched_to_no_one(conn, capsys):
    save(conn, "a", "Mrs. Sunita Patil", age="62 Y", sex="F")
    save(conn, "b", "SUNITA R. PATIL", "2026-04-15")
    save(conn, "c", None, "2026-02-15")
    assert main([]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "#1 Sunita Patil (F, born about 1964)",
        "   also printed as: SUNITA R. PATIL",
        "   reports: #1 2026-01-15 Sunrise Diagnostics, #2 2026-04-15 Sunrise Diagnostics",
        "Reports matched to no one:",
        "   #3 2026-02-15 Sunrise Diagnostics: no name printed",
    ]


def test_main_prints_json(conn, capsys):
    save(conn, "a")
    assert main(["--json"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert [(p["display_name"], [r["report_id"] for r in p["reports"]]) for p in listed["patients"]] == [
        ("Sunita Patil", [1])
    ]


def test_main_merges_and_assigns(conn, caplog):
    caplog.set_level("INFO", logger="arogya_vahi")
    save(conn, "a", "Sunita Patil")
    save(conn, "b", "Sunita Ramesh Patil")
    save(conn, "c", "Ramesh Patil")
    assert main([]) == 0
    assert main(["merge", "1", "2"]) == 0
    assert "Patient #2 is now part of #1 Sunita Patil." in caplog.text
    assert main(["assign", "3", "1"]) == 0
    assert "Report #3 is now for patient #1 Sunita Patil." in caplog.text
    assert patient_of_reports(conn) == [(1, 1), (2, 1), (3, 1)]


def test_main_reports_a_wrong_number_in_one_line(conn, capsys):
    assert main(["merge", "1", "2"]) == 1
    assert capsys.readouterr().err.strip() == "error: There is no patient #1; see them with arogya-patients."


def test_summary_is_for_the_patient_asked_for(conn, capsys):
    save(conn, "a", "Sunita Patil", "2026-01-15")
    save(conn, "b", "Ramesh Patil", "2026-04-15")
    assert summary_main(["--json", "--patient", "1"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["latest_sample_date"] == "2026-01-15" and summary["reports_left_out"] == 0
    assert summary_main(["--json"]) == 0  # the latest report's patient
    assert json.loads(capsys.readouterr().out)["latest_sample_date"] == "2026-04-15"
    assert summary_main(["--patient", "9"]) == 1
    assert "There is no patient #9" in capsys.readouterr().err


def test_summary_compares_spellings_of_one_patient(conn, capsys):
    save(conn, "a", "Sunita Ramesh Patil", "2026-01-15")
    save(conn, "b", "Sunita Patil", "2026-04-15")
    assert summary_main(["--json"]) == 0
    assert json.loads(capsys.readouterr().out)["reports_left_out"] == 1  # not the same patient, yet
    assert main(["merge", "2", "1"]) == 0
    assert summary_main(["--json"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["reports_left_out"] == 0 and len(summary["changes"]) == 1
