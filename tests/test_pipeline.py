import pytest

from truetrend.errors import UserError
from truetrend.models import PageExtraction, PageInput
from truetrend.pipeline import extract_pages

INVALID = "invalid"


def reply(results=(), **header):
    data = dict.fromkeys(["patient_name", "age", "sex", "lab_name", "sample_date", "report_date"])
    data.update(header)
    data["results"] = list(results)
    return PageExtraction.model_validate(data)


def row(code="HBA1C", name="Glycosylated Haemoglobin (HbA1c)", value="7.2", unit="%", ref="4.0 - 5.6"):
    return {"test_code": code, "raw_name": name, "value_text": value, "unit": unit, "ref_text": ref}


class ScriptedAsk:
    """Fake Gemma. script[page_number] lists one outcome per attempt: a PageExtraction or INVALID."""

    def __init__(self, script):
        self.script = {page: list(outcomes) for page, outcomes in script.items()}
        self.calls = []

    def __call__(self, page, retry):
        self.calls.append((page.number, retry))
        outcome = self.script[page.number].pop(0)
        if outcome == INVALID:
            PageExtraction.model_validate_json("{broken")  # raises ValidationError, like a truncated reply
        return outcome


def no_transcribe(page):
    raise AssertionError("text pages are never transcribed")


def pages(count):
    return [PageInput(number=n, total=count, mode="text", text=f"page {n} text") for n in range(1, count + 1)]


def test_page_numbers_come_from_code_and_header_is_merged():
    ask = ScriptedAsk(
        {
            1: [reply([row()], patient_name="Mrs. Sunita Patil", lab_name="SUNRISE DIAGNOSTICS")],
            2: [
                reply(
                    [row("HB", "Haemoglobin", "12.1", "g/dL", "12.0 - 15.0")], sample_date="12/09/2026 08:10"
                )
            ],
        }
    )
    out = extract_pages(pages(2), ask, no_transcribe)
    assert [(r.page, r.test_code, r.value_text) for r in out.results] == [
        (1, "HBA1C", "7.2"),
        (2, "HB", "12.1"),
    ]
    assert out.header.patient_name == "Mrs. Sunita Patil"
    assert out.header.lab_name == "SUNRISE DIAGNOSTICS"
    assert out.header.sample_date == "12/09/2026 08:10"
    assert out.header.report_date is None
    assert [(p.page, p.mode, p.results) for p in out.pages] == [(1, "text", 1), (2, "text", 1)]
    assert out.warnings == []
    assert ask.calls == [(1, False), (2, False)]


def test_conflicting_header_keeps_first_and_warns():
    ask = ScriptedAsk(
        {1: [reply(patient_name="Mrs. Sunita Patil")], 2: [reply(patient_name="Mr. Anil Patil")]}
    )
    out = extract_pages(pages(2), ask, no_transcribe)
    assert out.header.patient_name == "Mrs. Sunita Patil"
    assert len(out.warnings) == 1
    assert "page 2" in out.warnings[0] and "Mr. Anil Patil" in out.warnings[0]


def test_sections_reported_at_different_times_are_not_a_conflict():
    # Labs stamp each section with its own report time; only the first is kept, silently.
    ask = ScriptedAsk(
        {1: [reply(report_date="28-Feb-2023 10:26")], 2: [reply(report_date="20-Feb-2023 11:45")]}
    )
    out = extract_pages(pages(2), ask, no_transcribe)
    assert out.header.report_date == "28-Feb-2023 10:26"
    assert out.warnings == []


def test_the_word_null_from_gemma_means_empty():
    ask = ScriptedAsk(
        {
            1: [reply([row(unit="null", ref="N/A")], lab_name="null", sample_date="None")],
            2: [reply(lab_name="HOD Head Office")],
        }
    )
    out = extract_pages(pages(2), ask, no_transcribe)
    assert out.header.lab_name == "HOD Head Office" and out.header.sample_date is None
    assert (out.results[0].unit, out.results[0].ref_text) == (None, None)
    assert out.warnings == []


def test_same_age_or_lab_written_differently_is_not_a_conflict():
    ask = ScriptedAsk(
        {
            1: [reply(age="60 Y", lab_name="LPL-ROHINI (NATIONAL REFERENCE LAB)")],
            2: [reply(age="60", lab_name="NATIONAL REFERENCE LAB")],
            3: [reply(age="61 Years")],
        }
    )
    out = extract_pages(pages(3), ask, no_transcribe)
    assert out.header.age == "60 Y"
    assert len(out.warnings) == 1 and "61 Years" in out.warnings[0]  # a different age still warns


def test_same_header_in_other_case_or_date_format_is_not_a_conflict():
    ask = ScriptedAsk(
        {
            1: [reply(patient_name="Mrs. Sunita Patil", sample_date="12/09/2026 08:10")],
            2: [reply(patient_name="MRS.  SUNITA PATIL", sample_date="12-Sep-2026")],
        }
    )
    assert extract_pages(pages(2), ask, no_transcribe).warnings == []


def test_invalid_reply_is_retried_once():
    ask = ScriptedAsk({1: [INVALID, reply([row()])]})
    out = extract_pages(pages(1), ask, no_transcribe)
    assert ask.calls == [(1, False), (1, True)]
    assert out.pages[0].mode == "text" and len(out.results) == 1


def test_page_failing_twice_is_skipped_with_warning():
    ask = ScriptedAsk({1: [INVALID, INVALID], 2: [reply([row()])]})
    out = extract_pages(pages(2), ask, no_transcribe)
    assert out.pages[0].mode == "failed"
    assert [r.page for r in out.results] == [2]
    assert any("page 1" in w and "skipped" in w for w in out.warnings)
    assert out.replies[0].model_dump() == {"page": 1, "mode": "text", "transcription": None, "reply": None}


def test_every_page_failing_is_fatal():
    ask = ScriptedAsk({1: [INVALID, INVALID], 2: [INVALID, INVALID]})
    with pytest.raises(UserError, match="could not read any page"):
        extract_pages(pages(2), ask, no_transcribe)


def test_text_is_cleaned_and_rows_without_value_dropped():
    ask = ScriptedAsk(
        {
            1: [
                reply(
                    [
                        row(value="  7.2 ", unit="", ref="4.0  -  5.6"),
                        row("HB", "Haemoglobin", "", "g/dL", None),
                    ]
                )
            ]
        }
    )
    out = extract_pages(pages(1), ask, no_transcribe)
    assert [r.model_dump() for r in out.results] == [
        {
            "page": 1,
            "test_code": "HBA1C",
            "raw_name": "Glycosylated Haemoglobin (HbA1c)",
            "value_text": "7.2",
            "flag": None,
            "unit": None,
            "ref_text": "4.0 - 5.6",
        }
    ]
    assert any("HB" in w and "no value" in w for w in out.warnings)


def test_row_whose_name_contradicts_its_code_is_dropped_with_warning():
    # What Gemma vision really did on a scanned page.
    ask = ScriptedAsk(
        {
            1: [
                reply(
                    [
                        row(),
                        row("GLU_F", "Estimated Average Glucose", "160", "mg/dL", None),
                        row("FT4", "Total T4", "8.1", "ug/dL", "5.1 - 14.1"),
                    ]
                )
            ]
        }
    )
    out = extract_pages(pages(1), ask, no_transcribe)
    assert [r.test_code for r in out.results] == ["HBA1C"]
    assert out.pages[0].results == 1
    assert any("'Estimated Average Glucose' is not GLU_F" in w for w in out.warnings)
    assert any("'Total T4' is not FT4" in w for w in out.warnings)
    assert len(out.replies[0].reply.results) == 3  # Gemma's full reply is kept for raw_json


@pytest.mark.parametrize(
    "printed, flag, value",
    [
        ("H 168.0", "H", "168.0"),
        ("L 18.0", "L", "18.0"),
        ("L < 148", "L", "< 148"),
        ("141.0 H", "H", "141.0"),
        ("7.2", None, "7.2"),
        ("< 0.5", None, "< 0.5"),
        ("Low", None, "Low"),  # a word result, not a flag
    ],
)
def test_high_low_flag_is_split_from_the_value(printed, flag, value):
    out = extract_pages(pages(1), ScriptedAsk({1: [reply([row(value=printed)])]}), no_transcribe)
    assert (out.results[0].flag, out.results[0].value_text) == (flag, value)


def test_column_markers_do_not_leak_into_saved_text():
    ask = ScriptedAsk({1: [reply([row(ref="Deficiency | : <10 | Insufficiency : 10 - 30")])]})
    out = extract_pages(pages(1), ask, no_transcribe)
    assert out.results[0].ref_text == "Deficiency : <10 Insufficiency : 10 - 30"


def test_row_repeated_on_the_same_page_is_kept_once():
    # Gemma sometimes lists the same row twice; saving both would double-count it.
    ask = ScriptedAsk({1: [reply([row(), row(value=" 7.2"), row(value="7.3")])]})
    out = extract_pages(pages(1), ask, no_transcribe)
    assert [r.value_text for r in out.results] == ["7.2", "7.3"]
    assert any("HBA1C 7.2 was listed twice" in w for w in out.warnings)


def test_row_without_a_test_name_is_dropped_with_a_clear_warning():
    ask = ScriptedAsk({1: [reply([row(name="  "), row()])]})
    out = extract_pages(pages(1), ask, no_transcribe)
    assert len(out.results) == 1
    assert "page 1: HBA1C had no test name; row dropped" in out.warnings


def test_scanned_page_is_transcribed_once_and_the_transcription_kept():
    scan = PageInput(number=1, total=1, mode="vision", image=b"png")
    transcriptions, asked = [], []

    def transcribe(page):
        transcriptions.append(page.number)
        return "Glycosylated Haemoglobin (HbA1c) 7.2 %"

    def ask(page, retry):
        asked.append(page)
        if not retry:
            PageExtraction.model_validate_json("{broken")
        return reply([row()])

    out = extract_pages([scan], ask, transcribe)
    assert transcriptions == [1]  # the retry reuses the transcription
    assert [(p.mode, p.text) for p in asked] == [("vision", "Glycosylated Haemoglobin (HbA1c) 7.2 %")] * 2
    assert out.pages[0].mode == "vision" and len(out.results) == 1
    assert out.replies[0].transcription == "Glycosylated Haemoglobin (HbA1c) 7.2 %"


def test_same_test_on_two_pages_is_kept_twice_with_warning():
    ask = ScriptedAsk({1: [reply([row()])], 2: [reply([row()])]})
    out = extract_pages(pages(2), ask, no_transcribe)
    assert [(r.page, r.test_code) for r in out.results] == [(1, "HBA1C"), (2, "HBA1C")]
    assert any("HBA1C" in w and "pages 1, 2" in w for w in out.warnings)


def test_replies_are_kept_for_raw_json():
    out = extract_pages(
        pages(1), ScriptedAsk({1: [reply([row()], lab_name="SUNRISE DIAGNOSTICS")]}), no_transcribe
    )
    assert out.replies[0].page == 1 and out.replies[0].mode == "text"
    assert out.replies[0].reply.results[0].value_text == "7.2"


def test_progress_is_logged(caplog):
    caplog.set_level("INFO", logger="truetrend")
    extract_pages(pages(1), ScriptedAsk({1: [reply([row()])]}), no_transcribe)
    assert "page 1/1 · text · 1 result ·" in caplog.text


def test_a_scan_gemma_reads_no_text_off_is_a_failed_page():
    scan = PageInput(number=1, total=2, mode="vision", image=b"png")
    ask = ScriptedAsk({2: [reply([row()])]})
    out = extract_pages([scan, *pages(2)[1:]], ask, lambda page: "  \n ")
    assert [summary.mode for summary in out.pages] == ["failed", "text"]
    assert ask.calls == [(2, False)]  # the empty page is not sent to the extraction step
    assert "page 1: Gemma read no text off the scan; page skipped" in out.warnings
