"""What the web server needs around its routes: the signed-in person, and the website's files.

Sign-in uses the OAuth 2.0 password grant against this app's own accounts, so the
browser's token works as a cookie (what her phone keeps) or as a bearer header (what a
script or an iOS Shortcut sends). Nothing here talks to anyone outside this computer.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer

from arogya_vahi import accounts, db
from arogya_vahi.config import settings
from arogya_vahi.models import Account

WEB_DIR = Path(__file__).resolve().parent / "web"  # the website: HTML, CSS, JS, served as files

# auto_error=False: a browser with no header is simply not signed in yet, which is not an error
# until a route needs a person. The tokenUrl is what /docs offers an "Authorize" button for.
_bearer = OAuth2PasswordBearer(tokenUrl="api/sign-in", auto_error=False)

_SIGN_IN = "Please sign in."
_UNAUTHORIZED = {"WWW-Authenticate": "Bearer"}


def token_of(request: Request, header: Annotated[str | None, Depends(_bearer)]) -> str | None:
    """The session token this request carries: the Authorization header, else the cookie."""
    return header or request.cookies.get(settings.session_cookie)


SessionToken = Annotated[str | None, Depends(token_of)]


def connection() -> Iterator[sqlite3.Connection]:
    """One database connection per request: FastAPI reuses it for every dependency below."""
    with closing(db.connect()) as conn:
        yield conn


Connection = Annotated[sqlite3.Connection, Depends(connection)]


def signed_in(conn: Connection, token: SessionToken) -> Account | None:
    """Who is signed in on this request, or None."""
    return accounts.whoami(conn, token) if token else None


Viewer = Annotated[Account | None, Depends(signed_in)]


def reader(viewer: Viewer, conn: Connection) -> Account | None:
    """The person whose reports these are. Required once an account exists.

    Before the first account is made, the app is open: refusing everything would lock
    her out of a laptop that is hers, with reports that are hers, and no way in.
    """
    if viewer is None and accounts.anyone(conn):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, _SIGN_IN, headers=_UNAUTHORIZED)
    return viewer


Reader = Annotated[Account | None, Depends(reader)]
