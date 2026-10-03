"""What every command shares: console set-up, and failures reported as one line with an exit code."""

import argparse
import logging
import sqlite3
import sys
from collections.abc import Callable

from arogya_vahi.console import configure_console
from arogya_vahi.errors import UserError

logger = logging.getLogger(__name__)

EXIT_ERROR = 1
EXIT_CANCELLED = 130  # the shell convention for Ctrl+C


def run_command(
    parser: argparse.ArgumentParser, command: Callable[[argparse.Namespace], int], argv: list[str] | None
) -> int:
    """Parse argv and run the command; expected failures become one 'error:' line, never a traceback."""
    configure_console()
    args = parser.parse_args(argv)
    try:
        return command(args)
    except UserError as error:
        logger.error("%s", error)
    except sqlite3.OperationalError as error:
        logger.error("The database could not be used (%s). Is another command still running?", error)
    except sqlite3.DatabaseError as error:
        logger.error("The database file is damaged or not a database (%s).", error)
    except OSError as error:
        logger.error("A file could not be read or written: %s", error)
    except KeyboardInterrupt:
        logger.error("Cancelled. Nothing was saved.")
        return EXIT_CANCELLED
    return EXIT_ERROR


def parser(command: str, description: str) -> argparse.ArgumentParser:
    """An argument parser whose usage names the command as it was run (arogya-extract, python -m ...)."""
    prog = f"python -m arogya_vahi.{command}" if sys.argv[0].endswith(".py") else None
    return argparse.ArgumentParser(prog=prog, description=description)
