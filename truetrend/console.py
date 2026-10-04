"""Console set-up shared by the command-line tools."""

import logging
import sys
from contextlib import suppress

_HANDLER_NAME = "truetrend-console"
# Our progress lines are shown; other libraries (httpx logs every request) only when they warn.
# "__main__" is the name a module gets when run as `python -m truetrend.<module>`.
_INFO_LOGGERS = ("truetrend", "__main__")


class _ConsoleFormatter(logging.Formatter):
    """Progress lines as they are; warnings and errors prefixed 'warning:' / 'error:'."""

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        return message if record.levelno < logging.WARNING else f"{record.levelname.lower()}: {message}"


def configure_console() -> None:
    """UTF-8 output (Marathi names and µIU/mL survive a Windows code page) and logging to stderr."""
    for stream in (sys.stdout, sys.stderr):
        with suppress(AttributeError, ValueError):  # not a real console, e.g. under pytest
            stream.reconfigure(encoding="utf-8")

    handler = logging.StreamHandler(sys.stderr)
    handler.set_name(_HANDLER_NAME)
    handler.setFormatter(_ConsoleFormatter())
    root = logging.getLogger()
    root.handlers = [existing for existing in root.handlers if existing.get_name() != _HANDLER_NAME]
    root.addHandler(handler)
    root.setLevel(logging.WARNING)
    for name in _INFO_LOGGERS:
        logging.getLogger(name).setLevel(logging.INFO)
