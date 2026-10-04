"""The web server: the API the web app and the phone use, run on the laptop with the reports.

    arogya-serve [--port 8000] [--model gemma4:e2b]        (or: python -m arogya_vahi.server)

It listens on this computer only (127.0.0.1). A phone reaches it through Tailscale
(`tailscale serve 8000`), which adds HTTPS and lets in only the family's own devices.
Sent files are read one at a time by a background worker; the API answers meanwhile.
"""

import argparse
import logging
import sqlite3
import sys
from collections.abc import Iterator
from contextlib import asynccontextmanager, closing
from typing import Annotated

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from arogya_vahi import cli, db, ingest, jobs, patients
from arogya_vahi.change import every_timeline
from arogya_vahi.config import settings
from arogya_vahi.errors import UserError
from arogya_vahi.models import Patient, PatientListing, Questions, Review, Summary, Timeline, Upload
from arogya_vahi.summary import summarize

logger = logging.getLogger(__name__)


class MergeRequest(BaseModel):
    keep: int  # the patient to keep
    other: int  # the same person: their reports and names join `keep`


class AssignRequest(BaseModel):
    patient_id: int | None  # None: a new patient


class ReviewRequest(BaseModel):
    decision: Review


def connection() -> Iterator[sqlite3.Connection]:
    """One database connection per request."""
    with closing(db.connect()) as conn:
        yield conn


Connection = Annotated[sqlite3.Connection, Depends(connection)]
Files = Annotated[list[UploadFile], File(description="PDFs or photos of reports")]


def create_app(worker: jobs.Worker | None = None) -> FastAPI:
    """The API; with a worker, sent files are read in the background while the server runs."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if worker:
            worker.start()
        yield
        if worker:
            worker.stop(timeout=5)

    app = FastAPI(title="Arogya Vahi", lifespan=lifespan)

    @app.exception_handler(UserError)
    async def user_error(request: Request, error: UserError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(error)})

    def queue(conn: sqlite3.Connection, files: list[UploadFile]) -> list[Upload]:
        received = [ingest.receive(conn, file.filename or "report", file.file.read()) for file in files]
        if worker:
            worker.wake()
        return received

    # ------------------------------------------------------------ sending reports

    @app.post("/api/uploads", status_code=202)
    def upload(files: Files, conn: Connection) -> list[Upload]:
        """Queue one or more PDFs or photos of reports to be read."""
        return queue(conn, files)

    @app.get("/api/uploads")
    def recent_uploads(conn: Connection) -> list[Upload]:
        """The latest uploads and how reading each went, newest first."""
        return db.uploads(conn)

    @app.post("/share-target")
    def share_target(files: Files, conn: Connection) -> RedirectResponse:
        """Where the phone sends a report shared from WhatsApp (the web app's share target)."""
        received = queue(conn, files)
        return RedirectResponse(f"/?shared={len(received)}", status_code=303)

    # ------------------------------------------------------------ what the reports say

    @app.get("/api/summary")
    def summary(conn: Connection, patient: int | None = None) -> Summary:
        """The Marathi summary of a patient's latest report (the latest report's patient by default)."""
        return summarize(db.timeline_points(conn, _patient(conn, patient)))

    @app.get("/api/timelines")
    def timelines(conn: Connection, patient: int | None = None) -> list[Timeline]:
        """Every test's results for a patient, oldest first, with each change judged."""
        patient_id = _patient(conn, patient)
        return every_timeline(db.timeline_points(conn, patient_id)) if patient_id is not None else []

    @app.get("/api/reports/{report_id}/original")
    def original(report_id: int, conn: Connection) -> FileResponse:
        """The report's original PDF; open it at a page with #page=N."""
        if (file_path := db.report_file(conn, report_id)) is None:
            raise HTTPException(404, f"There is no report #{report_id}.")
        path = db.resolve_stored_path(file_path)
        if not path.is_file():
            raise HTTPException(404, f"The original of report #{report_id} is missing.")
        return FileResponse(
            path,
            media_type="application/pdf",
            filename=f"report-{report_id}.pdf",
            content_disposition_type="inline",
        )

    # ------------------------------------------------------------ what a person decides

    @app.get("/api/questions")
    def questions(conn: Connection, patient: int | None = None) -> Questions:
        """Everything waiting for a person: values to check, people to tell apart, duplicates."""
        patients.match_saved(conn)
        return Questions(
            results_to_check=db.results_to_check(conn, patient),
            same_person=patients.possibly_same(conn),
            unmatched_reports=[person for person in db.report_people(conn, to_match=True)],
            duplicates=db.same_sample_reports(conn),
        )

    @app.post("/api/results/{result_id}/review", status_code=204)
    def review(result_id: int, body: ReviewRequest, conn: Connection) -> None:
        """A person compared the value with the original: it is right ("verified") or wrong ("rejected")."""
        with db.write(conn):
            if not db.review_result(conn, result_id, body.decision):
                raise HTTPException(404, f"There is no result #{result_id}.")

    @app.get("/api/patients")
    def patient_list(conn: Connection) -> PatientListing:
        """Every patient and their reports, and the reports matched to no one."""
        patients.match_saved(conn)
        return patients.listing(conn)

    @app.post("/api/patients/merge")
    def merge(body: MergeRequest, conn: Connection) -> Patient:
        """Two patients are the same person."""
        return patients.merge(conn, body.keep, body.other)

    @app.put("/api/reports/{report_id}/patient")
    def assign(report_id: int, body: AssignRequest, conn: Connection) -> Patient:
        """A report is someone else's: an existing patient's, or (patient_id null) a new one's."""
        return patients.assign(conn, report_id, body.patient_id)

    @app.delete("/api/reports/{report_id}", status_code=204)
    def delete(report_id: int, conn: Connection) -> None:
        """Delete a report sent twice, and its results; its stored original is kept."""
        with db.write(conn):
            if not db.delete_report(conn, report_id):
                raise HTTPException(404, f"There is no report #{report_id}.")

    return app


def _patient(conn: sqlite3.Connection, patient: int | None) -> int | None:
    """The patient asked for (checked), or the latest report's."""
    return patients.get(conn, patient).id if patient is not None else db.latest_patient_id(conn)


def _command(args: argparse.Namespace) -> int:
    import uvicorn  # only the server command needs it

    worker = jobs.Worker(model=args.model)
    logger.info("Arogya Vahi at http://127.0.0.1:%d (reports in %s).", args.port, settings.storage_dir)
    uvicorn.run(create_app(worker), host="127.0.0.1", port=args.port, log_level="warning")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = cli.parser("server", "Run the web app and its API on this computer.")
    parser.add_argument("--port", type=int, default=8000, help="the port (default: 8000)")
    parser.add_argument("--model", default=settings.model, help=f"Ollama model (default: {settings.model})")
    return cli.run_command(parser, _command, argv)


if __name__ == "__main__":
    sys.exit(main())
