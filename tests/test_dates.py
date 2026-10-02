import pytest

from app.dates import parse_date


@pytest.mark.parametrize("text, expected", [
    ("14/09/2026", "2026-09-14"),
    ("12/09/2026 08:10", "2026-09-12"),
    ("02-Oct-2026 10:15 AM", "2026-10-02"),
    ("12/Sep/2026 08:10 AM", "2026-09-12"),
    ("12-09-2026 08:10:33", "2026-09-12"),
    ("03/04/2026", "2026-04-03"),          # day first: 3 April, not 4 March
    ("12/09/26", "2026-09-12"),
    ("Collected: 12/09/2026", "2026-09-12"),
    ("12.09.2026", "2026-09-12"),
    ("2026-09-12", "2026-09-12"),          # ISO is year first; day-first parsing would give 9 Dec
    ("2026-02-31", None),
    ("Sep 2026", None),                    # partial: no day
    ("08:10", None),                       # time only
    ("31/02/2026", None),
    ("garbage", None),
    ("02 Dec, 2X", None),                  # year blanked out: must not become 2002
    ("03:11 PM 02 Dec, 2X", None),
    ("12-Sep", None),
    ("", None),
    (None, None),
])
def test_parse_date(text, expected):
    assert parse_date(text) == expected
