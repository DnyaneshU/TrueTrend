"""The web server's API, the upload queue and its reading worker (Gemma is faked)."""

import time
from contextlib import closing

import pymupdf
import pytest
from factories import GOOD_REPLY, FakeGemma, rows, saved_report, saved_result
from fastapi.testclient import TestClient

from arogya_vahi import db, ingest, jobs
from arogya_vahi.config import settings
from arogya_vahi.extract import run
from arogya_vahi.server import create_app


def send(client, *files):
    """POST files given as (name, bytes)."""
    return client.post("/api/uploads", files=[("files", (name, data)) for name, data in files])


def photo_of(pdf: bytes) -> bytes:
    with pymupdf.open(stream=pdf) as doc:
        return doc[0].get_pixmap(dpi=72).tobytes("png")


# ---------------------------------------------------------------- sending reports


def test_a_sent_pdf_is_queued(client, report_pdf):
    response = send(client, ("report.pdf", report_pdf))
    assert response.status_code == 202
    ((sent),) = response.json()
    assert (sent["file_name"], sent["status"]) == ("report.pdf", "queued")
    assert ingest.inbox_path(sent["sha256"]).read_bytes() == report_pdf
    assert [upload["id"] for upload in client.get("/api/uploads").json()] == [sent["id"]]


def test_the_same_file_sent_twice_is_queued_once(client, report_pdf):
    first = send(client, ("report.pdf", report_pdf)).json()[0]
    again = send(client, ("same.pdf", report_pdf)).json()[0]
    assert again["id"] == first["id"]


def test_a_photo_becomes_a_one_page_pdf(client, report_pdf):
    (sent,) = send(client, ("whatsapp.png", photo_of(report_pdf))).json()
    with pymupdf.open(ingest.inbox_path(sent["sha256"])) as doc:
        assert doc.is_pdf and doc.page_count == 1


@pytest.mark.parametrize(
    "name, data, message",
    [("notes.txt", b"hello", "is not a PDF or an image"), ("empty.pdf", b"", "is empty")],
)
def test_a_file_that_is_not_a_report_is_refused(client, name, data, message):
    response = send(client, (name, data))
    assert response.status_code == 400 and message in response.json()["detail"]


def test_a_file_too_big_is_refused(client, report_pdf, monkeypatch):
    monkeypatch.setattr(settings, "max_upload_bytes", 100)
    assert "larger than" in send(client, ("report.pdf", report_pdf)).json()["detail"]


def test_the_share_target_queues_and_returns_to_the_app(client, report_pdf):
    response = client.post(
        "/share-target", files=[("files", ("report.pdf", report_pdf))], follow_redirects=False
    )
    assert (response.status_code, response.headers["location"]) == (303, "/?shared=1")


# ---------------------------------------------------------------- reading the queue


def test_the_worker_reads_a_queued_report(client, report_pdf, fake_gemma):
    (sent,) = send(client, ("report.pdf", report_pdf)).json()
    read = jobs.process_next()
    assert (read.status, read.report_id, read.message) == ("saved", 1, None)
    assert not ingest.inbox_path(sent["sha256"]).exists()  # stored with the report now
    assert jobs.process_next() is None  # nothing left


def test_a_report_saved_before_is_not_saved_again(client, make_pdf, report_page, fake_gemma):
    pdf = make_pdf([report_page])
    run(pdf)  # read with arogya-extract earlier
    send(client, ("report.pdf", pdf.read_bytes()))
    read = jobs.process_next()
    assert (read.status, read.report_id) == ("already_saved", 1)


def test_the_same_patient_lab_and_sample_date_is_flagged_as_a_duplicate(
    client, make_pdf, report_page, fake_gemma
):
    send(client, ("a.pdf", make_pdf([report_page], name="a.pdf").read_bytes()))
    jobs.process_next()
    reprint = [*report_page, (50, 800, "Reprinted copy")]  # other bytes, same report
    send(client, ("b.pdf", make_pdf([reprint], name="b.pdf").read_bytes()))
    read = jobs.process_next()
    assert read.status == "saved" and "Looks like the same report as #1" in read.message
    assert client.get("/api/questions").json()["duplicates"] == [[1, 2]]


def test_a_report_that_cant_be_read_is_failed_with_why_and_kept(client, report_pdf, use_gemma):
    class Unreachable(FakeGemma):
        def ask(self, page, model, retry=False):
            from arogya_vahi.errors import UserError

            raise UserError("Can't reach Ollama.")

    use_gemma(Unreachable())
    (sent,) = send(client, ("report.pdf", report_pdf)).json()
    read = jobs.process_next()
    assert (read.status, read.message) == ("failed", "Can't reach Ollama.")
    assert ingest.inbox_path(sent["sha256"]).exists()  # kept, to be sent again


def test_an_unexpected_error_fails_the_upload_not_the_worker(client, report_pdf, use_gemma, caplog):
    class Broken(FakeGemma):
        def ask(self, page, model, retry=False):
            raise ZeroDivisionError

    use_gemma(Broken())
    send(client, ("report.pdf", report_pdf))
    read = jobs.process_next()
    assert read.status == "failed" and "ZeroDivisionError" in read.message


def test_uploads_left_half_read_are_read_again(client, report_pdf, fake_gemma):
    send(client, ("report.pdf", report_pdf))
    with closing(db.connect()) as conn:
        db.claim_next_upload(conn)  # then the laptop was switched off
        assert db.requeue_interrupted_uploads(conn) == 1
    assert jobs.process_next().status == "saved"


def test_the_background_worker_reads_what_arrives(report_pdf, fake_gemma):
    worker = jobs.Worker(idle_seconds=0.05)
    with TestClient(create_app(worker)) as client:
        send(client, ("report.pdf", report_pdf))
        deadline = time.monotonic() + 20
        while client.get("/api/uploads").json()[0]["status"] != "saved":
            assert time.monotonic() < deadline, "the worker did not read the upload"
            time.sleep(0.05)
    assert not worker._thread.is_alive()  # stopped with the server


# ---------------------------------------------------------------- what the reports say


def saved(conn, sha256, sample_date, value, patient="Sunita Patil"):
    return saved_report(
        conn,
        sha256,
        [saved_result("HBA1C", value, ref_text="4.0 - 5.6", ref_low=4.0, ref_high=5.6, ref_verified=True)],
        sample_date=sample_date,
        patient_name_raw=patient,
    )


def test_summary_and_timelines_are_for_the_latest_reports_patient(client, conn):
    saved(conn, "a", "2026-01-15", 7.0)
    saved(conn, "b", "2026-04-15", 7.6)
    client.get("/api/patients")  # matches the saved reports
    summary = client.get("/api/summary").json()
    assert summary["latest_sample_date"] == "2026-04-15" and summary["findings"][0]["kind"] == "real_increase"
    (hba1c,) = client.get("/api/timelines").json()
    assert (hba1c["code"], [p["value"] for p in hba1c["points"]]) == ("HBA1C", [7.0, 7.6])
    assert [change["kind"] for change in hba1c["changes"]] == ["real_increase"]
    assert client.get("/api/summary?patient=9").json()["detail"].startswith("There is no patient #9")


def test_the_original_opens_as_a_pdf(client, conn, make_pdf, report_page):
    settings.originals_dir.mkdir(parents=True)
    (settings.originals_dir / "a.pdf").write_bytes(make_pdf([report_page]).read_bytes())
    saved(conn, "a", "2026-01-15", 7.0)
    response = client.get("/api/reports/1/original")
    assert response.status_code == 200 and response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"].startswith("inline")
    assert client.get("/api/reports/9/original").status_code == 404


# ---------------------------------------------------------------- what a person decides


def test_a_value_to_check_is_confirmed_by_a_person_and_stays_confirmed(client, conn):
    saved_report(conn, "a", [saved_result("HBA1C", 7.0, status="needs_check", notes=["page 1 is scanned"])])
    (to_check,) = client.get("/api/questions").json()["results_to_check"]
    assert (to_check["value_text"], to_check["notes"]) == ("7", ["page 1 is scanned"])
    assert (
        client.post(f"/api/results/{to_check['result_id']}/review", json={"decision": "verified"}).status_code
        == 204
    )
    assert client.get("/api/questions").json()["results_to_check"] == []
    with conn:
        conn.execute("UPDATE results SET status = 'needs_check'")  # as arogya-recheck leaves a scan
    assert [p.status for p in db.timeline_points(conn)] == []  # no sample date: off the timeline
    assert rows(conn, "SELECT COALESCE(reviewed, status) FROM results") == [("verified",)]


def test_a_value_rejected_by_a_person_leaves_the_timeline(client, conn):
    saved(conn, "a", "2026-01-15", 7.0)
    assert client.post("/api/results/1/review", json={"decision": "rejected"}).status_code == 204
    assert db.timeline_points(conn) == []
    assert client.post("/api/results/9/review", json={"decision": "verified"}).status_code == 404


def test_people_with_the_same_name_are_asked_about_and_merged(client, conn):
    saved_report(conn, "a", patient_name_raw="Anil Patil", patient_sex_raw="M", sample_date="2026-01-15")
    saved_report(conn, "b", patient_name_raw="Anil Patil", patient_sex_raw="F", sample_date="2026-04-15")
    ((first, second),) = client.get("/api/questions").json()["same_person"]
    assert (first["display_name"], second["display_name"]) == ("Anil Patil", "Anil Patil")
    kept = client.post("/api/patients/merge", json={"keep": first["id"], "other": second["id"]}).json()
    assert kept["id"] == first["id"] and client.get("/api/questions").json()["same_person"] == []


def test_a_report_is_moved_to_a_new_patient_and_a_duplicate_deleted(client, conn):
    saved_report(conn, "a", sample_date="2026-01-15")
    saved_report(conn, "b", sample_date="2026-01-15")
    client.get("/api/patients")
    new = client.put("/api/reports/2/patient", json={"patient_id": None}).json()
    assert new["id"] == 2
    assert client.delete("/api/reports/2").status_code == 204
    assert rows(conn, "SELECT id FROM reports") == [(1,)]
    assert client.delete("/api/reports/2").status_code == 404
    assert client.put("/api/reports/1/patient", json={"patient_id": 9}).status_code == 400


def test_the_good_reply_names_the_patient_on_the_page():
    # the fake Gemma's reply matches the synthetic report page used above
    assert GOOD_REPLY["patient_name"] == "Mrs. Sunita Patil"
