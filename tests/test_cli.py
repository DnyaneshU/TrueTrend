import json
from contextlib import closing

import pytest

from app import db, gemma
from app.config import settings
from app.errors import ExtractError
from app.extract import main, run
from app.models import PageExtraction

GOOD_REPLY = {
    "patient_name": "Mrs. Sunita Patil",
    "age": "62 Y",
    "sex": "F",
    "lab_name": "SUNRISE DIAGNOSTICS",
    "sample_date": "12/09/2026 08:10",
    "report_date": "13/09/2026 14:02",
    "results": [
        {
            "test_code": "HBA1C",
            "raw_name": "Glycosylated Haemoglobin (HbA1c)",
            "value_text": "7.2",
            "unit": "%",
            "ref_text": "4.0 - 5.6",
        },
        {
            "test_code": "HB",
            "raw_name": "Haemoglobin",
            "value_text": "12.1",
            "unit": "g/dL",
            "ref_text": "12.0 - 15.0",
        },
    ],
}
EMPTY_REPLY = {
    **dict.fromkeys(["patient_name", "age", "sex", "lab_name", "sample_date", "report_date"]),
    "results": [],
}


class FakeGemma:
    """Stands in for gemma.extract_results and gemma.transcribe.

    Page 1 gets `first`; other pages get an empty reply.
    """

    def __init__(self, first=GOOD_REPLY):
        self.first, self.pages, self.transcribed = first, [], []

    def ask(self, page, model, retry=False):
        self.pages.append(page)
        return PageExtraction.model_validate(self.first if page.number == 1 else EMPTY_REPLY)

    def transcribe(self, page, model):
        self.transcribed.append(page.number)
        return f"transcription of page {page.number}"


@pytest.fixture
def storage(tmp_path, monkeypatch):
    """Point the app's storage at a temp folder so tests never touch the real storage/."""
    monkeypatch.setattr(settings, "storage_dir", tmp_path / "storage")
    return tmp_path / "storage"


@pytest.fixture
def use_gemma(monkeypatch):
    def install(fake):
        monkeypatch.setattr(gemma, "extract_results", fake.ask)
        monkeypatch.setattr(gemma, "transcribe", fake.transcribe)
        return fake

    return install


@pytest.fixture
def fake_gemma(use_gemma):
    return use_gemma(FakeGemma())


def query(sql, *args):
    with closing(db.connect()) as conn:
        return [tuple(row) for row in conn.execute(sql, args)]


def test_run_saves_report_and_returns_output(storage, fake_gemma, make_pdf, report_page):
    pdf = make_pdf([report_page])
    out = run(pdf)
    assert out.report_id == 1
    assert out.model == "gemma4:e4b"
    assert out.patient_name == "Mrs. Sunita Patil" and out.lab_name == "SUNRISE DIAGNOSTICS"
    assert (out.sample_date, out.sample_date_text) == ("2026-09-12", "12/09/2026 08:10")
    assert (out.report_date, out.report_date_text) == ("2026-09-13", "13/09/2026 14:02")
    assert out.pages[0].mode == "text"
    assert out.warnings == []
    assert [(r.test_code, r.value_text, r.status) for r in out.results] == [
        ("HBA1C", "7.2", "verified"),
        ("HB", "12.1", "verified"),
    ]

    (report,) = query(
        "SELECT sample_date, source, file_path, is_scanned, patient_name_raw, extract_model, raw_json "
        "FROM reports"
    )
    assert report[:2] == ("2026-09-12", "upload")
    assert report[2].endswith(f"{out.sha256}.pdf")
    assert report[3:6] == (0, "Mrs. Sunita Patil", "gemma4:e4b")
    assert json.loads(report[6])["pages"][0]["reply"]["results"][0]["value_text"] == "7.2"
    assert query(
        "SELECT test_code, raw_value_text, flag, page, status, check_notes FROM results ORDER BY id"
    ) == [
        ("HBA1C", "7.2", None, 1, "verified", None),
        ("HB", "12.1", None, 1, "verified", None),
    ]
    assert (storage / "originals" / f"{out.sha256}.pdf").read_bytes() == pdf.read_bytes()


def test_saved_results_are_normalised(storage, use_gemma, make_pdf, report_page):
    reply = {
        **GOOD_REPLY,
        "results": [
            {
                "test_code": "VITD",
                "raw_name": "Vitamin D, 25 Hydroxy",
                "value_text": "150.00",
                "unit": "nmol/L",
                "ref_text": "75.00 - 250.00",
            },
            {
                "test_code": "B12",
                "raw_name": "Vitamin B12",
                "value_text": "L < 148",
                "unit": "pg/mL",
                "ref_text": "187 - 833",
            },
            {
                "test_code": "HBA1C",
                "raw_name": "HbA1c",
                "value_text": "7.2",
                "unit": "mg",
                "ref_text": "4.0 - 5.6",
            },
        ],
    }
    use_gemma(FakeGemma(first=reply))
    out = run(make_pdf([report_page]))
    vitd, b12, hba1c = out.results
    assert (vitd.value, vitd.value_std, vitd.unit_std) == (150.0, 60.0962, "ng/mL")
    assert (vitd.ref_low, vitd.ref_high) == (30.0481, 100.16)
    assert (b12.flag, b12.qualifier, b12.value_std) == ("L", "<", 148.0)
    assert (hba1c.value_std, hba1c.notes) == (None, ["unit 'mg' is not a known unit for HBA1C"])
    assert [result.status for result in out.results] == ["needs_check"] * 3
    assert query(
        "SELECT test_code, value, qualifier, value_std, unit_std, ref_low, ref_high, check_notes "
        "FROM results ORDER BY id"
    ) == [
        ("VITD", 150.0, None, 60.0962, "ng/mL", 30.0481, 100.16,
         "150.00 is not printed on page 1 in a row naming Vitamin D (25-OH)"),
        ("B12", 148.0, "<", 148.0, "pg/mL", 187.0, 833.0,
         "< 148 is not printed on page 1 in a row naming Vitamin B12"),
        ("HBA1C", 7.2, None, None, None, None, None, "unit 'mg' is not a known unit for HBA1C"),
    ]  # fmt: skip


def test_same_file_twice_is_skipped_without_calling_gemma(storage, fake_gemma, make_pdf, report_page, caplog):
    caplog.set_level("INFO", logger="app")
    pdf = make_pdf([report_page])
    run(pdf)
    calls = len(fake_gemma.pages)
    assert run(pdf) is None
    assert len(fake_gemma.pages) == calls
    assert "already extracted as report #1" in caplog.text


def test_force_replaces_the_old_report(storage, fake_gemma, make_pdf, report_page):
    pdf = make_pdf([report_page])
    first = run(pdf)
    second = run(pdf, force=True)
    assert second.report_id != first.report_id
    assert query("SELECT COUNT(*) FROM reports") == [(1,)]
    assert query("SELECT COUNT(*) FROM results") == [(2,)]


def test_missing_date_and_no_tests_are_warnings_not_errors(storage, use_gemma, make_pdf, report_page):
    use_gemma(FakeGemma(first=EMPTY_REPLY))
    out = run(make_pdf([report_page]))
    assert any("No sample collection date" in w for w in out.warnings)
    assert any("No MVP tests" in w for w in out.warnings)
    assert query("SELECT COUNT(*) FROM reports") == [(1,)]


def test_unreadable_date_is_a_warning(storage, use_gemma, make_pdf, report_page):
    use_gemma(FakeGemma(first={**GOOD_REPLY, "sample_date": "Sep 2026"}))
    out = run(make_pdf([report_page]))
    assert out.sample_date is None and out.sample_date_text == "Sep 2026"
    assert any("could not be read as a date" in w for w in out.warnings)


def test_sample_date_in_the_future_is_a_warning(storage, use_gemma, make_pdf, report_page):
    # e.g. a misread year; it would otherwise sit at the end of the timeline unnoticed
    use_gemma(FakeGemma(first={**GOOD_REPLY, "sample_date": "12/09/2096"}))
    out = run(make_pdf([report_page]))
    assert out.sample_date == "2096-09-12"
    assert any("2096-09-12 is in the future" in w for w in out.warnings)


def test_scanned_page_is_transcribed_and_marks_the_report(storage, fake_gemma, make_pdf, report_page):
    run(make_pdf([report_page, []]))
    assert fake_gemma.transcribed == [2]
    assert [(p.mode, p.text) for p in fake_gemma.pages][1] == ("vision", "transcription of page 2")
    assert query("SELECT is_scanned FROM reports") == [(1,)]


def test_ollama_failing_mid_report_saves_nothing(storage, use_gemma, make_pdf, report_page):
    class FailsOnPage2(FakeGemma):
        def ask(self, page, model, retry=False):
            if page.number == 2:
                raise ExtractError(
                    "Lost the connection to Ollama while reading the report. Is it still running?"
                )
            return super().ask(page, model, retry)

    use_gemma(FailsOnPage2())
    with pytest.raises(ExtractError):
        run(make_pdf([report_page, report_page]))
    assert query("SELECT COUNT(*) FROM reports") == [(0,)]


def test_path_with_apostrophe_and_spaces(storage, fake_gemma, make_pdf, report_page):
    pdf = make_pdf([report_page], name="Dnyanesh's Asus/lab reports/Mom's report 2026.pdf")
    out = run(pdf)
    assert out.report_id == 1
    assert (storage / "originals" / f"{out.sha256}.pdf").exists()


def test_main_prints_json_on_stdout(storage, fake_gemma, make_pdf, report_page, capsys):
    assert main([str(make_pdf([report_page]))]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["results"][0]["value_text"] == "7.2"
    assert "page 1/1 · text · 2 results" in captured.err


def test_main_keeps_non_ascii_text_readable(storage, use_gemma, make_pdf, report_page, capsys):
    reply = {
        **GOOD_REPLY,
        "patient_name": "श्रीमती सुनीता पाटील",
        "results": [
            {
                "test_code": "TSH",
                "raw_name": "TSH",
                "value_text": "4.82",
                "unit": "µIU/mL",
                "ref_text": "0.35 - 5.50",
            },
        ],
    }
    use_gemma(FakeGemma(first=reply))
    assert main([str(make_pdf([report_page]))]) == 0
    stdout = capsys.readouterr().out
    assert "श्रीमती सुनीता पाटील" in stdout and "µIU/mL" in stdout


def test_main_reports_errors_in_one_line(storage, tmp_path, capsys):
    missing = tmp_path / "missing.pdf"
    assert main([str(missing)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.strip() == f"error: File not found: {missing}"


def test_main_ctrl_c_saves_nothing(storage, use_gemma, make_pdf, report_page, capsys):
    class Interrupted(FakeGemma):
        def ask(self, page, model, retry=False):
            raise KeyboardInterrupt

    use_gemma(Interrupted())
    assert main([str(make_pdf([report_page]))]) == 130
    assert "Cancelled" in capsys.readouterr().err
    assert query("SELECT COUNT(*) FROM reports") == [(0,)]
