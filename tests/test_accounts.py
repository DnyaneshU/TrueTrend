"""Accounts: sign up, sign in, and the tokens that keep a phone signed in."""

import pytest

from truetrend import accounts, db
from truetrend.errors import UserError


def test_a_password_is_never_stored_as_typed(conn):
    accounts.sign_up(conn, "aai", "liquorice-tractor-92")
    (stored,) = conn.execute("SELECT password_hash FROM accounts").fetchone()
    assert "liquorice-tractor-92" not in stored


def test_signing_in_with_the_right_password_gives_a_token(conn):
    accounts.sign_up(conn, "aai", "liquorice-tractor-92")
    session = accounts.sign_in(conn, "aai", "liquorice-tractor-92")
    assert accounts.whoami(conn, session.token).name == "aai"


def test_the_wrong_password_is_refused_and_says_nothing_about_which_part_was_wrong(conn):
    accounts.sign_up(conn, "aai", "liquorice-tractor-92")
    with pytest.raises(UserError) as wrong_password:
        accounts.sign_in(conn, "aai", "not-the-password")
    with pytest.raises(UserError) as no_such_person:
        accounts.sign_in(conn, "nobody", "liquorice-tractor-92")
    assert str(wrong_password.value) == str(no_such_person.value)


def test_the_same_name_cannot_be_taken_twice(conn):
    accounts.sign_up(conn, "aai", "liquorice-tractor-92")
    with pytest.raises(UserError, match="already"):
        accounts.sign_up(conn, "AAI", "another-password-entirely")


def test_a_short_password_is_refused(conn):
    with pytest.raises(UserError, match="characters"):
        accounts.sign_up(conn, "aai", "short")


def test_a_name_that_is_blank_is_refused(conn):
    with pytest.raises(UserError, match="name"):
        accounts.sign_up(conn, "   ", "liquorice-tractor-92")


def test_an_unknown_token_is_nobody(conn):
    assert accounts.whoami(conn, "not-a-token") is None


def test_signing_out_ends_only_that_session(conn):
    accounts.sign_up(conn, "aai", "liquorice-tractor-92")
    phone = accounts.sign_in(conn, "aai", "liquorice-tractor-92")
    laptop = accounts.sign_in(conn, "aai", "liquorice-tractor-92")
    accounts.sign_out(conn, phone.token)
    assert accounts.whoami(conn, phone.token) is None
    assert accounts.whoami(conn, laptop.token) is not None


def test_an_expired_session_is_nobody(conn):
    accounts.sign_up(conn, "aai", "liquorice-tractor-92")
    session = accounts.sign_in(conn, "aai", "liquorice-tractor-92")
    with db.write(conn):
        conn.execute(
            "UPDATE sessions SET expires_at = datetime('now', '-1 day') WHERE token_hash = ?",
            (accounts.token_hash(session.token),),
        )
    assert accounts.whoami(conn, session.token) is None


def test_two_sessions_never_share_a_token(conn):
    accounts.sign_up(conn, "aai", "liquorice-tractor-92")
    tokens = {accounts.sign_in(conn, "aai", "liquorice-tractor-92").token for _ in range(5)}
    assert len(tokens) == 5


def test_a_token_is_not_stored_as_sent(conn):
    accounts.sign_up(conn, "aai", "liquorice-tractor-92")
    session = accounts.sign_in(conn, "aai", "liquorice-tractor-92")
    stored = [row["token_hash"] for row in conn.execute("SELECT token_hash FROM sessions")]
    assert session.token not in stored


def test_whether_anyone_has_signed_up_yet(conn):
    assert not accounts.anyone(conn)
    accounts.sign_up(conn, "aai", "liquorice-tractor-92")
    assert accounts.anyone(conn)


def test_a_name_is_matched_whatever_the_case_and_spacing(conn):
    accounts.sign_up(conn, "Aai", "liquorice-tractor-92")
    assert accounts.sign_in(conn, "  aai ", "liquorice-tractor-92").name == "Aai"


def test_the_password_check_is_slow_enough_to_be_worth_doing(conn):
    # a stored hash carries its own cost, so an old account stays as hard to guess as a new one
    accounts.sign_up(conn, "aai", "liquorice-tractor-92")
    (stored,) = conn.execute("SELECT password_hash FROM accounts").fetchone()
    algorithm, rounds, _salt, _hash = stored.split("$")
    assert algorithm == "pbkdf2_sha256" and int(rounds) >= 600_000
