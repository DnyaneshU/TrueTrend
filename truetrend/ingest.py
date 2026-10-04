"""Files people send (a PDF, or a photo of a report from WhatsApp), queued to be read one at a time.

Every file becomes a PDF in settings.inbox_dir before it is queued: a photo becomes a
one-page PDF, so the rest of the pipeline reads one kind of file. The same file sent
twice is read once.
"""

import hashlib
import logging
import sqlite3
from pathlib import Path

import pymupdf

from truetrend import db
from truetrend.config import settings
from truetrend.errors import UserError
from truetrend.files import write_whole
from truetrend.models import Upload

logger = logging.getLogger(__name__)


def receive(conn: sqlite3.Connection, file_name: str, data: bytes) -> Upload:
    """Queue a sent file to be read, unless the same bytes are already saved or waiting.

    Raises UserError when the file is empty, too big, or neither a PDF nor an image.
    """
    if not data:
        raise UserError(f"{file_name} is empty.")
    if len(data) > settings.max_upload_bytes:
        raise UserError(f"{file_name} is larger than {settings.max_upload_bytes // 2**20} MB.")
    sha256 = hashlib.sha256(data).hexdigest()
    pdf = as_pdf(file_name, data)
    with db.write(conn):
        if (same := db.pending_upload(conn, sha256) or db.saved_upload(conn, sha256)) is not None:
            return same
        write_whole(inbox_path(sha256), pdf)
        upload = db.add_upload(conn, file_name, sha256)
    logger.info("Queued upload #%d: %s.", upload.id, file_name)
    return upload


def inbox_path(sha256: str) -> Path:
    """Where a sent file waits, as a PDF, until it is read."""
    return settings.inbox_dir / f"{sha256}.pdf"


def as_pdf(file_name: str, data: bytes) -> bytes:
    """The file as PDF bytes: a PDF as it is, an image (JPEG, PNG, ...) as a one-page PDF."""
    try:
        with pymupdf.open(stream=data) as doc:
            if doc.is_pdf:
                return data
            return doc.convert_to_pdf()
    except (RuntimeError, ValueError):  # pymupdf.FileDataError, or a format it can't read
        raise UserError(f"{file_name} is not a PDF or an image of a report.") from None
