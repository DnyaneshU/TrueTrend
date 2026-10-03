"""The data passed between the steps of reading a report, what is saved, and what is printed."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.lab_tests import TestCode

PageMode = Literal["text", "vision"]


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
    page: int
    mode: Literal["text", "vision", "failed"]
    results: int
    seconds: float


class PageReply(BaseModel):
    """Gemma's full answer for a page, kept for audit in reports.raw_json."""

    page: int
    mode: PageMode
    transcription: str | None = None  # what Gemma read off a scanned page
    reply: PageExtraction | None = None  # None when the answer was unreadable twice


class Extraction(BaseModel):
    header: Header = Field(default_factory=Header)
    results: list[Result] = Field(default_factory=list)
    pages: list[PageSummary] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    replies: list[PageReply] = Field(default_factory=list)


# --- Normalised and saved.


class Normalized(BaseModel):
    """A result as numbers: the printed value, and the value and normal range in the standard unit."""

    value: float | None = None  # the printed number, in the printed unit
    qualifier: Literal["<", ">", "<=", ">="] | None = None  # "< 148": below what the lab can measure
    value_std: float | None = None  # in unit_std; None when the unit is missing or unknown
    unit_std: str | None = None
    ref_low: float | None = None  # the lab's normal range, in unit_std
    ref_high: float | None = None
    notes: list[str] = Field(default_factory=list)  # why something above could not be filled in


class SavedResult(Result, Normalized):
    """A result as saved: printed, normalised, and checked against its PDF by app.verify."""

    status: Literal["verified", "needs_check", "rejected"] = "needs_check"
    # where the value is printed on its page: (x0, y0, x1, y1) in PDF points from the top left
    bbox: tuple[float, float, float, float] | None = None


class ReportRecord(BaseModel):
    """A row of the reports table."""

    lab_name: str | None
    sample_date: str | None  # ISO YYYY-MM-DD: the sample collection date
    report_date: str | None
    source: Literal["whatsapp", "gmail", "upload", "gmail_import"] = "upload"
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
    """What `python -m app.extract` prints as JSON."""

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
