"""Who may open the app: accounts on this computer, and the sessions that keep a phone signed in.

Sign-in is the OAuth 2.0 password grant, served by this app itself: no account is made
with anyone else, and nothing is sent anywhere. A password is stored only as a PBKDF2
hash with its own salt, and a session token only as its SHA-256 — so neither the
database nor a backup of it reveals either one.
"""

import hashlib
import hmac
import secrets
import sqlite3

from arogya_vahi import db
from arogya_vahi.errors import UserError
from arogya_vahi.models import Account, Session

ALGORITHM = "pbkdf2_sha256"
ROUNDS = 600_000  # OWASP's 2023 floor for PBKDF2-HMAC-SHA256; a sign-in costs ~0.3 s
SALT_BYTES = 16
TOKEN_BYTES = 32  # 256 bits of randomness: a token is never guessed
SESSION_DAYS = 365  # she signs in once on her phone and stays signed in

# Said for a wrong name and a wrong password alike: which one was wrong is not worth telling.
_REFUSED = "That name and password don't match an account on this computer."
MIN_PASSWORD = 8


def password_hash(password: str, *, salt: bytes | None = None, rounds: int = ROUNDS) -> str:
    """The stored form, pbkdf2_sha256$rounds$salt$hash: the cost is stored with it, so an old
    account stays exactly as hard to guess as the day it was made."""
    salt = salt or secrets.token_bytes(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, rounds)
    return f"{ALGORITHM}${rounds}${salt.hex()}${digest.hex()}"


def password_matches(password: str, stored: str) -> bool:
    """Whether the password makes the stored hash again, compared in constant time."""
    try:
        algorithm, rounds, salt, _ = stored.split("$")
        if algorithm != ALGORITHM:
            return False
        again = password_hash(password, salt=bytes.fromhex(salt), rounds=int(rounds))
    except ValueError:
        return False
    return hmac.compare_digest(again, stored)


def token_hash(token: str) -> str:
    """What a session's token is stored as: a stolen database still has no usable token."""
    return hashlib.sha256(token.encode()).hexdigest()


def _name(name: str) -> str:
    """A name as typed, with the spaces around it dropped."""
    if not (tidy := name.strip()):
        raise UserError("Please type a name for the account.")
    return tidy


def sign_up(conn: sqlite3.Connection, name: str, password: str) -> Account:
    """Make the account. The first one is usually the only one: this is a home laptop."""
    tidy = _name(name)
    if len(password) < MIN_PASSWORD:
        raise UserError(f"Please choose a password of at least {MIN_PASSWORD} characters.")
    with db.write(conn):
        if db.account_named(conn, tidy):
            raise UserError(f"There is already an account called {tidy!r}.")
        return db.add_account(conn, tidy, password_hash(password))


def sign_in(conn: sqlite3.Connection, name: str, password: str) -> Session:
    """Check the password and start a session; its token is what the phone keeps."""
    account = db.account_named(conn, _name(name))
    if account is None or not password_matches(password, account.password_hash):
        raise UserError(_REFUSED)
    token = secrets.token_urlsafe(TOKEN_BYTES)
    with db.write(conn):
        db.add_session(conn, account.id, token_hash(token), SESSION_DAYS)
    return Session(token=token, name=account.name, account_id=account.id)


def whoami(conn: sqlite3.Connection, token: str) -> Account | None:
    """Whose session this token is, or None when it is unknown, signed out or expired."""
    return db.account_of_session(conn, token_hash(token))


def sign_out(conn: sqlite3.Connection, token: str) -> None:
    """End this one session; her other devices stay signed in."""
    with db.write(conn):
        db.delete_session(conn, token_hash(token))


def anyone(conn: sqlite3.Connection) -> bool:
    """Whether any account exists yet: if not, the app asks the first person to make one."""
    return db.account_count(conn) > 0
