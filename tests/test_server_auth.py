"""Signing in, staying signed in, and what a signed-out browser may not see."""

import re

import pytest
from factories import saved_report, saved_result

from arogya_vahi import accounts
from arogya_vahi.config import settings

PASSWORD = "liquorice-tractor-92"


@pytest.fixture
def anyone(client, conn):
    """An account exists; the client is not signed in."""
    accounts.sign_up(conn, "aai", PASSWORD)
    return client


@pytest.fixture
def signed_in(anyone):
    anyone.post("/api/sign-in", data={"username": "aai", "password": PASSWORD})
    return anyone


# ---------------------------------------------------------------- making the first account


def test_a_fresh_app_says_nobody_has_signed_up_yet(client):
    assert client.get("/api/me").json() == {"account": None, "anyone": False}


def test_the_first_person_signs_up_and_is_signed_in_at_once(client):
    response = client.post("/api/sign-up", json={"name": "aai", "password": PASSWORD})
    assert response.status_code == 201
    assert client.get("/api/me").json()["account"]["name"] == "aai"


def test_once_an_account_exists_a_stranger_cannot_make_another(anyone):
    response = anyone.post("/api/sign-up", json={"name": "someone", "password": PASSWORD})
    assert response.status_code == 403 and "sign in" in response.json()["detail"]


def test_a_signed_in_person_can_add_an_account_for_the_family(signed_in):
    assert signed_in.post("/api/sign-up", json={"name": "baba", "password": PASSWORD}).status_code == 201


def test_a_short_password_is_refused_with_a_sentence_she_can_act_on(client):
    response = client.post("/api/sign-up", json={"name": "aai", "password": "short"})
    assert response.status_code == 400 and "8 characters" in response.json()["detail"]


# ---------------------------------------------------------------- signing in and out


def test_signing_in_sets_a_cookie_the_phone_keeps(anyone):
    response = anyone.post("/api/sign-in", data={"username": "aai", "password": PASSWORD})
    assert response.status_code == 200
    cookie = response.cookies[settings.session_cookie]
    assert cookie and response.json()["token_type"] == "bearer"
    assert anyone.get("/api/me").json()["account"]["name"] == "aai"


def test_the_wrong_password_is_refused(anyone):
    response = anyone.post("/api/sign-in", data={"username": "aai", "password": "wrong-password"})
    assert response.status_code == 401 and "don't match" in response.json()["detail"]


def test_the_token_also_works_as_a_bearer_header_for_a_shortcut_or_a_script(anyone, conn):
    token = anyone.post("/api/sign-in", data={"username": "aai", "password": PASSWORD}).json()["access_token"]
    anyone.cookies.clear()
    response = anyone.get("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert response.json()["account"]["name"] == "aai"


def test_signing_out_forgets_the_browser_and_the_session(signed_in, conn):
    assert signed_in.post("/api/sign-out").status_code == 204
    assert signed_in.get("/api/me").json()["account"] is None
    assert conn.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0


def test_a_made_up_token_is_not_signed_in(anyone):
    response = anyone.get("/api/me", headers={"Authorization": "Bearer not-a-real-token"})
    assert response.json()["account"] is None


# ---------------------------------------------------------------- what a stranger may not see


REPORT_ROUTES = [
    ("get", "/api/summary"),
    ("get", "/api/timelines"),
    ("get", "/api/questions"),
    ("get", "/api/patients"),
    ("get", "/api/uploads"),
    ("get", "/api/reports/1/original"),
    ("get", "/api/reports/1/page/1"),
    ("delete", "/api/reports/1"),
]


@pytest.mark.parametrize("method, path", REPORT_ROUTES)
def test_a_signed_out_browser_is_refused_every_report_route(anyone, method, path):
    assert getattr(anyone, method)(path).status_code == 401


def test_a_signed_out_browser_cannot_send_a_report(anyone, report_pdf):
    response = anyone.post("/api/uploads", files=[("files", ("report.pdf", report_pdf))])
    assert response.status_code == 401


def test_with_no_account_yet_the_app_is_open_so_the_first_screen_can_be_reached(client, conn):
    # nobody has signed up: refusing everything would lock her out of her own laptop
    saved_report(conn, "a", sample_date="2026-01-15")
    assert client.get("/api/summary").status_code == 200


def test_a_signed_in_person_sees_the_reports(signed_in, conn):
    saved_report(conn, "a", sample_date="2026-01-15")
    assert signed_in.get("/api/summary").status_code == 200


# ---------------------------------------------------------------- the page picture, for iOS


def test_the_page_picture_marks_where_the_value_is_printed(signed_in, conn, make_pdf, report_page):
    settings.originals_dir.mkdir(parents=True)
    (settings.originals_dir / "a.pdf").write_bytes(make_pdf([report_page]).read_bytes())
    saved_report(conn, "a", [saved_result("HBA1C", 7.2, bbox=(50, 300, 120, 312))])
    plain = signed_in.get("/api/reports/1/page/1")
    marked = signed_in.get("/api/reports/1/page/1?result=1")
    assert plain.headers["content-type"] == "image/png"
    assert marked.content != plain.content  # the value is ringed


def test_a_page_or_report_that_does_not_exist_is_a_plain_not_found(signed_in, conn, make_pdf, report_page):
    settings.originals_dir.mkdir(parents=True)
    (settings.originals_dir / "a.pdf").write_bytes(make_pdf([report_page]).read_bytes())
    saved_report(conn, "a")
    assert signed_in.get("/api/reports/1/page/9").status_code == 404
    assert signed_in.get("/api/reports/9/page/1").status_code == 404


def test_a_result_from_another_report_does_not_move_the_mark(signed_in, conn, make_pdf, report_page):
    settings.originals_dir.mkdir(parents=True)
    for name in ("a.pdf", "b.pdf"):
        (settings.originals_dir / name).write_bytes(make_pdf([report_page]).read_bytes())
    saved_report(conn, "a", [saved_result("HBA1C", 7.2, bbox=(50, 300, 120, 312))])
    saved_report(conn, "b", [saved_result("HB", 12.0, bbox=(50, 500, 120, 512))])
    with_other = signed_in.get("/api/reports/1/page/1?result=2")  # result 2 is report 2's
    assert with_other.content == signed_in.get("/api/reports/1/page/1").content


def test_the_picture_is_cached_by_her_browser_but_never_by_a_proxy(signed_in, conn, make_pdf, report_page):
    settings.originals_dir.mkdir(parents=True)
    (settings.originals_dir / "a.pdf").write_bytes(make_pdf([report_page]).read_bytes())
    saved_report(conn, "a")
    headers = signed_in.get("/api/reports/1/page/1").headers
    assert "private" in headers["cache-control"]  # a report page is never cached by a shared proxy


# ---------------------------------------------------------------- the website itself


def test_the_website_is_served_without_signing_in_so_the_sign_in_screen_can_load(anyone):
    page = anyone.get("/")
    assert page.status_code == 200 and "text/html" in page.headers["content-type"]


def test_the_share_target_needs_a_session_like_everything_else(anyone, report_pdf):
    response = anyone.post("/share-target", files=[("files", ("report.pdf", report_pdf))])
    assert response.status_code == 401


# ---------------------------------------------------------------- the website's own files


@pytest.mark.parametrize(
    "path, kind",
    [
        ("/", "text/html"),
        ("/styles.css", "text/css"),
        ("/app.js", "javascript"),
        ("/icon.svg", "image/svg"),
        ("/vendor/chart.umd.min.js", "javascript"),
    ],
)
def test_every_file_the_page_asks_for_is_served(client, path, kind):
    response = client.get(path)
    assert response.status_code == 200 and kind in response.headers["content-type"]


def test_the_website_loads_nothing_from_the_internet(client):
    # The promise is that reports never leave the laptop; a page that fetches a font or a
    # chart library from a CDN would tell that CDN when she opens her own health record.
    page = client.get("/").text
    assert "//" not in page.replace("http://www.w3.org", "").replace("<!--", "").replace("-->", "")


def test_the_app_is_one_module_per_job(client):
    for module in ("api.js", "text.js", "dom.js", "chart.js", "guide.js", "voice.js"):
        assert client.get(f"/{module}").status_code == 200


DEVANAGARI = re.compile(r"[\u0900-\u097f]")


def test_the_interface_is_english_and_only_what_is_spoken_is_marathi(client):
    # The split the user asked for: labels, buttons and screens in English, and Marathi
    # only where the app speaks to her about her own results. Those sentences are built
    # by the server (summary.py), so the page's own files should carry almost no Marathi.
    body = client.get("/").text.split("</svg>", 1)[1]  # past the icon definitions
    marathi = {line.strip() for line in body.splitlines() if DEVANAGARI.search(line)}
    # The app's own name, and the example question in the box that invites one. Both are
    # addressed to her; neither is a label of the interface.
    assert marathi == {
        "<span>आरोग्य वही</span>",  # the name on the sign-in screen
        "<small>आरोग्य वही</small>",  # and in the header, under the English one
        'placeholder="माझी साखर वाढली आहे का?" aria-label="Your question" />',
    }


def test_the_app_speaks_marathi_about_the_results(client):
    # The other half of the same rule: everything the app says about her results is
    # Marathi, and the page writes none of those sentences itself -- the server builds
    # them from templates. What is left here is the heading said before the doctor's
    # questions, and the example questions she can tap instead of typing.
    app_js = client.get("/app.js").text
    spoken = [line.strip() for line in app_js.splitlines() if DEVANAGARI.search(line)]
    assert any("डॉक्टरांना विचारा" in line for line in spoken)
    assert all("?" in line or "विचारा" in line for line in spoken), spoken


def test_hidden_really_hides(client):
    # `hidden` works by setting display:none, which any explicit `display` in a rule
    # beats. Both the sign-in screen and the app set display, so without this the app
    # showed the login form and the signed-in page at the same time.
    css = " ".join(client.get("/styles.css").text.split())  # line endings differ by platform
    assert "[hidden] { display: none !important; }" in css


# ---------------------------------------------------------------- the Marathi voice


def test_the_app_says_whether_this_computer_can_speak_marathi(signed_in):
    spoken = signed_in.get("/api/voice").json()
    assert spoken == {"installed": False, "voice": "mr_IN-google-medium"}


def test_asking_for_audio_without_the_voice_says_how_to_get_it(signed_in, conn):
    saved_report(conn, "a", [saved_result("HBA1C", 9.1, ref_high=5.6)], sample_date="2026-01-15")
    signed_in.get("/api/patients")  # match the report, so the summary has something to say
    response = signed_in.get("/api/summary/audio")
    assert response.status_code == 503 and "arogya-voice install" in response.json()["detail"]


def test_a_signed_out_browser_cannot_ask_for_the_audio(anyone):
    assert anyone.get("/api/summary/audio").status_code == 401
