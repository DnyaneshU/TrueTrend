"""Matching reports to family members, and correcting it with arogya-patients."""

import json

import pytest
from factories import printed_person, rows, saved_report

from arogya_vahi import db, patients
from arogya_vahi.errors import UserError
from arogya_vahi.patients import main, match_report
from arogya_vahi.summary import main as summary_main


def save(conn, sha256, name="Sunita Patil", sample_date="2026-01-15", age=None, sex=None):
    return saved_report(
        conn, sha256, sample_date=sample_date, patient_name_raw=name, patient_age_raw=age, patient_sex_raw=sex
    )


def patient_of_reports(conn):
    return rows(conn, "SELECT id, patient_id FROM reports ORDER BY id")


def match(conn, *args, **kwargs):
    with db.write(conn):
        return match_report(conn, printed_person(*args, **kwargs))


# ---------------------------------------------------------------- matching


def test_a_new_name_is_a_new_patient(conn):
    new = match(conn, "Mrs. Sunita Patil", age="62 Y")
    assert new.new and new.note is None
    assert db.patients(conn) == [new.patient]
    assert (new.patient.display_name, new.patient.sex, new.patient.birth_year) == ("Sunita Patil", "F", 1964)


def test_the_same_name_is_the_same_patient_and_its_new_spelling_is_remembered(conn):
    first = match(conn, "Mrs. Sunita Patil")
    again = match(conn, "PATIL SUNITA", age="62", sex="F")
    assert not again.new and again.patient.id == first.patient.id
    (patient,) = db.patients(conn)
    assert patient.aliases == []  # same name, in another order: nothing new to remember
    assert (patient.sex, patient.birth_year) == ("F", 1964)  # learned from the second report
    match(conn, "Sunita R. Patil")
    assert db.patients(conn)[0].aliases == ["Sunita R. Patil"]


@pytest.mark.parametrize(
    "earlier, later, reason",
    [
        ({"name": "Anil Patil", "sex": "M"}, {"name": "Anil Patil", "sex": "F"}, "of the other sex"),
        (
            {"name": "Anil Patil", "age": "70"},
            {"name": "Anil Patil", "age": "8"},
            "born about 1956, not 2018",
        ),
    ],
)
def test_the_same_name_of_someone_else_is_a_new_patient_with_a_note(conn, earlier, later, reason):
    first, second = match(conn, **earlier), match(conn, **later)
    assert second.new and second.patient.id != first.patient.id
    assert reason in second.note
    assert f"arogya-patients merge {first.patient.id} {second.patient.id}" in second.note


def test_a_middle_initial_already_printed_rules_out_another(conn):
    # "Sunita R." and "Sunita K." are two people: the initial is a father's or husband's name
    first = match(conn, "Sunita Patil")
    assert match(conn, "Sunita R. Patil").patient.id == first.patient.id
    other = match(conn, "Sunita K. Patil")
    assert other.new and "also printed as 'Sunita R. Patil'" in other.note


def test_a_babys_report_is_not_the_mothers(conn):
    mother = match(conn, "Sunita Patil", age="30")
    baby = match(conn, "B/O Sunita Patil", age="2 Days")
    assert baby.new and baby.note is None and baby.patient.id != mother.patient.id
    assert match(conn, "Sunita Patil").patient.id == mother.patient.id  # the next report is hers again
    assert match(conn, "b / o SUNITA PATIL").patient.id == baby.patient.id


def test_an_age_a_year_apart_is_the_same_patient(conn):
    first = match(conn, age="62", sample_date="2026-01-15")
    assert match(conn, age="62", sample_date="2027-01-10").patient.id == first.patient.id


def test_a_report_that_could_be_two_patients_is_matched_to_no_one(conn):
    grandfather = match(conn, "Anil Patil", sex="M", age="70")
    grandson = match(conn, "Anil Patil", sex="M", age="8")
    either = match(conn, "Anil Patil")  # no age, no sex: either of them
    assert either.patient is None
    assert f"#{grandfather.patient.id} or #{grandson.patient.id}" in either.note


@pytest.mark.parametrize("name", [None, "Mrs.", "Patient", "Demo Patient Name"])
def test_a_report_that_names_no_one_is_matched_to_no_one(conn, name):
    assert match(conn, name).patient is None and db.patients(conn) == []


def test_reports_saved_before_matching_are_matched(conn):
    save(conn, "b", "Sunita Patil", "2026-04-15")
    save(conn, "a", "Mrs. Sunita Patil", "2026-01-15")
    save(conn, "c", "Ramesh Patil", "2026-02-15")
    save(conn, "d", None, "2026-03-15")
    matched = patients.match_saved(conn)
    assert [person.report_id for person, _ in matched] == [2, 3, 1]  # oldest sample first
    assert patient_of_reports(conn) == [(1, 1), (2, 1), (3, 2), (4, None)]
    assert db.patients(conn)[0].display_name == "Sunita Patil"
    assert patients.match_saved(conn) == []  # nothing left but the report without a name


# ---------------------------------------------------------------- corrections


def test_merge_joins_reports_and_remembers_the_names(conn):
    save(conn, "a", "Sunita Patil")
    save(conn, "b", "Sunita Ramesh Patil")
    patients.match_saved(conn)
    kept = patients.merge(conn, 1, 2)
    assert kept.aliases == ["Sunita Ramesh Patil"]
    assert patient_of_reports(conn) == [(1, 1), (2, 1)] and len(db.patients(conn)) == 1
    assert match(conn, "SUNITA RAMESH PATIL").patient.id == 1  # the next report


def test_assign_moves_a_report_and_deletes_a_patient_left_without_reports(conn):
    save(conn, "a", "Sunita Patil")
    save(conn, "b", "Sunita Patel")  # a misprint
    patients.match_saved(conn)
    assert patients.assign(conn, 2, 1).id == 1
    assert patient_of_reports(conn) == [(1, 1), (2, 1)]
    assert [patient.id for patient in db.patients(conn)] == [1]
    assert db.patients(conn)[0].aliases == []  # a misprint isn't remembered


def test_assign_to_a_new_patient(conn):
    save(conn, "a", "Anil Patil", age="70")
    save(conn, "b", "Anil Patil", age="70")  # the grandson's report, his age misread
    patients.match_saved(conn)
    new = patients.assign(conn, 2, None)
    assert new.id == 2 and patient_of_reports(conn) == [(1, 1), (2, 2)]


@pytest.mark.parametrize(
    "call, message",
    [
        (lambda conn: patients.assign(conn, 9, 1), "There is no report #9"),
        (lambda conn: patients.assign(conn, 1, 9), "There is no patient #9"),
        (lambda conn: patients.assign(conn, 2, None), "prints no name"),
        (lambda conn: patients.merge(conn, 1, 1), "two different patients"),
        (lambda conn: patients.merge(conn, 1, 9), "There is no patient #9"),
    ],
)
def test_corrections_that_cant_be_made_are_clear_errors(conn, call, message):
    save(conn, "a", "Sunita Patil")
    save(conn, "b", None)
    patients.match_saved(conn)
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
    assert listed["unmatched"] == []


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


def test_main_assign_takes_a_number_or_new(conn, capsys):
    save(conn, "a", "Sunita Patil")
    with pytest.raises(SystemExit):
        main(["assign", "1", "sunita"])
    assert 'is not a patient number or "new"' in capsys.readouterr().err


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
    summary = json.loads(capsys.readouterr().out)
    assert summary["reports_left_out"] == 1  # not the same patient, yet
    assert summary["other_people"] == ["Sunita Ramesh Patil"]  # shown by patient name
    assert main(["merge", "2", "1"]) == 0
    assert summary_main(["--json"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["reports_left_out"] == 0 and len(summary["changes"]) == 1


def test_two_requests_matching_at_once_do_not_each_add_the_same_patient(storage):
    # The server answers requests on several threads, and more than one screen asks for
    # the patient list. Without one writer at a time, each would read "no such patient"
    # and add her again, splitting one person's history across copies of her.
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import closing

    with closing(db.connect()) as setup:
        for sha, date in (("a", "2026-01-12"), ("b", "2026-05-20"), ("c", "2026-09-28")):
            saved_report(conn=setup, sha256=sha, sample_date=date,
                         patient_name_raw="Mrs. Sunita Patil", patient_sex_raw="F")

    def match_in_its_own_connection():
        with closing(db.connect()) as conn:
            patients.match_saved(conn)

    with ThreadPoolExecutor(max_workers=4) as pool:
        for job in [pool.submit(match_in_its_own_connection) for _ in range(4)]:
            job.result()

    with closing(db.connect()) as conn:
        assert len(db.patients(conn)) == 1, "one person, however many requests arrive at once"
