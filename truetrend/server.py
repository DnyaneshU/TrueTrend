"""The web server: the website and the API, run on the laptop with the reports.

    truetrend-serve [--port 8000] [--model gemma4:e2b]        (or: python -m truetrend.server)

It listens on this computer only (127.0.0.1). A phone reaches it through Tailscale
(`tailscale serve 8000`), which adds HTTPS and lets in only the family's own devices.
Sent files are read one at a time by a background worker; the API answers meanwhile.
"""

import argparse
import logging
import sqlite3
import sys
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile, status
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from truetrend import accounts, ask, cli, db, ingest, jobs, patients, speech
from truetrend.change import every_timeline
from truetrend.config import settings
from truetrend.errors import UserError
from truetrend.highlight import page_png
from truetrend.models import Patient, PatientListing, Questions, Review, Summary, Timeline, Upload
from truetrend.summary import summarize
from truetrend.web import WEB_DIR, Connection, Reader, SessionToken, Viewer

logger = logging.getLogger(__name__)

PAGE_CACHE = "private, max-age=86400"  # her own browser may keep a page picture; no proxy may
ASK_DOCTOR = "डॉक्टरांना विचारा:"  # said before the questions, in the summary and aloud


class MergeRequest(BaseModel):
    keep: int  # the patient to keep
    other: int  # the same person: their reports and names join `keep`


class AssignRequest(BaseModel):
    patient_id: int | None  # None: a new patient


class ReviewRequest(BaseModel):
    decision: Review


class SignUpRequest(BaseModel):
    name: str = Field(min_length=1)
    password: str


class SignInReply(BaseModel):
    """What sign-in returns: the OAuth 2.0 bearer token, also set as the session cookie."""

    access_token: str
    token_type: str = "bearer"
    name: str


class Question(BaseModel):
    """A question she asked, in her own words."""

    question: str = Field(min_length=1, max_length=400)
    patient_id: int | None = None


class Spoken(BaseModel):
    """Whether this computer can read the summary aloud in Marathi itself."""

    installed: bool
    voice: str


class Me(BaseModel):
    """Who is signed in, and whether any account exists yet (the first screen needs both)."""

    account: dict | None  # {"id", "name"}; None when this browser is not signed in
    anyone: bool


Files = Annotated[list[UploadFile], File(description="PDFs or photos of reports")]
SignIn = Annotated[OAuth2PasswordRequestForm, Depends()]


def create_app(worker: jobs.Worker | None = None) -> FastAPI:
    """The website and its API; with a worker, sent files are read while the server runs."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if worker:
            worker.start()
        yield
        if worker:
            worker.stop(timeout=5)

    app = FastAPI(title="TrueTrend", lifespan=lifespan)

    @app.exception_handler(UserError)
    async def user_error(request: Request, error: UserError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(error)})

    def queue(conn: sqlite3.Connection, files: list[UploadFile]) -> list[Upload]:
        received = [ingest.receive(conn, file.filename or "report", file.file.read()) for file in files]
        if worker:
            worker.wake()
        return received

    # ------------------------------------------------------------ who may open the app

    @app.get("/api/me")
    def me(conn: Connection, viewer: Viewer) -> Me:
        """Who this browser is signed in as, and whether an account exists to sign in to."""
        account = {"id": viewer.id, "name": viewer.name} if viewer else None
        return Me(account=account, anyone=accounts.anyone(conn))

    @app.post("/api/sign-up", status_code=status.HTTP_201_CREATED)
    def sign_up(body: SignUpRequest, response: Response, conn: Connection, viewer: Viewer) -> SignInReply:
        """Make an account. The first one is open; after that, only a signed-in person adds more."""
        if viewer is None and accounts.anyone(conn):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Please sign in to add another account.")
        accounts.sign_up(conn, body.name, body.password)
        return _remember(response, accounts.sign_in(conn, body.name, body.password))

    @app.post("/api/sign-in")
    def sign_in(form: SignIn, response: Response, conn: Connection) -> SignInReply:
        """The OAuth 2.0 password grant, against this computer's own accounts."""
        try:
            session = accounts.sign_in(conn, form.username, form.password)
        except UserError as refused:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, str(refused), headers={"WWW-Authenticate": "Bearer"}
            ) from None
        return _remember(response, session)

    @app.post("/api/sign-out", status_code=status.HTTP_204_NO_CONTENT)
    def sign_out(response: Response, conn: Connection, token: SessionToken) -> None:
        """Sign this device out; the account's other devices stay signed in."""
        if token:
            accounts.sign_out(conn, token)
        response.delete_cookie(settings.session_cookie)

    # ------------------------------------------------------------ sending reports

    @app.post("/api/uploads", status_code=202)
    def upload(files: Files, conn: Connection, reader: Reader) -> list[Upload]:
        """Queue one or more PDFs or photos of reports to be read."""
        return queue(conn, files)

    @app.get("/api/uploads")
    def recent_uploads(conn: Connection, reader: Reader) -> list[Upload]:
        """The latest uploads and how reading each went, newest first."""
        return db.uploads(conn)

    @app.post("/share-target")
    def share_target(files: Files, conn: Connection, reader: Reader) -> RedirectResponse:
        """Where the phone sends a report shared from WhatsApp (the web app's share target)."""
        received = queue(conn, files)
        return RedirectResponse(f"/?shared={len(received)}", status_code=303)

    # ------------------------------------------------------------ what the reports say

    @app.get("/api/summary")
    def summary(conn: Connection, reader: Reader, patient: int | None = None) -> Summary:
        """The Marathi summary of a patient's latest report (the latest report's patient by default)."""
        return summarize(db.timeline_points(conn, _patient(conn, patient)))

    @app.get("/api/timelines")
    def timelines(conn: Connection, reader: Reader, patient: int | None = None) -> list[Timeline]:
        """Every test's results for a patient, oldest first, with each change judged."""
        patient_id = _patient(conn, patient)
        return every_timeline(db.timeline_points(conn, patient_id)) if patient_id is not None else []

    @app.get("/api/reports/{report_id}/original")
    def original(report_id: int, conn: Connection, reader: Reader) -> FileResponse:
        """The report's original PDF, to save or print."""
        return FileResponse(
            _original_path(conn, report_id),
            media_type="application/pdf",
            filename=f"report-{report_id}.pdf",
            content_disposition_type="inline",
        )

    @app.get("/api/reports/{report_id}/page/{page}", response_class=Response)
    def report_page(
        report_id: int, page: int, conn: Connection, reader: Reader, result: int | None = None
    ) -> Response:
        """One page of the report as a picture, with `result`'s value ringed on it.

        A picture, not the PDF, because Safari ignores a PDF link's #page=N: it would
        open page 1 of a 19-page report and leave her to find the value herself.
        """
        bbox = db.result_bbox(conn, result, report_id) if result else None
        try:
            picture = page_png(_original_path(conn, report_id), page, bbox)
        except UserError as missing:  # a page the report doesn't have is a not-found, not a bad request
            raise HTTPException(404, str(missing)) from None
        return Response(content=picture, media_type="image/png", headers={"Cache-Control": PAGE_CACHE})

    @app.post("/api/ask")
    def ask_question(body: Question, conn: Connection, reader: Reader) -> ask.Answer:
        """Answer a question about the saved reports, using only values found in them.

        Gemma reads the question and says which test and what kind of question it is;
        code writes every sentence from stored results. The model never sees a number
        and never writes one.
        """
        return ask.answer(conn, body.question, patient_id=body.patient_id)

    @app.get("/api/voice")
    def voice(reader: Reader) -> Spoken:
        """Whether this computer can read the summary aloud in Marathi itself."""
        return Spoken(installed=speech.installed(), voice=speech.VOICE)

    @app.get("/api/summary/audio", response_class=Response)
    def summary_audio(conn: Connection, reader: Reader, patient: int | None = None) -> Response:
        """The Marathi summary as speech, said by this computer.

        The same sentences the screen shows, in the same order, so what she hears and
        what she reads cannot drift apart. A browser can only speak what its system has,
        and Windows has no Marathi voice; this route is how a laptop says them at all.
        """
        said = summarize(db.timeline_points(conn, _patient(conn, patient)))
        lines = [*said.sentences, *([ASK_DOCTOR, *said.questions] if said.questions else [])]
        if not lines:
            raise HTTPException(404, "There is nothing to say yet.")
        if not speech.installed():
            raise HTTPException(
                503,
                "The Marathi voice is not installed on this computer. Run: truetrend-voice install",
            )
        return Response(
            content=speech.say(" ".join(lines)),
            media_type="audio/wav",
            headers={"Cache-Control": "no-store"},  # it changes when a report is added
        )

    # ------------------------------------------------------------ what a person decides

    @app.get("/api/questions")
    def questions(conn: Connection, reader: Reader, patient: int | None = None) -> Questions:
        """Everything waiting for a person: values to check, people to tell apart, duplicates."""
        patients.match_saved(conn)
        return Questions(
            results_to_check=db.results_to_check(conn, patient),
            same_person=patients.possibly_same(conn),
            unmatched_reports=list(db.report_people(conn, to_match=True)),
            duplicates=db.same_sample_reports(conn),
        )

    @app.post("/api/results/{result_id}/review", status_code=204)
    def review(result_id: int, body: ReviewRequest, conn: Connection, reader: Reader) -> None:
        """A person compared the value with the original: it is right ("verified") or wrong ("rejected")."""
        with db.write(conn):
            if not db.review_result(conn, result_id, body.decision):
                raise HTTPException(404, f"There is no result #{result_id}.")

    @app.get("/api/patients")
    def patient_list(conn: Connection, reader: Reader) -> PatientListing:
        """Every patient and their reports, and the reports matched to no one."""
        patients.match_saved(conn)
        return patients.listing(conn)

    @app.post("/api/patients/merge")
    def merge(body: MergeRequest, conn: Connection, reader: Reader) -> Patient:
        """Two patients are the same person."""
        return patients.merge(conn, body.keep, body.other)

    @app.put("/api/reports/{report_id}/patient")
    def assign(report_id: int, body: AssignRequest, conn: Connection, reader: Reader) -> Patient:
        """A report is someone else's: an existing patient's, or (patient_id null) a new one's."""
        return patients.assign(conn, report_id, body.patient_id)

    @app.delete("/api/reports/{report_id}", status_code=204)
    def delete(report_id: int, conn: Connection, reader: Reader) -> None:
        """Delete a report sent twice, and its results; its stored original is kept."""
        with db.write(conn):
            if not db.delete_report(conn, report_id):
                raise HTTPException(404, f"There is no report #{report_id}.")

    # The website itself, last so no page name can shadow an API route. It is served to
    # anyone who asks: the sign-in screen is part of it, and it holds no one's data.
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    return app


def _remember(response: Response, session) -> SignInReply:
    """Set the session cookie this browser keeps, and return the same token as a bearer token."""
    response.set_cookie(
        settings.session_cookie,
        session.token,
        max_age=accounts.SESSION_DAYS * 86400,
        httponly=True,  # JavaScript can't read it, so a bug on the page can't leak it
        samesite="lax",  # sent on her own navigation, not on another site's request
    )
    return SignInReply(access_token=session.token, name=session.name)


def _original_path(conn: sqlite3.Connection, report_id: int):
    """Where the report's stored original is, or 404 saying which part is missing."""
    if (file_path := db.report_file(conn, report_id)) is None:
        raise HTTPException(404, f"There is no report #{report_id}.")
    path = db.resolve_stored_path(file_path)
    if not path.is_file():
        raise HTTPException(404, f"The original of report #{report_id} is missing.")
    return path


def _patient(conn: sqlite3.Connection, patient: int | None) -> int | None:
    """The patient asked for (checked), or the latest report's."""
    return patients.get(conn, patient).id if patient is not None else db.latest_patient_id(conn)


def _command(args: argparse.Namespace) -> int:
    import uvicorn  # only the server command needs it

    worker = jobs.Worker(model=args.model)
    logger.info("TrueTrend at http://127.0.0.1:%d (reports in %s).", args.port, settings.storage_dir)
    uvicorn.run(create_app(worker), host="127.0.0.1", port=args.port, log_level="warning")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = cli.parser("server", "Run the web app and its API on this computer.")
    parser.add_argument("--port", type=int, default=8000, help="the port (default: 8000)")
    parser.add_argument("--model", default=settings.model, help=f"Ollama model (default: {settings.model})")
    return cli.run_command(parser, _command, argv)


if __name__ == "__main__":
    sys.exit(main())
