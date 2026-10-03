"""The data passed between the steps of reading a report, what is saved, and what is printed."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from arogya_vahi.lab_tests import TestCode
from arogya_vahi.text import Qualifier

PageMode = Literal["text", "vision"]
Status = Literal["verified", "needs_check", "rejected"]
Source = Literal["whatsapp", "gmail", "upload", "gmail_import"]  # how a report arrived


class PageInput(BaseModel):
    """One page as Gemma will see it: its rebuilt text, or (for a scan) a PNG of it."""

    model_config = ConfigDict(frozen=True)

    number: int  # 1-based, assigned by code
    total: int
    mode: PageMode
    text: str = ""  # for a scan, filled in by transcription
    image: bytes | None = None  # PNG, scans only


# --- Gemma's reply. No docstrings on these two: Pydantic would put them into the JSON
# --- schema sent to Gemma, and the schema must stay exactly the one that was tested.


class ExtractedResult(BaseModel):
    test_code: TestCode
    raw_name: str
    value_text: str
    unit: str | None
    ref_text: str | None


class PageExtraction(BaseModel):
    patient_name: str | None
    age: str | None
    sex: str | None
    lab_name: str | None
    sample_date: str | None
    report_date: str | None
    results: list[ExtractedResult]


# --- A report, merged from its pages.


class Header(BaseModel):
    """Report details as printed (the first page that prints each one wins)."""

    patient_name: str | None = None
    age: str | None = None
    sex: str | None = None
    lab_name: str | None = None
    sample_date: str | None = None
    report_date: str | None = None


class Result(BaseModel):
    """One test result, copied as printed; the page number comes from code, not Gemma."""

    page: int
    test_code: str
    raw_name: str
    value_text: str
    flag: str | None = None  # the lab's high/low mark printed with the value (H, L, ...)
    unit: str | None = None
    ref_text: str | None = None


class PageSummary(BaseModel):
    """How one page went: how it was read, how many results it gave, how long it took."""

    page: int
    mode: PageMode | Literal["failed"]
    results: int
    seconds: float


class PageReply(BaseModel):
    """Gemma's full answer for a page, kept for audit in reports.raw_json."""

    page: int
    mode: PageMode
    transcription: str | None = None  # what Gemma read off a scanned page
    reply: PageExtraction | None = None  # None when the answer was unreadable twice


class Extraction(BaseModel):
    """A report merged from Gemma's answers for each page, before normalising and verifying."""

    header: Header = Field(default_factory=Header)
    results: list[Result] = Field(default_factory=list)
    pages: list[PageSummary] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    replies: list[PageReply] = Field(default_factory=list)


# --- Normalised and saved.


class Normalized(BaseModel):
    """A result as numbers: the printed value, and the value and normal range in the standard unit."""

    value: float | None = None  # the printed number, in the printed unit
    qualifier: Qualifier | None = None  # "< 148": below what the lab can measure
    value_std: float | None = None  # in unit_std; None when the unit is missing or unknown
    unit_std: str | None = None
    ref_low: float | None = None  # the lab's normal range, in unit_std
    ref_high: float | None = None
    notes: list[str] = Field(default_factory=list)  # why something above could not be filled in


class SavedResult(Result, Normalized):
    """A result as saved: printed, normalised, and checked against its PDF by arogya_vahi.verify."""

    status: Status = "needs_check"
    # where the value is printed on its page: (x0, y0, x1, y1) in PDF points from the top left
    bbox: tuple[float, float, float, float] | None = None
    ref_verified: bool = False  # the normal range's limits are printed in the value's row


class ReportRecord(BaseModel):
    """A row of the reports table."""

    lab_name: str | None
    sample_date: str | None  # ISO YYYY-MM-DD: the sample collection date
    report_date: str | None
    source: Source = "upload"
    file_path: str
    sha256: str
    is_scanned: bool
    patient_name_raw: str | None  # as printed; matched to a patient later
    patient_age_raw: str | None
    patient_sex_raw: str | None
    extract_model: str
    extract_seconds: float
    raw_json: str  # Gemma's reply for every page


class ReportOutput(BaseModel):
    """What `python -m arogya_vahi.extract` prints as JSON."""

    report_id: int
    file: str
    sha256: str
    model: str
    seconds: float
    patient_name: str | None
    age: str | None
    sex: str | None
    lab_name: str | None
    sample_date: str | None  # ISO YYYY-MM-DD, or None when it could not be read
    sample_date_text: str | None  # as printed
    report_date: str | None
    report_date_text: str | None
    pages: list[PageSummary]
    warnings: list[str]
    results: list[SavedResult]


# --- Timelines and changes between reports.


class TimelinePoint(BaseModel):
    """One saved result on its test's timeline, dated by the sample collection date."""

    result_id: int
    report_id: int
    patient_name: str | None  # as printed on the report
    test_code: str
    sample_date: date
    lab_name: str | None
    value_text: str  # as printed
    value: float | None  # the printed number, in the printed unit
    unit: str | None  # as printed
    qualifier: Qualifier | None
    value_std: float | None  # compared in this unit
    unit_std: str | None
    ref_text: str | None  # that lab's normal range as printed
    ref_low: float | None  # ... and in unit_std
    ref_high: float | None
    ref_verified: bool  # its limits are printed in the value's row
    status: Status
    page: int
    file_path: str  # the stored original, to open it at `page`


ChangeKind = Literal["real_increase", "real_decrease", "within_normal_variation", "not_judged"]


class Change(BaseModel):
    """Two consecutive results of one test, and whether the difference is more than normal variation."""

    before: TimelinePoint
    after: TimelinePoint
    kind: ChangeKind
    same_lab: bool
    percent: float | None = None  # (after - before) / before x 100
    rcv_percent: float | None = None  # the threshold used, in the change's direction: a bigger change is real
    reason: str | None = None  # why it was not judged


# --- The Marathi summary.

FindingKind = Literal[
    "trend_increase", "trend_decrease", "real_increase", "real_decrease", "above_range", "below_range"
]


class Finding(BaseModel):
    """Something worth saying about one test in the latest report, found by rules over verified results."""

    kind: FindingKind
    test_code: str
    slots: dict[str, str]  # placeholder -> its Marathi text, numbers included, filled by code


class Summary(BaseModel):
    """What arogya-summary reports: at most 3 Marathi sentences, and questions for the doctor."""

    latest_sample_date: date | None = None  # None when no saved report has a sample date
    sentences: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)  # most important first; sentences tell the first
    to_check: int = 0  # tests of the latest report whose result still needs checking
    changes: list[Change] = Field(default_factory=list)  # every consecutive pair, judged
    other_people: list[str] = Field(default_factory=list)  # names on reports left out: not this person
    reports_left_out: int = 0  # earlier reports naming someone else, or no one
