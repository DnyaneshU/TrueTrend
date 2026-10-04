"""Make the synthetic sample reports in this folder.

    python samples/make_samples.py

Nobody's real report is in this repo, so these stand in for one: made-up people, made-up
labs, made-up values. They exist so anyone can try the app, and so the README's pictures
and the demo show a timeline without anyone's health in it.

The values are chosen to exercise the parts worth seeing: a test that rises enough to
beat its Reference Change Value, one that drifts inside normal variation, one printed
outside the lab's own range, a value with a "<" qualifier, and two labs printing the
same test in different units and layouts.
"""

import sys
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

OUT = Path(__file__).resolve().parent
A4 = pymupdf.paper_rect("a4")
MARGIN = 54.0
# The PDF base-14 fonts, so a sample needs no font file: Helvetica and Times, each with its bold.
BOLD_OF = {"helv": "hebo", "tiro": "tibo"}


@dataclass
class Lab:
    """How one lab lays its report out, so the samples are not four copies of one layout."""

    name: str
    address: str
    columns: tuple[str, ...]
    widths: tuple[float, ...]
    rule: bool = True  # draws a line under the table head
    font: str = "helv"


@dataclass
class Report:
    """One made-up report: who it is for, which lab, and what it printed."""

    lab: Lab
    patient: str
    age: str
    sex: str
    sample_date: str
    report_date: str
    rows: list[tuple[str, str, str, str]]  # test, value, unit, normal range
    note: str | None = None
    footer: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- the labs

SUNRISE = Lab(
    name="SUNRISE DIAGNOSTICS",
    address="Plot 14, Shivaji Road, Pune 411004  |  NABL accredited",
    columns=("INVESTIGATION", "RESULT", "UNITS", "BIOLOGICAL REF. RANGE"),
    widths=(0.40, 0.15, 0.15, 0.30),
)

MAITRI = Lab(
    name="Maitri Pathology Laboratory",
    address="Near Bus Stand, Nashik 422001  |  Dr. A. V. Kale, MD (Path)",
    columns=("Test Name", "Observed Value", "Unit", "Reference Interval"),
    widths=(0.42, 0.16, 0.14, 0.28),
    rule=False,
    font="tiro",
)

CITYCARE = Lab(
    name="CityCare Labs",
    address="2nd Floor, Mall Road, Mumbai 400050",
    columns=("PARAMETER", "VALUE", "UNIT", "NORMAL RANGE"),
    widths=(0.44, 0.14, 0.14, 0.28),
)

# ---------------------------------------------------------------- the reports
#
# One person, three visits over nine months. The first two are at the same lab, the
# third at another, because that is how it goes in a family -- and because the two
# cases are judged against different thresholds, which is worth being able to see.
#
# Every value below was checked against the real Reference Change Value for its test
# (arogya_vahi.change) before being written here, so the samples show each verdict
# the app can give, and none of them by accident:
#
#   HbA1c   6.8 -> 7.6  same lab, +11.8% against a 4.2% threshold   -> real increase
#           7.6 -> 8.9  across labs, +17.1% against 9.7%            -> real increase
#                                                 (three in a row: a rising trend)
#   Hb     11.9 -> 12.1 same lab, +1.7% against 9.8%                -> normal variation
#          12.1 -> 12.0 across labs, and haemoglobin has no sourced
#                       between-lab CV                              -> not judged, and says so
#   Chol    196 -> 188  same lab, -4.1% against -16.8%              -> normal variation
#           188 -> 236  across labs, +25.5% against 24.3%           -> real increase
#   Vit D  18.4 -> 31.0 same lab, +68.5% against 26.5%              -> real increase
#                       (and 18.4 is below the lab's own range: both things are said)
#
# The third report also prints a "< 148" vitamin B12, an HDL with a "> 50" range and a
# post-prandial glucose, which has no published CVi -- so it is never judged, by design.
#
# Anil's single report is here so the app has two people to tell apart: same surname,
# different sex, and a sample date shared with Sunita's third report.

SUNITA = dict(patient="Mrs. Sunita Patil", age="62 Y", sex="F")

REPORTS = [
    Report(
        lab=SUNRISE,
        **SUNITA,
        sample_date="12/01/2026",
        report_date="13/01/2026",
        rows=[
            ("Glycosylated Haemoglobin (HbA1c)", "6.8", "%", "4.0 - 5.6"),
            ("Glucose, Fasting", "126", "mg/dL", "70 - 100"),
            ("Haemoglobin", "11.9", "g/dL", "12.0 - 15.0"),
            ("Total Cholesterol", "196", "mg/dL", "< 200"),
            ("Triglycerides", "158", "mg/dL", "< 150"),
            ("HDL Cholesterol", "44", "mg/dL", "> 50"),
            ("Thyroid Stimulating Hormone (TSH)", "3.10", "uIU/mL", "0.27 - 4.20"),
            ("Vitamin D, 25 Hydroxy", "18.4", "ng/mL", "30 - 100"),
            ("Creatinine, Serum", "0.8", "mg/dL", "0.6 - 1.1"),
        ],
        footer=["* Results relate only to the sample tested.", "Verified by: Dr. S. R. Kulkarni, MD"],
    ),
    Report(
        lab=SUNRISE,  # the same lab as January: this pair is judged on the tighter threshold
        **SUNITA,
        sample_date="20/05/2026",
        report_date="21/05/2026",
        rows=[
            ("Glycosylated Haemoglobin (HbA1c)", "7.6", "%", "4.0 - 5.6"),
            ("Glucose, Fasting", "138", "mg/dL", "70 - 100"),
            ("Haemoglobin", "12.1", "g/dL", "12.0 - 15.0"),
            ("Total Cholesterol", "188", "mg/dL", "< 200"),
            ("Triglycerides", "152", "mg/dL", "< 150"),
            ("HDL Cholesterol", "45", "mg/dL", "> 50"),
            ("Thyroid Stimulating Hormone (TSH)", "2.84", "uIU/mL", "0.27 - 4.20"),
            ("Vitamin D, 25 Hydroxy", "31.0", "ng/mL", "30 - 100"),
            ("Creatinine, Serum", "0.9", "mg/dL", "0.6 - 1.1"),
        ],
        footer=["* Results relate only to the sample tested.", "Verified by: Dr. S. R. Kulkarni, MD"],
    ),
    Report(
        lab=MAITRI,  # a different lab: the between-lab CV is added to every comparison
        **SUNITA,
        sample_date="28/09/2026",
        report_date="29/09/2026",
        rows=[
            ("HbA1c (Glycated Haemoglobin)", "8.9", "%", "4.0 - 5.6"),
            ("Fasting Blood Sugar", "171", "mg/dL", "70 - 100"),
            ("Post Prandial Blood Sugar", "246", "mg/dL", "< 140"),
            ("Haemoglobin", "12.0", "g/dL", "12.0 - 15.0"),
            ("Cholesterol, Total", "236", "mg/dL", "< 200"),
            ("Triglyceride", "164", "mg/dL", "< 150"),
            ("HDL Cholesterol", "42", "mg/dL", "> 50"),
            ("LDL Cholesterol", "151", "mg/dL", "< 100"),
            ("Vitamin D (25-OH)", "34.5", "ng/mL", "30 - 100"),
            ("Vitamin B12", "< 148", "pg/mL", "211 - 911"),
            ("T.S.H.", "3.46", "uIU/mL", "0.27 - 4.20"),
            ("Uric Acid", "5.2", "mg/dL", "2.6 - 6.0"),
            ("Urea", "26", "mg/dL", "15 - 40"),
        ],
        note="Sample collected after 10 hours fasting, as advised.",
        footer=["Dr. A. V. Kale, MD (Path)"],
    ),
    Report(
        lab=CITYCARE,
        patient="Mr. Anil Patil",
        age="66 Y",
        sex="M",
        sample_date="28/09/2026",
        report_date="28/09/2026",
        rows=[
            ("HbA1c", "5.4", "%", "4.0 - 5.6"),
            ("Glucose (F)", "92", "mg/dL", "70 - 100"),
            ("Haemoglobin", "14.6", "g/dL", "13.0 - 17.0"),
            ("Total Cholesterol", "174", "mg/dL", "< 200"),
            ("Creatinine", "1.0", "mg/dL", "0.7 - 1.3"),
        ],
        footer=["CityCare Labs  |  This is a computer-generated report."],
    ),
]


# ---------------------------------------------------------------- drawing


class Sheet:
    """A page being written down, which starts a new one when it runs out of room."""

    def __init__(self, doc: pymupdf.Document, lab: Lab):
        self.doc = doc
        self.lab = lab
        self.page = doc.new_page(width=A4.width, height=A4.height)
        self.y = MARGIN

    def room_for(self, height: float) -> None:
        if self.y + height > A4.height - MARGIN - 40:
            self.page = self.doc.new_page(width=A4.width, height=A4.height)
            self.y = MARGIN

    def text(self, x: float, s: str, size: float = 9.5, bold: bool = False, grey: bool = False) -> None:
        font = BOLD_OF[self.lab.font] if bold else self.lab.font
        self.page.insert_text(
            (x, self.y),
            s,
            fontname=font,
            fontsize=size,
            color=(0.42, 0.42, 0.45) if grey else (0, 0, 0),
        )

    def line(self, width: float = 0.7, grey: float = 0.75) -> None:
        self.page.draw_line(
            (MARGIN, self.y), (A4.width - MARGIN, self.y), color=(grey, grey, grey), width=width
        )


def columns_at(widths: tuple[float, ...]) -> list[float]:
    """Where each column starts, across the printable width."""
    usable = A4.width - 2 * MARGIN
    at, x = [], MARGIN
    for share in widths:
        at.append(x)
        x += share * usable
    return at


def draw(report: Report) -> pymupdf.Document:
    doc = pymupdf.open()
    sheet = Sheet(doc, report.lab)
    at = columns_at(report.lab.widths)

    # The lab's letterhead.
    sheet.y += 6
    sheet.text(MARGIN, report.lab.name, size=15, bold=True)
    sheet.y += 14
    sheet.text(MARGIN, report.lab.address, size=8, grey=True)
    sheet.y += 10
    sheet.line(width=1.1, grey=0.45)
    sheet.y += 20

    # Who it is for, printed as labs print it: two columns of label-and-value.
    left = [("Patient Name", report.patient), ("Age / Sex", f"{report.age} / {report.sex}")]
    right = [("Sample Collected", report.sample_date), ("Report Date", report.report_date)]
    for (label_l, value_l), (label_r, value_r) in zip(left, right, strict=True):
        sheet.text(MARGIN, f"{label_l}", size=8.5, grey=True)
        sheet.text(MARGIN + 92, f": {value_l}", size=9.5, bold=True)
        sheet.text(MARGIN + 290, f"{label_r}", size=8.5, grey=True)
        sheet.text(MARGIN + 382, f": {value_r}", size=9.5)
        sheet.y += 15

    sheet.y += 8
    sheet.text(MARGIN, "BIOCHEMISTRY   |   HAEMATOLOGY", size=9, bold=True, grey=True)
    sheet.y += 16

    # The table head.
    for x, column in zip(at, report.lab.columns, strict=True):
        sheet.text(x, column, size=8.5, bold=True)
    sheet.y += 5
    if report.lab.rule:
        sheet.line()
    sheet.y += 13

    # The results. The value is printed as its own word so verify.py can find it.
    for test, value, unit, ref in report.rows:
        sheet.room_for(16)
        sheet.text(at[0], test, size=9.5)
        sheet.text(at[1], value, size=9.5, bold=True)
        sheet.text(at[2], unit, size=9)
        sheet.text(at[3], ref, size=9, grey=True)
        sheet.y += 16

    sheet.y += 4
    sheet.line(grey=0.85)
    sheet.y += 16

    if report.note:
        sheet.text(MARGIN, report.note, size=8.5, grey=True)
        sheet.y += 18

    for line in report.footer:
        sheet.text(MARGIN, line, size=8, grey=True)
        sheet.y += 11

    sheet.y += 10
    sheet.text(
        MARGIN, "SAMPLE REPORT - made up for testing. Not a real person's result.", size=7.5, grey=True
    )
    return doc


def name_of(report: Report) -> str:
    """A file name that says what the sample is, as a phone would show it."""
    who = report.patient.split()[-1].lower()
    day, month, year = report.sample_date.split("/")
    lab = report.lab.name.split()[0].lower()
    return f"{who}-{year}-{month}-{day}-{lab}.pdf"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for report in REPORTS:
        path = OUT / name_of(report)
        with draw(report) as doc:
            doc.save(path)
        print(f"{path.name}  ({path.stat().st_size // 1024} KB, {len(report.rows)} results)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
