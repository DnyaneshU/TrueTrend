"""The data passed between the steps of reading a report, and what the CLI prints."""

from typing import Literal

from pydantic import BaseModel, Field

from app.gemma import PageExtraction

PageMode = Literal["text", "vision"]


class Header(BaseModel):
    """Report details as printed (the first page that prints each one wins)."""

    patient_name: str | None = None
    age: str | None = None
    sex: str | None = None
    lab_name: str | None = None
    sample_date: str | None = None
    report_date: str | None = None


class Result(BaseModel):
    """One MVP test result, copied as printed; page numbers come from code, not Gemma."""

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
    """Gemma's full answer for a page, kept for audit (reports.raw_json)."""

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


class Normalized(BaseModel):
    """A result as numbers: the printed value, and the value and normal range in the standard unit."""

    value: float | None = None  # the printed number, in the printed unit
    qualifier: Literal["<", ">", "<=", ">="] | None = None  # "< 148" is below what the lab can measure
    value_std: float | None = None  # in unit_std; None when the unit is missing or unknown
    unit_std: str | None = None
    ref_low: float | None = None  # the lab's normal range, in unit_std
    ref_high: float | None = None
    notes: list[str] = Field(default_factory=list)  # why something above could not be filled in


class SavedResult(Result, Normalized):
    status: Literal["verified", "needs_check", "rejected"] = "needs_check"


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
