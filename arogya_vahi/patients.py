"""Which family member a report is for, and the command to list and correct it.

    arogya-patients                         every patient and their reports
    arogya-patients merge KEEP OTHER        OTHER is the same person as KEEP: their reports join
    arogya-patients assign REPORT PATIENT   a report is someone else's (PATIENT may be "new")
    (or: python -m arogya_vahi.patients ...)

A report goes to the patient whose name, or a name printed on one of their earlier
reports, is the same as the one it prints. Names are compared ignoring case, punctuation,
word order and titles ("Mrs. Sunita Patil" = "PATIL SUNITA"); a middle initial only one
of them prints is ignored when both have at least two full words ("Sunita R. Patil" =
"Sunita Patil"). Nothing else is guessed: "S. Patil" or "Sunita Ramesh Patil" is someone
new until the two are merged, and then that name is remembered.

A same-named patient of the other sex, or born more than a year apart (from the printed
ages), is someone else. A report that could be more than one patient, or names no one,
is matched to no one and compared with nothing.
"""

import argparse
import json
import logging
import re
import sqlite3
import sys
import unicodedata
from collections import Counter
from contextlib import closing
from typing import NamedTuple

from arogya_vahi import cli, db
from arogya_vahi.errors import UserError
from arogya_vahi.models import Patient, ReportPerson, Sex

logger = logging.getLogger(__name__)

# Titles printed before a name, and the sex they say (None: either).
TITLES: dict[str, Sex | None] = {
    "mr": "M", "master": "M", "shri": "M", "shree": "M", "sri": "M", "श्री": "M", "चि": "M",
    "mrs": "F", "ms": "F", "miss": "F", "smt": "F", "kum": "F", "kumari": "F",
    "श्रीमती": "F", "सौ": "F", "कु": "F", "कुमारी": "F",
    "dr": None, "डॉ": None, "mx": None,
}  # fmt: skip
SEXES: dict[str, Sex] = {"f": "F", "female": "F", "स्त्री": "F", "m": "M", "male": "M", "पुरुष": "M"}
# "62", "62 Y", "62 Years", "62Y 3M"; "8 Months" and "12 Days" are under a year.
_AGE = re.compile(r"(\d{1,3})\s*(\S*)")
_YEAR_UNITS = {"", "y", "yr", "yrs", "year", "years", "वर्ष", "वर्षे"}
_UNDER_A_YEAR = ("m", "d", "w", "महिन", "दिवस")
MAX_AGE = 120
BIRTH_YEAR_SLACK = 1  # an age is printed in whole years, so two estimates may differ by one


class Match(NamedTuple):
    """Who a report was matched to: a patient (new or not), or no one and why."""

    patient: Patient | None
    new: bool = False
    note: str | None = None  # something the user should know or fix


# ---------------------------------------------------------------- comparing what is printed


def _words(name: str) -> list[str]:
    """A name's words, casefolded, without punctuation (Devanagari vowel signs are kept)."""
    kept = "".join(char if unicodedata.category(char)[0] in "LMN" else " " for char in name.casefold())
    return kept.split()


def _without_titles(words: list[str]) -> list[str]:
    while words and words[0] in TITLES:
        words = words[1:]
    return words


def _key(name: str) -> tuple[Counter[str], Counter[str]]:
    """(full words, initials) of a name without its titles, in any order."""
    words = _without_titles(_words(name))
    return Counter(word for word in words if len(word) > 1), Counter(word for word in words if len(word) == 1)


def same_person_name(a: str | None, b: str | None) -> bool:
    """Two printed names are the same person's (see the module docstring); an unknown name never is."""
    if not a or not b:
        return False
    (words_a, initials_a), (words_b, initials_b) = _key(a), _key(b)
    if not words_a or words_a != words_b:
        return False
    if initials_a == initials_b:
        return True
    one_has_none = not initials_a or not initials_b
    return one_has_none and words_a.total() >= 2


def display_name(printed: str) -> str:
    """How a patient is shown: the printed name without its titles, in title case if it was all capitals."""
    words = [word for word in re.split(r"[\s.,]+", printed) if word]
    while words and "".join(_words(words[0])) in TITLES:
        words = words[1:]
    name = " ".join(words) or printed.strip()
    return name.title() if name.isupper() else name


def sex_of(person: ReportPerson) -> Sex | None:
    """The printed sex ("F", "Female", "स्त्री"), or the one the name's title says ("Mrs.")."""
    for word in _words(person.sex or ""):
        if word in SEXES:
            return SEXES[word]
    words = _words(person.name or "")
    return TITLES.get(words[0]) if words else None


def age_of(text: str | None) -> int | None:
    """Whole years from a printed age: "62 Y" -> 62, "8 Months" -> 0; None if there is no age."""
    match = _AGE.search(text.casefold()) if text else None
    if not match:
        return None
    unit = _leading_letters(match[2])
    if unit in _YEAR_UNITS:
        years = int(match[1])
        return years if years <= MAX_AGE else None
    return 0 if unit.startswith(_UNDER_A_YEAR) else None


def _leading_letters(text: str) -> str:
    """The letters a word starts with, Devanagari vowel signs included ("वर्षे/F" -> "वर्षे")."""
    for end, char in enumerate(text):
        if unicodedata.category(char)[0] not in "LM":
            return text[:end]
    return text


def birth_year_of(person: ReportPerson) -> int | None:
    """The year of birth the printed age says, from the year of the sample (or of the report)."""
    age, when = age_of(person.age), person.sample_date or person.report_date
    return int(when[:4]) - age if age is not None and when else None


def _conflict(patient: Patient, sex: Sex | None, birth_year: int | None) -> str | None:
    """Why a same-named patient can't be the person a report is for, if they can't."""
    if sex and patient.sex and sex != patient.sex:
        return "of the other sex"
    if birth_year and patient.birth_year and abs(birth_year - patient.birth_year) > BIRTH_YEAR_SLACK:
        return f"born about {patient.birth_year}, not {birth_year}"
    return None


# ---------------------------------------------------------------- matching saved reports


def match_report(conn: sqlite3.Connection, person: ReportPerson) -> Match:
    """The patient a report is for, adding them if they are new; call inside the caller's transaction.

    Doesn't set the report's patient_id: the caller saves it with the report.
    """
    if not person.name:
        return Match(None, note="no name is printed, so it is compared with no one")
    sex, birth_year = sex_of(person), birth_year_of(person)
    named = [
        patient
        for patient in db.patients(conn)
        if any(same_person_name(person.name, name) for name in (patient.display_name, *patient.aliases))
    ]
    fitting = [patient for patient in named if not _conflict(patient, sex, birth_year)]
    if len(fitting) > 1:
        ids = " or ".join(f"#{patient.id}" for patient in fitting)
        return Match(None, note=f"'{person.name}' could be patient {ids}; choose with arogya-patients assign")
    if fitting:
        patient = _remember(fitting[0], person.name, sex, birth_year)
        db.update_patient(conn, patient)
        return Match(patient)
    patient = Patient(id=0, display_name=display_name(person.name), sex=sex, birth_year=birth_year)
    patient = patient.model_copy(update={"id": db.add_patient(conn, patient)})
    note = None
    if named:
        reasons = "; ".join(f"#{other.id} is {_conflict(other, sex, birth_year)}" for other in named)
        note = (
            f"'{person.name}' has another patient's name ({reasons}), so is a new patient #{patient.id}. "
            f"If they are the same person: arogya-patients merge {named[0].id} {patient.id}"
        )
    return Match(patient, new=True, note=note)


def match_unmatched(conn: sqlite3.Connection) -> list[tuple[ReportPerson, Match]]:
    """Match every saved report not matched to anyone yet (saved before matching, or ambiguous).

    Reports that name no one are left as they are, silently.
    """
    matches = []
    with conn:
        for person in db.report_people(conn, unmatched=True):
            if not person.name:
                continue
            match = match_report(conn, person)
            if match.patient:
                db.set_report_patient(conn, person.report_id, match.patient.id)
            matches.append((person, match))
    return matches


def log_matches(matches: list[tuple[ReportPerson, Match]]) -> None:
    """Say which earlier reports were matched just now, and what needs the user."""
    matched = sum(match.patient is not None for _, match in matches)
    if matched:
        logger.info("Matched %d saved report(s) to patients; see them with arogya-patients.", matched)
    for person, match in matches:
        if match.note:
            logger.warning("report #%d: %s", person.report_id, match.note)


def _remember(patient: Patient, printed: str, sex: Sex | None, birth_year: int | None) -> Patient:
    """The patient with a new printed name, sex or birth year added; what is known stays."""
    names = (patient.display_name, *patient.aliases)
    aliases = (
        patient.aliases if any(_key(printed) == _key(name) for name in names) else [*patient.aliases, printed]
    )
    return patient.model_copy(
        update={"aliases": aliases, "sex": patient.sex or sex, "birth_year": patient.birth_year or birth_year}
    )


# ---------------------------------------------------------------- corrections


def merge(conn: sqlite3.Connection, keep_id: int, other_id: int) -> Patient:
    """Two patients are one person: the other's reports and names go to the one kept."""
    if keep_id == other_id:
        raise UserError("Give two different patients to merge.")
    with conn:
        keep, other = _patient(conn, keep_id), _patient(conn, other_id)
        for printed in (other.display_name, *other.aliases):
            keep = _remember(keep, printed, other.sex, other.birth_year)
        db.update_patient(conn, keep)
        db.move_reports(conn, other.id, keep.id)
        db.delete_patient(conn, other.id)
    return keep


def assign(conn: sqlite3.Connection, report_id: int, patient: str) -> Patient:
    """A report is someone else's: an existing patient's (by id) or, with "new", a new patient's.

    The printed name isn't remembered for the patient: it may be a misprint. A patient
    left with no reports is deleted.
    """
    with conn:
        people = {person.report_id: person for person in db.report_people(conn)}
        if report_id not in people:
            raise UserError(f"There is no report #{report_id}.")
        person = people[report_id]
        if patient == "new":
            if not person.name:
                raise UserError(f"Report #{report_id} prints no name to give a new patient.")
            target = Patient(
                id=0,
                display_name=display_name(person.name),
                sex=sex_of(person),
                birth_year=birth_year_of(person),
            )
            target = target.model_copy(update={"id": db.add_patient(conn, target)})
        elif patient.isdigit():
            target = _patient(conn, int(patient))
        else:
            raise UserError(f"'{patient}' is not a patient number or \"new\".")
        db.set_report_patient(conn, report_id, target.id)
        previous = person.patient_id
        if (
            previous is not None
            and previous != target.id
            and not any(
                other.patient_id == previous for other in people.values() if other.report_id != report_id
            )
        ):
            db.delete_patient(conn, previous)
    return target


def _patient(conn: sqlite3.Connection, patient_id: int) -> Patient:
    for patient in db.patients(conn):
        if patient.id == patient_id:
            return patient
    raise UserError(f"There is no patient #{patient_id}; see them with arogya-patients.")


# ---------------------------------------------------------------- the command


def listing(conn: sqlite3.Connection) -> dict:
    """Every patient with their reports, and the reports matched to no one."""
    people = db.report_people(conn)
    return {
        "patients": [
            {
                **patient.model_dump(),
                "reports": [person.model_dump() for person in people if person.patient_id == patient.id],
            }
            for patient in db.patients(conn)
        ],
        "unmatched": [person.model_dump() for person in people if person.patient_id is None],
    }


def _report_line(report: dict) -> str:
    when = report["sample_date"] or report["report_date"] or "no date"
    return f"#{report['report_id']} {when}" + (f" {report['lab_name']}" if report["lab_name"] else "")


def _print_listing(listed: dict) -> None:
    for patient in listed["patients"]:
        details = [patient["sex"]] if patient["sex"] else []
        if patient["birth_year"]:
            details.append(f"born about {patient['birth_year']}")
        print(f"#{patient['id']} {patient['display_name']}" + (f" ({', '.join(details)})" if details else ""))
        if patient["aliases"]:
            print(f"   also printed as: {'; '.join(patient['aliases'])}")
        reports = ", ".join(map(_report_line, patient["reports"])) or "none"
        print(f"   reports: {reports}")
    if listed["unmatched"]:
        print("Reports matched to no one:")
        for report in listed["unmatched"]:
            print(f"   {_report_line(report)}: {report['name'] or 'no name printed'}")


def _command(args: argparse.Namespace) -> int:
    with closing(db.connect()) as conn:
        if args.action == "merge":
            kept = merge(conn, args.keep, args.other)
            logger.info("Patient #%d is now part of #%d %s.", args.other, kept.id, kept.display_name)
        elif args.action == "assign":
            target = assign(conn, args.report, args.patient)
            logger.info("Report #%d is now for patient #%d %s.", args.report, target.id, target.display_name)
        else:
            log_matches(match_unmatched(conn))
            listed = listing(conn)
            if args.json:
                print(json.dumps(listed, ensure_ascii=False, indent=2))
            elif not listed["patients"] and not listed["unmatched"]:
                logger.info("No reports saved yet; extract one with arogya-extract.")
            else:
                _print_listing(listed)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = cli.parser("patients", "List the patients reports are for, and correct who a report is for.")
    parser.add_argument("--json", action="store_true", help="print the list as JSON")
    actions = parser.add_subparsers(dest="action")
    merging = actions.add_parser("merge", help="two patients are the same person")
    merging.add_argument("keep", type=int, help="the patient to keep")
    merging.add_argument("other", type=int, help="the patient whose reports and names join it")
    assigning = actions.add_parser("assign", help="a report is someone else's")
    assigning.add_argument("report", type=int, help="the report's number")
    assigning.add_argument("patient", help='the patient\'s number, or "new" for a new patient')
    return cli.run_command(parser, _command, argv)


if __name__ == "__main__":
    sys.exit(main())
