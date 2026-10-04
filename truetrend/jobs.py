"""The worker that reads queued uploads with Gemma, one at a time.

Reading a report takes minutes and uses the whole GPU, so uploads wait in the uploads
table and one worker thread reads them in the order they came. The queue is in the
database: uploads left half-read by a stop or crash are read again on the next start.
"""

import hashlib
import logging
import threading
from contextlib import closing

from truetrend import db, extract, ingest
from truetrend.errors import UserError
from truetrend.models import Upload

logger = logging.getLogger(__name__)


def process_next(model: str | None = None) -> Upload | None:
    """Read the oldest queued upload and record how it went; None when nothing is waiting."""
    with closing(db.connect()) as conn:
        upload = db.claim_next_upload(conn)
    if upload is None:
        return None
    path = ingest.inbox_path(upload.sha256)
    status, message, report_id = "failed", None, None
    try:
        output = extract.run(path, model=model, name=upload.file_name)
    except UserError as error:
        message = str(error)
    except Exception as error:  # one bad file must not stop the worker
        logger.exception("Reading upload #%d failed", upload.id)
        message = f"Reading it failed unexpectedly ({type(error).__name__}); try sending it again."
    else:
        with closing(db.connect()) as conn:
            if output is None:
                status = "already_saved"
                report_id = db.find_report_id(conn, hashlib.sha256(path.read_bytes()).hexdigest())
            else:
                status, report_id = "saved", output.report_id
                notes = output.warnings + _duplicate_notes(conn, report_id)
                message = "; ".join(notes) or None
    with closing(db.connect()) as conn:
        db.finish_upload(conn, upload.id, status, message, report_id)
        finished = db.upload(conn, upload.id)
    if status != "failed":  # the original is stored with the report now; a failed one can be retried
        path.unlink(missing_ok=True)
    return finished


def _duplicate_notes(conn, report_id: int) -> list[str]:
    return [
        f"Looks like the same report as #{earlier} (same patient, lab and sample date)"
        for earlier, later in db.same_sample_reports(conn)
        if later == report_id
    ]


class Worker:
    """A background thread that reads queued uploads until stopped; wake() it when one arrives."""

    def __init__(self, model: str | None = None, idle_seconds: float = 5.0) -> None:
        self.model, self.idle_seconds = model, idle_seconds
        self._wake, self._stop = threading.Event(), threading.Event()
        self._thread = threading.Thread(target=self._run, name="truetrend-reader", daemon=True)

    def start(self) -> None:
        with closing(db.connect()) as conn:
            if requeued := db.requeue_interrupted_uploads(conn):
                logger.info("Reading again %d upload(s) left half-read by the last stop.", requeued)
        self._thread.start()

    def wake(self) -> None:
        self._wake.set()

    def stop(self, timeout: float | None = None) -> None:
        """Ask the worker to stop after the upload it is reading, and wait for it."""
        self._stop.set()
        self._wake.set()
        self._thread.join(timeout)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                read = process_next(self.model)
            except Exception:  # the database is busy or broken: wait and try again
                logger.exception("The reader could not take the next upload")
                read = None
            if read is None:
                self._wake.wait(self.idle_seconds)
                self._wake.clear()
