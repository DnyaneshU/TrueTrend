"""Errors shown to the user."""


class ExtractError(Exception):
    """A problem the user can fix. The command prints it as one line and exits with 1."""
