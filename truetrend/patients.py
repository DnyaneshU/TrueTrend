"""Which family member a report is for, and the command to list and correct it.

    truetrend-patients                         every patient and their reports
    truetrend-patients merge KEEP OTHER        OTHER is the same person as KEEP: their reports join
    truetrend-patients assign REPORT PATIENT   a report is someone else's (PATIENT may be "new")
    (or: python -m truetrend.patients ...)

A report goes to the patient whose name, or a name printed on one of their earlier
reports, is the same as the one it prints (truetrend.people), and which none of their
names rules out: "Sunita K. Patil" is not a patient also printed as "Sunita R. Patil".
A same-named patient of the other sex, or born more than a year apart (from the printed
ages), is someone else too. A report that could be more than one patient, or names no
one, is matched to no one and compared with nothing.
"""

import argparse
import logging
import sqlite3
import sys
from contextlib import closing

from truetrend import cli, db
from truetrend.errors import UserError
from truetrend.models import Match, Patient, PatientListing, PatientReports, PrintedPerson, ReportPerson
from truetrend.people import (
    birth_year_of,
    display_name,
    initials_clash,
    is_a_name,
    name_key,
    same_person_name,
    sex_of,
)

logger = logging.getLogger(__name__)

BIRTH_YEAR_SLACK = 1  # an age is printed in whole years, so two estimates may differ by one


def get(conn: sqlite3.Connection, patient_id: int) -> Patient:
    """The patient with this id, or a UserError saying how to find the right one."""
    if (patient := db.patient(conn, patient_id)) is None:
        raise UserError(f"There is no patient #{patient_id}; see them with truetrend-patients.")
    return patient


# ---------------------------------------------------------------- matching


def match_report(conn: sqlite3.Connection, person: PrintedPerson) -> Match:
    """The patient a report is for, adding them if they are new.

    Call inside db.write(conn), with the report's save, so two reports read at once
    can't both add the same new patient. Doesn't set the report's patient: the caller
    saves it with the report.
    """
    if not is_a_name(person.name):
        return Match(patient=None, note="no name is printed, so it is compared with no one")
    sex, birth_year = sex_of(person), birth_year_of(person)
    named = [patient for patient in db.patients(conn) if _names(patient, person.name)]
    reasons = {patient.id: _ruled_out(patient, person.name, sex, birth_year) for patient in named}
    fitting = [patient for patient in named if reasons[patient.id] is None]
    if len(fitting) > 1:
        ids = " or ".join(f"#{patient.id}" for patient in fitting)
        return Match(
            patient=None,
            note=f"'{person.name}' could be patient {ids}; choose with truetrend-patients assign",
        )
    if fitting:
        patient = _remember(fitting[0], person.name, sex, birth_year)
        db.update_patient(conn, patient)
        return Match(patient=patient)
    patient = _new_patient(conn, person)
    note = None
    if named:
        why = "; ".join(f"#{other.id} is {reasons[other.id]}" for other in named)
        note = (
            f"'{person.name}' has another patient's name ({why}), so is a new patient #{patient.id}. "
            f"If they are the same person: truetrend-patients merge {named[0].id} {patient.id}"
        )
    return Match(patient=patient, new=True, note=note)


def match_saved(conn: sqlite3.Connection) -> list[tuple[ReportPerson, Match]]:
    """Match every saved report that names someone and is matched to no one yet, and say what
    happened (reports saved before matching existed, or that could be more than one patient)."""
    matches = []
    with db.write(conn):
        for person in db.report_people(conn, to_match=True):
            match = match_report(conn, person)
            if match.patient:
                db.set_report_patient(conn, person.report_id, match.patient.id)
            matches.append((person, match))
    if matched := sum(match.patient is not None for _, match in matches):
        logger.info("Matched %d saved report(s) to patients; see them with truetrend-patients.", matched)
    for person, match in matches:
        if match.note:
            logger.warning("report #%d: %s", person.report_id, match.note)
    return matches


def _names(patient: Patient, printed: str) -> bool:
    return any(same_person_name(printed, name) for name in (patient.display_name, *patient.aliases))


def _ruled_out(patient: Patient, printed: str, sex: str | None, birth_year: int | None) -> str | None:
    """Why a same-named patient can't be the person a report is for, if they can't."""
    if clash := next((name for name in patient.aliases if initials_clash(printed, name)), None):
        return f"also printed as '{clash}'"
    if sex and patient.sex and sex != patient.sex:
        return "of the other sex"
    if birth_year and patient.birth_year and abs(birth_year - patient.birth_year) > BIRTH_YEAR_SLACK:
        return f"born about {patient.birth_year}, not {birth_year}"
    return None


def _new_patient(conn: sqlite3.Connection, person: PrintedPerson) -> Patient:
    patient = Patient(
        id=0, display_name=display_name(person.name), sex=sex_of(person), birth_year=birth_year_of(person)
    )
    return db.add_patient(conn, patient)


def _remember(patient: Patient, printed: str, sex: str | None, birth_year: int | None) -> Patient:
    """The patient with a new printed name, sex or birth year added; what is known stays."""
    known = any(name_key(printed) == name_key(name) for name in (patient.display_name, *patient.aliases))
    return patient.model_copy(
        update={
            "aliases": patient.aliases if known else [*patient.aliases, printed],
            "sex": patient.sex or sex,
            "birth_year": patient.birth_year or birth_year,
        }
    )


def possibly_same(conn: sqlite3.Connection) -> list[tuple[Patient, Patient]]:
    """Pairs of patients with the same name, kept apart by their sex, age or another name:
    "Is Sunita Patil the same person as Sunita Patil?" is the user's to answer."""
    everyone = db.patients(conn)
    return [
        (a, b)
        for index, a in enumerate(everyone)
        for b in everyone[index + 1 :]
        if any(_names(b, name) for name in (a.display_name, *a.aliases))
    ]


# ---------------------------------------------------------------- corrections


def merge(conn: sqlite3.Connection, keep_id: int, other_id: int) -> Patient:
    """Two patients are one person: the other's reports and names go to the one kept."""
    if keep_id == other_id:
        raise UserError("Give two different patients to merge.")
    with db.write(conn):
        keep, other = get(conn, keep_id), get(conn, other_id)
        for printed in (other.display_name, *other.aliases):
            keep = _remember(keep, printed, other.sex, other.birth_year)
        db.update_patient(conn, keep)
        db.move_reports(conn, other.id, keep.id)
        db.delete_patient_if_unused(conn, other.id)
    return keep


def assign(conn: sqlite3.Connection, report_id: int, patient_id: int | None) -> Patient:
    """A report is someone else's: patient_id's, or (None) a new patient's.

    The printed name isn't remembered for the patient: it may be a misprint. A patient
    left with no reports is deleted.
    """
    with db.write(conn):
        if (person := db.report_person(conn, report_id)) is None:
            raise UserError(f"There is no report #{report_id}.")
        if patient_id is not None:
            target = get(conn, patient_id)
        elif is_a_name(person.name):
            target = _new_patient(conn, person)
        else:
            raise UserError(f"Report #{report_id} prints no name to give a new patient.")
        db.set_report_patient(conn, report_id, target.id)
        if person.patient_id is not None and person.patient_id != target.id:
            db.delete_patient_if_unused(conn, person.patient_id)
    return target


# ---------------------------------------------------------------- the command


def listing(conn: sqlite3.Connection) -> PatientListing:
    """Every patient with their reports, and the reports matched to no one."""
    people = db.report_people(conn)
    return PatientListing(
        patients=[
            PatientReports(
                **patient.model_dump(),
                reports=[person for person in people if person.patient_id == patient.id],
            )
            for patient in db.patients(conn)
        ],
        unmatched=[person for person in people if person.patient_id is None],
    )


def _report_line(report: ReportPerson) -> str:
    when = report.sample_date or report.report_date or "no date"
    return f"#{report.report_id} {when}" + (f" {report.lab_name}" if report.lab_name else "")


def _print_listing(listed: PatientListing) -> None:
    for patient in listed.patients:
        details = [patient.sex] if patient.sex else []
        if patient.birth_year:
            details.append(f"born about {patient.birth_year}")
        print(f"#{patient.id} {patient.display_name}" + (f" ({', '.join(details)})" if details else ""))
        if patient.aliases:
            print(f"   also printed as: {'; '.join(patient.aliases)}")
        print(f"   reports: {', '.join(map(_report_line, patient.reports)) or 'none'}")
    if listed.unmatched:
        print("Reports matched to no one:")
        for report in listed.unmatched:
            print(f"   {_report_line(report)}: {report.name or 'no name printed'}")


def _list(conn: sqlite3.Connection, args: argparse.Namespace) -> None:
    match_saved(conn)
    listed = listing(conn)
    if args.json:
        print(listed.model_dump_json(indent=2))
    elif not listed.patients and not listed.unmatched:
        logger.info("No reports saved yet; extract one with truetrend-extract.")
    else:
        _print_listing(listed)


def _merge(conn: sqlite3.Connection, args: argparse.Namespace) -> None:
    kept = merge(conn, args.keep, args.other)
    logger.info("Patient #%d is now part of #%d %s.", args.other, kept.id, kept.display_name)


def _assign(conn: sqlite3.Connection, args: argparse.Namespace) -> None:
    target = assign(conn, args.report, args.patient)
    logger.info("Report #%d is now for patient #%d %s.", args.report, target.id, target.display_name)


def _patient_or_new(text: str) -> int | None:
    """argparse type for a patient: a number, or "new" (None)."""
    if text == "new":
        return None
    if text.isdigit():
        return int(text)
    raise argparse.ArgumentTypeError(f"'{text}' is not a patient number or \"new\"")


def _command(args: argparse.Namespace) -> int:
    with closing(db.connect()) as conn:
        args.run(conn, args)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = cli.parser("patients", "List the patients reports are for, and correct who a report is for.")
    parser.add_argument("--json", action="store_true", help="print the list as JSON")
    parser.set_defaults(run=_list)
    actions = parser.add_subparsers()
    merging = actions.add_parser("merge", help="two patients are the same person")
    merging.add_argument("keep", type=int, help="the patient to keep")
    merging.add_argument("other", type=int, help="the patient whose reports and names join it")
    merging.set_defaults(run=_merge)
    assigning = actions.add_parser("assign", help="a report is someone else's")
    assigning.add_argument("report", type=int, help="the report's number")
    assigning.add_argument("patient", type=_patient_or_new, help='the patient\'s number, or "new"')
    assigning.set_defaults(run=_assign)
    return cli.run_command(parser, _command, argv)


if __name__ == "__main__":
    sys.exit(main())
