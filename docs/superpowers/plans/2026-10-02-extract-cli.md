# Extract CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `python -m app.extract path/to/report.pdf` extracts the 15 MVP lab tests from a report PDF with local Gemma (`gemma4:e4b` via Ollama, JSON schema output), prints them as JSON and saves them to SQLite.

**Architecture:** One Gemma call per page. Digital pages are sent as text rebuilt line by line from PyMuPDF word positions (`HbA1c | 6.8 | % | 4.0 - 5.6`); pages with almost no text are rendered to PNG and sent to Gemma vision. Gemma copies values exactly as printed into a Pydantic-defined schema; code assigns page numbers, merges header fields across pages, parses dates day-first and saves everything in one SQLite transaction with every row marked `needs_check`.

**Tech Stack:** Python 3.13 (3.11+ supported), PyMuPDF, ollama-python, Pydantic v2, python-dateutil, sqlite3 (stdlib), pytest.

**Spec:** `docs/superpowers/specs/2026-10-02-extract-cli-design.md`

## Global Constraints

- Python 3.11+; exact pins in `requirements.txt`: `pymupdf==1.28.2`, `ollama==0.6.3`, `pydantic==2.13.5`, `python-dateutil==2.9.0.post0`, `httpx==0.28.1`, `pytest==9.1.1`.
- Default model `gemma4:e4b`; `--model gemma4:e2b` is the faster fallback.
- Ollama call: `think=False`, options `{"temperature": 0, "num_ctx": 8192, "num_predict": 2048}`; the single retry uses `temperature` 0.3 with the other options unchanged.
- Gemma copies text exactly as printed. extract.py does no numeric parsing: `value`, `value_std`, `unit_std`, `ref_low`, `ref_high`, `bbox_json` stay NULL.
- Every saved result row: `status = 'needs_check'`, `check_notes = 'not verified yet'`. Reports from the CLI: `source = 'upload'`.
- stdout carries only the final JSON (UTF-8, `ensure_ascii=False`); progress, warnings and errors go to stderr.
- Tests use synthetic PDFs built in `tmp_path` and must never touch the real `storage/` folder. Never commit real reports or databases.
- This machine's paths contain spaces and an apostrophe (`C:\Users\Dnyanesh's Asus\...`): always use `pathlib.Path`, never build shell strings from paths.
- Run Python through the venv: `.venv/Scripts/python` (works from Bash and PowerShell in the repo root).
- Every commit message ends with the line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Paths with spaces and apostrophes** (this laptop's home folder is `Dnyanesh's Asus`): extraction works and the original is stored. Test: `test_path_with_apostrophe_and_spaces` (Task 6).
2. **Non-ASCII text on a Windows console or redirect** (Devanagari patient names, `µIU/mL`): printed literally as UTF-8, no `UnicodeEncodeError`. Test: `test_main_keeps_non_ascii_text_readable` (Task 6).
3. **The same MVP test on more than one page** (summary pages repeat values): every occurrence kept with its own page number, plus a warning. Test: `test_same_test_on_two_pages_is_kept_twice_with_warning` (Task 5).
4. **Ollama failing on a later page**: exit 1, nothing saved, no half report. Test: `test_ollama_failing_mid_report_saves_nothing` (Task 6).
5. **Ctrl+C during a multi-minute run**: "Cancelled. Nothing was saved.", exit 130, no report row. Test: `test_main_ctrl_c_saves_nothing` (Task 6).

---

### Task 1: Project skeleton, dependencies and SQLite schema

**Files:**
- Create: `requirements.txt`, `.gitignore`, `pytest.ini`, `app/__init__.py`, `app/db.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: nothing.
- Produces (`app/db.py`):
  - `ROOT: Path` (repo root), `STORAGE_DIR`, `DB_PATH = STORAGE_DIR / "arogya.db"`, `ORIGINALS_DIR = STORAGE_DIR / "originals"`
  - `REPORT_COLUMNS: tuple[str, ...]`, `RESULT_COLUMNS: tuple[str, ...]`
  - `connect(path: Path | None = None) -> sqlite3.Connection` — `None` means `DB_PATH` looked up at call time (tests monkeypatch it); creates the folder and tables; `row_factory = sqlite3.Row`; foreign keys on.
  - `find_report_id(conn, sha256: str) -> int | None`
  - `save_report(conn, report: dict, results: list[dict], replace: bool = False) -> int`

- [ ] **Step 1: Create a branch**

```bash
git switch -c feat/extract-cli
```

- [ ] **Step 2: Write `requirements.txt`**

```
pymupdf==1.28.2
ollama==0.6.3
pydantic==2.13.5
python-dateutil==2.9.0.post0
httpx==0.28.1
pytest==9.1.1
```

- [ ] **Step 3: Write `.gitignore`**

```
# Real reports and patient data never go into the repo
storage/
*.db
*.db-journal
*.pdf
!samples/**/*.pdf
/out*.json

# Python
__pycache__/
*.pyc
.venv/
.pytest_cache/

# Secrets
.env
```

- [ ] **Step 4: Write `pytest.ini` and an empty `app/__init__.py`**

`pytest.ini`:
```ini
[pytest]
pythonpath = .
testpaths = tests
```

`app/__init__.py`: empty file.

- [ ] **Step 5: Create the venv and install**

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -q -r requirements.txt
.venv/Scripts/python -c "import pymupdf, ollama, pydantic, dateutil, httpx; print('deps ok')"
```
Expected: `deps ok`

- [ ] **Step 6: Write the failing tests `tests/test_db.py`**

```python
import sqlite3

import pytest

from app import db


def make_report(sha256="abc123", **overrides):
    report = dict.fromkeys(db.REPORT_COLUMNS)
    report.update(source="upload", file_path="storage/originals/abc123.pdf", sha256=sha256, is_scanned=0)
    report.update(overrides)
    return report


def make_result(**overrides):
    result = {
        "test_code": "HBA1C", "raw_name": "HbA1c", "raw_value_text": "7.2", "unit": "%",
        "ref_text": "4.0 - 5.6", "page": 1, "status": "needs_check", "check_notes": "not verified yet",
    }
    result.update(overrides)
    return result


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "test.db")
    yield connection
    connection.close()


def test_connect_creates_tables(conn):
    names = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"patients", "reports", "results"} <= names


def test_connect_creates_missing_folder(tmp_path):
    path = tmp_path / "new" / "folder" / "test.db"
    db.connect(path).close()
    assert path.exists()


def test_save_report_and_find_it(conn):
    report_id = db.save_report(conn, make_report(), [
        make_result(),
        make_result(test_code="HB", raw_name="Haemoglobin", raw_value_text="12.1"),
    ])
    assert db.find_report_id(conn, "abc123") == report_id
    rows = conn.execute(
        "SELECT test_code, status, check_notes, page FROM results WHERE report_id = ? ORDER BY id", (report_id,)
    ).fetchall()
    assert [tuple(r) for r in rows] == [
        ("HBA1C", "needs_check", "not verified yet", 1),
        ("HB", "needs_check", "not verified yet", 1),
    ]
    assert conn.execute("SELECT created_at FROM reports").fetchone()[0]


def test_find_report_id_unknown(conn):
    assert db.find_report_id(conn, "nope") is None


def test_duplicate_sha256_rejected(conn):
    db.save_report(conn, make_report(), [])
    with pytest.raises(sqlite3.IntegrityError):
        db.save_report(conn, make_report(), [])


def test_replace_deletes_old_report_and_its_results(conn):
    old_id = db.save_report(conn, make_report(), [make_result()])
    new_id = db.save_report(conn, make_report(), [make_result(raw_value_text="7.0")], replace=True)
    assert new_id != old_id  # AUTOINCREMENT: ids are never reused
    assert conn.execute("SELECT COUNT(*) FROM reports").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM results WHERE report_id = ?", (old_id,)).fetchone()[0] == 0
    assert conn.execute("SELECT raw_value_text FROM results").fetchone()[0] == "7.0"


def test_failed_replace_keeps_old_report(conn):
    old_id = db.save_report(conn, make_report(), [make_result()])
    with pytest.raises(sqlite3.IntegrityError):
        db.save_report(conn, make_report(), [make_result(status="bogus")], replace=True)
    assert db.find_report_id(conn, "abc123") == old_id
    assert conn.execute("SELECT COUNT(*) FROM results").fetchone()[0] == 1


def test_unknown_source_rejected(conn):
    with pytest.raises(sqlite3.IntegrityError):
        db.save_report(conn, make_report(source="fax"), [])
```

- [ ] **Step 7: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_db.py -v`
Expected: collection error, `ImportError: cannot import name 'db' from 'app'`

- [ ] **Step 8: Write `app/db.py`**

```python
"""SQLite schema and queries.

The database and the stored original PDFs live in storage/, which is gitignored:
real reports and patient data never go into the repo.
"""
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STORAGE_DIR = ROOT / "storage"
DB_PATH = STORAGE_DIR / "arogya.db"
ORIGINALS_DIR = STORAGE_DIR / "originals"

SCHEMA = """
CREATE TABLE IF NOT EXISTS patients (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    display_name  TEXT NOT NULL,
    aliases_json  TEXT NOT NULL DEFAULT '[]',
    sex           TEXT,
    birth_year    INTEGER
);

CREATE TABLE IF NOT EXISTS reports (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id        INTEGER REFERENCES patients(id),
    lab_name          TEXT,
    sample_date       TEXT,                 -- ISO YYYY-MM-DD, sample collection date
    report_date       TEXT,                 -- ISO YYYY-MM-DD
    source            TEXT NOT NULL
                      CHECK (source IN ('whatsapp', 'gmail', 'upload', 'gmail_import')),
    file_path         TEXT NOT NULL,
    sha256            TEXT NOT NULL UNIQUE,
    is_scanned        INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    patient_name_raw  TEXT,                 -- as printed; patient_id is set once matching exists
    patient_age_raw   TEXT,
    patient_sex_raw   TEXT,
    extract_model     TEXT,
    extract_seconds   REAL,
    raw_json          TEXT                  -- Gemma's reply for every page
);

CREATE TABLE IF NOT EXISTS results (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id       INTEGER NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
    test_code       TEXT,
    raw_name        TEXT NOT NULL,
    raw_value_text  TEXT NOT NULL,
    value           REAL,
    unit            TEXT,
    value_std       REAL,
    unit_std        TEXT,
    ref_low         REAL,
    ref_high        REAL,
    ref_text        TEXT,
    page            INTEGER NOT NULL,
    bbox_json       TEXT,
    status          TEXT NOT NULL DEFAULT 'needs_check'
                    CHECK (status IN ('verified', 'needs_check', 'rejected')),
    check_notes     TEXT
);

CREATE INDEX IF NOT EXISTS idx_results_report ON results(report_id);
CREATE INDEX IF NOT EXISTS idx_results_test ON results(test_code);
"""

REPORT_COLUMNS = (
    "lab_name", "sample_date", "report_date", "source", "file_path", "sha256",
    "is_scanned", "patient_name_raw", "patient_age_raw", "patient_sex_raw",
    "extract_model", "extract_seconds", "raw_json",
)
RESULT_COLUMNS = (
    "test_code", "raw_name", "raw_value_text", "unit", "ref_text", "page",
    "status", "check_notes",
)


def connect(path: Path | None = None) -> sqlite3.Connection:
    """Open the database, creating its folder and tables if needed."""
    path = Path(path or DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def find_report_id(conn: sqlite3.Connection, sha256: str) -> int | None:
    row = conn.execute("SELECT id FROM reports WHERE sha256 = ?", (sha256,)).fetchone()
    return row["id"] if row else None


def save_report(conn: sqlite3.Connection, report: dict, results: list[dict], replace: bool = False) -> int:
    """Insert a report and its results in one transaction and return the report id.

    With replace=True an existing report with the same sha256 is deleted inside the
    same transaction (its results cascade), so a failed save keeps the old data.
    `report` needs every key in REPORT_COLUMNS and each result every key in
    RESULT_COLUMNS; values may be None.
    """
    with conn:
        if replace:
            conn.execute("DELETE FROM reports WHERE sha256 = ?", (report["sha256"],))
        cursor = conn.execute(
            f"INSERT INTO reports ({', '.join(REPORT_COLUMNS)}) "
            f"VALUES ({', '.join('?' * len(REPORT_COLUMNS))})",
            [report[column] for column in REPORT_COLUMNS],
        )
        report_id = cursor.lastrowid
        conn.executemany(
            f"INSERT INTO results (report_id, {', '.join(RESULT_COLUMNS)}) "
            f"VALUES (?, {', '.join('?' * len(RESULT_COLUMNS))})",
            [[report_id, *(result[column] for column in RESULT_COLUMNS)] for result in results],
        )
    return report_id
```

- [ ] **Step 9: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_db.py -v`
Expected: `8 passed`

- [ ] **Step 10: Commit**

```bash
git add requirements.txt .gitignore pytest.ini app/__init__.py app/db.py tests/test_db.py
git commit -m "chore: project skeleton and SQLite schema" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Read PDF pages as rebuilt text or page images

**Files:**
- Create: `app/extract.py`, `tests/conftest.py`
- Test: `tests/test_extract_pdf.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces (`app/extract.py`):
  - `ExtractError(Exception)` — user-fixable problem; `main()` (Task 6) prints it as one line, exit 1.
  - `@dataclass PageInput(number: int, total: int, mode: Literal["text", "vision"], text: str = "", image: bytes | None = None)` — `number` is 1-based.
  - `open_pdf(path: Path) -> pymupdf.Document` — raises `ExtractError` for missing file, unreadable/corrupt file, non-PDF, password-protected, zero pages. Usable as a context manager.
  - `page_text(page: pymupdf.Page) -> str`
  - `read_pages(doc: pymupdf.Document) -> list[PageInput]`
- Produces (`tests/conftest.py`): fixtures `report_page` (list of `(x, y, text)`) and `make_pdf(pages, name="report.pdf", password=None) -> Path` (`name` may contain sub-folders; `[]` makes a blank page).

- [ ] **Step 1: Write `tests/conftest.py`**

```python
"""Shared test helpers. Tests only ever use synthetic PDFs, never real reports."""
import pymupdf
import pytest


@pytest.fixture
def report_page():
    """One synthetic lab report page as (x, y, text): 2 MVP tests and 1 look-alike."""
    return [
        (50, 60, "SUNRISE DIAGNOSTICS"),
        (50, 80, "Patient Name : Mrs. Sunita Patil"), (320, 80, "Age / Sex : 62 Y / F"),
        (50, 100, "Collected : 12/09/2026 08:10"), (320, 100, "Reported : 13/09/2026 14:02"),
        (50, 130, "Test Description"), (260, 130, "Result"), (320, 130, "Unit"),
        (380, 130, "Biological Ref. Interval"),
        (50, 150, "Glycosylated Haemoglobin (HbA1c)"), (260, 150, "7.2"), (320, 150, "%"),
        (380, 150, "4.0 - 5.6"),
        (50, 170, "Estimated Average Glucose"), (260, 170, "160"), (320, 170, "mg/dL"),
        (50, 190, "Haemoglobin"), (260, 190, "12.1"), (320, 190, "g/dL"), (380, 190, "12.0 - 15.0"),
    ]


@pytest.fixture
def make_pdf(tmp_path):
    """Build a synthetic PDF in tmp_path. Each page is a list of (x, y, text); [] is a blank page."""
    def _make(pages, name="report.pdf", password=None):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        doc = pymupdf.open()
        for items in pages:
            page = doc.new_page()
            for x, y, text in items:
                page.insert_text((x, y), text, fontsize=10)
        if password:
            doc.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_256,
                     user_pw=password, owner_pw=password + "-owner")
        else:
            doc.save(path)
        doc.close()
        return path
    return _make
```

- [ ] **Step 2: Write the failing tests `tests/test_extract_pdf.py`**

```python
import pymupdf
import pytest

from app.extract import ExtractError, open_pdf, page_text, read_pages


def test_page_text_rebuilds_table_rows(make_pdf, report_page):
    with pymupdf.open(make_pdf([report_page])) as doc:
        lines = page_text(doc[0]).splitlines()
    assert "Glycosylated Haemoglobin (HbA1c) | 7.2 | % | 4.0 - 5.6" in lines
    assert "Haemoglobin | 12.1 | g/dL | 12.0 - 15.0" in lines
    assert "Collected : 12/09/2026 08:10 | Reported : 13/09/2026 14:02" in lines


def test_page_text_joins_slightly_offset_words(make_pdf):
    with pymupdf.open(make_pdf([[(50, 100, "Fasting Blood Sugar"), (260, 100.8, "112")]])) as doc:
        assert page_text(doc[0]) == "Fasting Blood Sugar | 112"


def test_page_text_of_blank_page_is_empty(make_pdf):
    with pymupdf.open(make_pdf([[]])) as doc:
        assert page_text(doc[0]) == ""


def test_read_pages_sends_text_pages_as_text_and_blank_pages_as_images(make_pdf, report_page):
    with pymupdf.open(make_pdf([report_page, []])) as doc:
        first, second = read_pages(doc)
    assert (first.number, first.total, first.mode) == (1, 2, "text")
    assert "7.2" in first.text and first.image is None
    assert (second.number, second.total, second.mode) == (2, 2, "vision")
    assert second.image.startswith(b"\x89PNG")


def test_open_pdf_missing_file(tmp_path):
    with pytest.raises(ExtractError, match="File not found"):
        open_pdf(tmp_path / "nope.pdf")


def test_open_pdf_rejects_other_documents(tmp_path):
    notes = tmp_path / "notes.txt"
    notes.write_text("hello")
    with pytest.raises(ExtractError, match="Not a PDF"):
        open_pdf(notes)


def test_open_pdf_rejects_corrupt_file(tmp_path):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"\x00\x01 this is not a pdf")
    with pytest.raises(ExtractError, match="Not a readable PDF"):
        open_pdf(bad)


def test_open_pdf_rejects_password_protected(make_pdf, report_page):
    with pytest.raises(ExtractError, match="password-protected"):
        open_pdf(make_pdf([report_page], password="1234"))


def test_open_pdf_opens_a_good_pdf(make_pdf, report_page):
    with open_pdf(make_pdf([report_page])) as doc:
        assert doc.page_count == 1
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_extract_pdf.py -v`
Expected: collection error, `ModuleNotFoundError: No module named 'app.extract'`

- [ ] **Step 4: Write `app/extract.py`**

```python
"""Extract lab test results from a lab report PDF with local Gemma.

    python -m app.extract path/to/report.pdf [--force] [--model gemma4:e2b]

One Gemma call per page. Digital pages are sent as text rebuilt line by line;
pages with almost no text (scans) are sent as an image. Gemma copies values
exactly as printed; code assigns page numbers, parses dates and saves the rows.
Every saved row is `needs_check` until verify.py exists.
"""
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pymupdf

SCAN_TEXT_THRESHOLD = 50  # fewer non-whitespace characters than this: treat the page as scanned
RENDER_DPI = 150
COLUMN_GAP = 0.6          # a gap wider than this × text height separates table columns


class ExtractError(Exception):
    """A problem the user can fix. main() prints it as one line and exits with 1."""


@dataclass
class PageInput:
    number: int                      # 1-based, assigned by code
    total: int
    mode: Literal["text", "vision"]
    text: str = ""
    image: bytes | None = None       # PNG, vision pages only


def open_pdf(path: Path) -> pymupdf.Document:
    if not path.is_file():
        raise ExtractError(f"File not found: {path}")
    try:
        doc = pymupdf.open(path)
    except RuntimeError:  # pymupdf.FileDataError and friends
        raise ExtractError(f"Not a readable PDF: {path}") from None
    if not doc.is_pdf:
        doc.close()
        raise ExtractError(f"Not a PDF: {path}")
    if doc.needs_pass:
        doc.close()
        raise ExtractError(
            f"{path.name} is password-protected. Open it once, save a copy "
            "without a password, and run this on the copy."
        )
    if doc.page_count == 0:
        doc.close()
        raise ExtractError(f"{path.name} has no pages.")
    return doc


def page_text(page: pymupdf.Page) -> str:
    """The page's text rebuilt one printed line at a time.

    Words whose vertical centres are within half a text height form one line,
    read left to right. A gap wider than COLUMN_GAP × text height becomes " | ",
    so a table row reads "HbA1c | 6.8 | % | 4.0 - 5.6".
    """
    words = page.get_text("words")  # (x0, y0, x1, y1, word, block_no, line_no, word_no)
    if not words:
        return ""
    heights = sorted(w[3] - w[1] for w in words)
    height = heights[len(heights) // 2] or 1.0
    lines: list[tuple[float, list]] = []
    for word in sorted(words, key=lambda w: ((w[1] + w[3]) / 2, w[0])):
        centre = (word[1] + word[3]) / 2
        if lines and centre - lines[-1][0] <= height / 2:
            lines[-1][1].append(word)
        else:
            lines.append((centre, [word]))
    rendered = []
    for _, line in lines:
        line.sort(key=lambda w: w[0])
        parts = [line[0][4]]
        for previous, word in zip(line, line[1:]):
            parts.append(" | " if word[0] - previous[2] > COLUMN_GAP * height else " ")
            parts.append(word[4])
        rendered.append("".join(parts))
    return "\n".join(rendered)


def read_pages(doc: pymupdf.Document) -> list[PageInput]:
    pages = []
    for index, page in enumerate(doc):
        number, total = index + 1, doc.page_count
        text = page_text(page)
        if len("".join(text.split())) >= SCAN_TEXT_THRESHOLD:
            pages.append(PageInput(number, total, "text", text=text))
        else:
            png = page.get_pixmap(dpi=RENDER_DPI).tobytes("png")
            pages.append(PageInput(number, total, "vision", image=png))
    return pages
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_extract_pdf.py -v`
Expected: `9 passed`

- [ ] **Step 6: Commit**

```bash
git add app/extract.py tests/conftest.py tests/test_extract_pdf.py
git commit -m "feat(extract): read PDF pages as rebuilt text or page images" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Parse printed report dates day-first

**Files:**
- Modify: `app/extract.py` (imports + new function after `read_pages`)
- Test: `tests/test_extract_dates.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `parse_date(text: str | None) -> str | None` — ISO `YYYY-MM-DD`, or `None` for missing, partial or unreadable text.

- [ ] **Step 1: Write the failing tests `tests/test_extract_dates.py`**

```python
import pytest

from app.extract import parse_date


@pytest.mark.parametrize("text, expected", [
    ("14/09/2026", "2026-09-14"),
    ("12/09/2026 08:10", "2026-09-12"),
    ("02-Oct-2026 10:15 AM", "2026-10-02"),
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
    ("", None),
    (None, None),
])
def test_parse_date(text, expected):
    assert parse_date(text) == expected
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_extract_dates.py -v`
Expected: collection error, `ImportError: cannot import name 'parse_date'`

- [ ] **Step 3: Add the imports to the top of `app/extract.py`**

The import block becomes:
```python
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Literal

import pymupdf
from dateutil import parser as dateparser
```

- [ ] **Step 4: Append `parse_date` to `app/extract.py`**

```python
_ISO_DATE = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
_DEFAULT_A, _DEFAULT_B = datetime(2000, 1, 1), datetime(2001, 2, 2)


def parse_date(text: str | None) -> str | None:
    """A printed date as ISO YYYY-MM-DD, read day-first (Indian DD/MM/YYYY).

    Returns None for missing, partial ("Sep 2026") or unreadable text. Parsing
    twice with different defaults catches parts dateutil would silently fill in.
    """
    if not text or not text.strip():
        return None
    iso = _ISO_DATE.search(text)
    if iso:
        try:
            return date(*map(int, iso.groups())).isoformat()
        except ValueError:
            return None
    try:
        a = dateparser.parse(text, dayfirst=True, fuzzy=True, default=_DEFAULT_A)
        b = dateparser.parse(text, dayfirst=True, fuzzy=True, default=_DEFAULT_B)
    except (ValueError, OverflowError):
        return None
    return a.date().isoformat() if a.date() == b.date() else None
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_extract_dates.py -v`
Expected: `15 passed`

- [ ] **Step 6: Commit**

```bash
git add app/extract.py tests/test_extract_dates.py
git commit -m "feat(extract): parse printed report dates day-first" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: One Gemma call per page with a JSON schema

**Files:**
- Modify: `app/extract.py` (imports, constants, schema models, prompt, `ask_gemma`)
- Test: `tests/test_extract_gemma.py`

**Interfaces:**
- Consumes: `PageInput`, `ExtractError` (Task 2).
- Produces:
  - `DEFAULT_MODEL = "gemma4:e4b"`, `OLLAMA_OPTIONS`, `RETRY_TEMPERATURE = 0.3`
  - `TestCode` — `Literal` of the 15 codes `HBA1C, GLU_F, GLU_PP, TSH, FT4, CHOL, LDL, HDL, TG, CREAT, HB, VITD, B12, URIC, UREA`
  - `ExtractedResult(BaseModel)`: `test_code: TestCode, raw_name: str, value_text: str, unit: str | None, ref_text: str | None`
  - `PageExtraction(BaseModel)`: `patient_name, age, sex, lab_name, sample_date, report_date: str | None`, `results: list[ExtractedResult]`
  - `SYSTEM_PROMPT: str`
  - `ask_gemma(page: PageInput, model: str, retry: bool = False) -> PageExtraction` — raises `pydantic.ValidationError` when the reply does not fit the schema; raises `ExtractError` when Ollama is unreachable, the model is missing or the connection drops.

- [ ] **Step 1: Write the failing tests `tests/test_extract_gemma.py`**

```python
import typing
from types import SimpleNamespace

import httpx
import ollama
import pytest
from pydantic import ValidationError

from app import extract
from app.extract import ExtractError, PageExtraction, PageInput, ask_gemma

VALID_REPLY = (
    '{"patient_name":"Mrs. Sunita Patil","age":"62","sex":"F","lab_name":"SUNRISE DIAGNOSTICS",'
    '"sample_date":"12/09/2026 08:10","report_date":null,"results":[{"test_code":"HBA1C",'
    '"raw_name":"Glycosylated Haemoglobin (HbA1c)","value_text":"7.2","unit":"%","ref_text":"4.0 - 5.6"}]}'
)
TEXT_PAGE = PageInput(number=1, total=2, mode="text", text="Glycosylated Haemoglobin (HbA1c) | 7.2 | % | 4.0 - 5.6")
VISION_PAGE = PageInput(number=2, total=2, mode="vision", image=b"\x89PNG fake")


class FakeChat:
    """Stands in for ollama.chat: records the call and returns `reply` or raises `error`."""

    def __init__(self, reply=VALID_REPLY, error=None):
        self.reply, self.error, self.calls = reply, error, []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return SimpleNamespace(message=SimpleNamespace(content=self.reply))


@pytest.fixture
def fake_chat(monkeypatch):
    def install(**kwargs):
        fake = FakeChat(**kwargs)
        monkeypatch.setattr(extract.ollama, "chat", fake)
        return fake
    return install


def test_text_page_is_sent_as_text_with_fixed_settings(fake_chat):
    fake = fake_chat()
    result = ask_gemma(TEXT_PAGE, "gemma4:e4b")
    assert result.results[0].value_text == "7.2"
    call = fake.calls[0]
    assert call["model"] == "gemma4:e4b"
    assert call["think"] is False
    assert call["options"] == {"temperature": 0, "num_ctx": 8192, "num_predict": 2048}
    assert call["format"] == PageExtraction.model_json_schema()
    system, user = call["messages"]
    assert system == {"role": "system", "content": extract.SYSTEM_PROMPT}
    assert user["content"].startswith("Page 1 of 2.")
    assert "(HbA1c) | 7.2 | %" in user["content"]
    assert "images" not in user


def test_vision_page_is_sent_as_image(fake_chat):
    fake = fake_chat()
    ask_gemma(VISION_PAGE, "gemma4:e4b")
    user = fake.calls[0]["messages"][1]
    assert user["images"] == [b"\x89PNG fake"]
    assert user["content"].startswith("Page 2 of 2.")


def test_retry_uses_slightly_higher_temperature(fake_chat):
    fake = fake_chat()
    ask_gemma(TEXT_PAGE, "gemma4:e4b", retry=True)
    assert fake.calls[0]["options"] == {"temperature": 0.3, "num_ctx": 8192, "num_predict": 2048}


def test_truncated_reply_raises_validation_error(fake_chat):
    fake_chat(reply='{"patient_name": "Mrs. Sun')
    with pytest.raises(ValidationError):
        ask_gemma(TEXT_PAGE, "gemma4:e4b")


def test_unknown_test_code_raises_validation_error(fake_chat):
    fake_chat(reply=VALID_REPLY.replace('"HBA1C"', '"T3"'))
    with pytest.raises(ValidationError):
        ask_gemma(TEXT_PAGE, "gemma4:e4b")


@pytest.mark.parametrize("error, message", [
    (ConnectionError("refused"), "Can't reach Ollama"),
    (ollama.ResponseError("model 'gemma4:e4b' not found", 404), "Run: ollama pull gemma4:e4b"),
    (ollama.ResponseError("out of memory", 500), "Ollama error: out of memory"),
    (httpx.ReadError("connection reset"), "Lost the connection to Ollama"),
])
def test_ollama_problems_become_clear_errors(fake_chat, error, message):
    fake_chat(error=error)
    with pytest.raises(ExtractError, match=message):
        ask_gemma(TEXT_PAGE, "gemma4:e4b")


def test_schema_requires_every_field_and_limits_test_codes():
    schema = PageExtraction.model_json_schema()
    assert set(schema["required"]) == {
        "patient_name", "age", "sex", "lab_name", "sample_date", "report_date", "results",
    }
    codes = set(typing.get_args(extract.TestCode))
    assert len(codes) == 15
    assert set(schema["$defs"]["ExtractedResult"]["properties"]["test_code"]["enum"]) == codes


def test_prompt_describes_every_test_code():
    for code in typing.get_args(extract.TestCode):
        assert f"\n  {code} " in extract.SYSTEM_PROMPT
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_extract_gemma.py -v`
Expected: collection error, `ImportError: cannot import name 'PageExtraction'`

- [ ] **Step 3: Add the imports to the top of `app/extract.py`**

The import block becomes:
```python
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Literal

import httpx
import ollama
import pymupdf
from dateutil import parser as dateparser
from pydantic import BaseModel
```

- [ ] **Step 4: Add the model constants next to the existing constants**

Directly above `SCAN_TEXT_THRESHOLD = 50`:
```python
DEFAULT_MODEL = "gemma4:e4b"
OLLAMA_OPTIONS = {"temperature": 0, "num_ctx": 8192, "num_predict": 2048}
RETRY_TEMPERATURE = 0.3   # a second temperature-0 call would usually repeat the same broken reply
```

- [ ] **Step 5: Append the schema, prompt and `ask_gemma` to `app/extract.py`**

```python
TestCode = Literal[
    "HBA1C", "GLU_F", "GLU_PP", "TSH", "FT4", "CHOL", "LDL", "HDL",
    "TG", "CREAT", "HB", "VITD", "B12", "URIC", "UREA",
]


class ExtractedResult(BaseModel):
    test_code: TestCode
    raw_name: str
    value_text: str
    unit: str | None
    ref_text: str | None


class PageExtraction(BaseModel):
    patient_name: str | None
    age: str | None
    sex: str | None
    lab_name: str | None
    sample_date: str | None
    report_date: str | None
    results: list[ExtractedResult]


SYSTEM_PROMPT = """You read one page of an Indian medical laboratory report and copy data exactly as printed.
Never calculate, convert, round, translate or guess. If something is not printed on this page, use null.

Fields:
- patient_name, age, sex, lab_name: exactly as printed on this page, or null.
- sample_date: the sample COLLECTION date and time as printed (labels such as "Collected", "Sample Collected On", "Collection Date", "Drawn"). Not the registration date and not the report date.
- report_date: the date the report was released, as printed (labels such as "Reported", "Report Date", "Reported On").
- results: one entry for each result of ONLY these tests:
  HBA1C  = HbA1c, Glycated / Glycosylated Haemoglobin
  GLU_F  = Fasting blood or plasma glucose, FBS, Fasting Blood Sugar
  GLU_PP = Post-prandial glucose, PPBS, PP blood sugar, 2-hour glucose
  TSH    = TSH, Thyroid Stimulating Hormone (including ultrasensitive TSH)
  FT4    = Free T4, FT4, Free Thyroxine
  CHOL   = Total Cholesterol
  LDL    = LDL Cholesterol (direct or calculated)
  HDL    = HDL Cholesterol
  TG     = Triglycerides
  CREAT  = Serum Creatinine
  HB     = Haemoglobin / Hemoglobin / Hb
  VITD   = 25-Hydroxy (25-OH) Vitamin D, Vitamin D Total
  B12    = Vitamin B12, Cyanocobalamin
  URIC   = Uric Acid
  UREA   = Urea, Blood Urea, Serum Urea
  Do NOT include: Total T4 or T4, T3, LDL/HDL or other ratios, VLDL, non-HDL cholesterol, Estimated Average Glucose, Mean Blood Glucose, Random Blood Sugar, BUN / Blood Urea Nitrogen, any urine test, or any other test.
  Haemoglobin (HB) and HbA1c are different tests.
  For each result: raw_name = the test name exactly as printed; value_text = the result exactly as printed (keep "<", ">" and all decimals); unit = as printed, or null; ref_text = the reference range exactly as printed, or null.
  If none of these tests are on this page, results is [].
In the page text, each line is one printed line and " | " separates table columns.
Reply with compact JSON on a single line, no indentation."""


def ask_gemma(page: PageInput, model: str, retry: bool = False) -> PageExtraction:
    """One Gemma call for one page.

    Raises pydantic.ValidationError if the reply does not fit the schema, and
    ExtractError if Ollama is unreachable, the model is missing or the connection drops.
    """
    header = f"Page {page.number} of {page.total}."
    if page.mode == "vision":
        user = {"role": "user", "content": f"{header} The page is attached as an image.",
                "images": [page.image]}
    else:
        user = {"role": "user", "content": f"{header} Page text:\n\n{page.text}"}
    options = dict(OLLAMA_OPTIONS, temperature=RETRY_TEMPERATURE) if retry else dict(OLLAMA_OPTIONS)
    try:
        response = ollama.chat(
            model=model,
            messages=[{"role": "system", "content": SYSTEM_PROMPT}, user],
            format=PageExtraction.model_json_schema(),
            options=options,
            think=False,
        )
    except ConnectionError:
        raise ExtractError(
            "Can't reach Ollama. Start the Ollama app (or run: ollama serve) and try again."
        ) from None
    except ollama.ResponseError as error:
        if error.status_code == 404:
            raise ExtractError(f"Model {model} is not installed. Run: ollama pull {model}") from None
        raise ExtractError(f"Ollama error: {error.error}") from None
    except httpx.TransportError:
        raise ExtractError(
            "Lost the connection to Ollama while reading the report. Is it still running?"
        ) from None
    return PageExtraction.model_validate_json(response.message.content)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_extract_gemma.py -v`
Expected: `11 passed`

- [ ] **Step 7: Commit**

```bash
git add app/extract.py tests/test_extract_gemma.py
git commit -m "feat(extract): Gemma page call with JSON schema" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Per-page extraction with retry, header merge and warnings

**Files:**
- Modify: `app/extract.py` (imports, new section after `ask_gemma`)
- Test: `tests/test_extract_pipeline.py`

**Interfaces:**
- Consumes: `PageInput`, `ExtractError` (Task 2); `parse_date` (Task 3); `PageExtraction` (Task 4).
- Produces:
  - `Ask = Callable[[PageInput, bool], PageExtraction]` — `(page, retry)`
  - `@dataclass Extraction`: `header: dict[str, str | None]` (keys `patient_name, age, sex, lab_name, sample_date, report_date`; text as printed), `results: list[dict]` (keys `page, test_code, raw_name, value_text, unit, ref_text`), `pages: list[dict]` (keys `page, mode` (`text|vision|failed`), `results, seconds`), `warnings: list[str]`, `replies: list[dict]` (keys `page, mode, reply` where `reply` is `PageExtraction.model_dump()` or `None`)
  - `log(message: str) -> None` — one line to stderr
  - `extract_pages(pages: list[PageInput], ask: Ask) -> Extraction` — raises `ExtractError` if every page failed; lets `ExtractError` and `KeyboardInterrupt` from `ask` propagate.

- [ ] **Step 1: Write the failing tests `tests/test_extract_pipeline.py`**

```python
import pytest

from app.extract import ExtractError, PageExtraction, PageInput, extract_pages

INVALID = "invalid"


def reply(results=(), **header):
    data = dict.fromkeys(["patient_name", "age", "sex", "lab_name", "sample_date", "report_date"])
    data.update(header)
    data["results"] = list(results)
    return PageExtraction.model_validate(data)


def row(code="HBA1C", name="Glycosylated Haemoglobin (HbA1c)", value="7.2", unit="%", ref="4.0 - 5.6"):
    return {"test_code": code, "raw_name": name, "value_text": value, "unit": unit, "ref_text": ref}


class ScriptedAsk:
    """Fake Gemma. script[page_number] lists one outcome per attempt: a PageExtraction or INVALID."""

    def __init__(self, script):
        self.script = {page: list(outcomes) for page, outcomes in script.items()}
        self.calls = []

    def __call__(self, page, retry):
        self.calls.append((page.number, retry))
        outcome = self.script[page.number].pop(0)
        if outcome == INVALID:
            PageExtraction.model_validate_json("{broken")  # raises ValidationError, like a truncated reply
        return outcome


def pages(count):
    return [PageInput(number=n, total=count, mode="text", text=f"page {n} text") for n in range(1, count + 1)]


def test_page_numbers_come_from_code_and_header_is_merged():
    ask = ScriptedAsk({
        1: [reply([row()], patient_name="Mrs. Sunita Patil", lab_name="SUNRISE DIAGNOSTICS")],
        2: [reply([row("HB", "Haemoglobin", "12.1", "g/dL", "12.0 - 15.0")], sample_date="12/09/2026 08:10")],
    })
    out = extract_pages(pages(2), ask)
    assert [(r["page"], r["test_code"], r["value_text"]) for r in out.results] == [(1, "HBA1C", "7.2"), (2, "HB", "12.1")]
    assert out.header["patient_name"] == "Mrs. Sunita Patil"
    assert out.header["lab_name"] == "SUNRISE DIAGNOSTICS"
    assert out.header["sample_date"] == "12/09/2026 08:10"
    assert out.header["report_date"] is None
    assert [(p["page"], p["mode"], p["results"]) for p in out.pages] == [(1, "text", 1), (2, "text", 1)]
    assert out.warnings == []
    assert ask.calls == [(1, False), (2, False)]


def test_conflicting_header_keeps_first_and_warns():
    ask = ScriptedAsk({1: [reply(patient_name="Mrs. Sunita Patil")], 2: [reply(patient_name="Mr. Anil Patil")]})
    out = extract_pages(pages(2), ask)
    assert out.header["patient_name"] == "Mrs. Sunita Patil"
    assert len(out.warnings) == 1
    assert "page 2" in out.warnings[0] and "Mr. Anil Patil" in out.warnings[0]


def test_same_header_in_other_case_or_date_format_is_not_a_conflict():
    ask = ScriptedAsk({
        1: [reply(patient_name="Mrs. Sunita Patil", sample_date="12/09/2026 08:10")],
        2: [reply(patient_name="MRS.  SUNITA PATIL", sample_date="12-Sep-2026")],
    })
    assert extract_pages(pages(2), ask).warnings == []


def test_invalid_reply_is_retried_once():
    ask = ScriptedAsk({1: [INVALID, reply([row()])]})
    out = extract_pages(pages(1), ask)
    assert ask.calls == [(1, False), (1, True)]
    assert out.pages[0]["mode"] == "text" and len(out.results) == 1


def test_page_failing_twice_is_skipped_with_warning():
    ask = ScriptedAsk({1: [INVALID, INVALID], 2: [reply([row()])]})
    out = extract_pages(pages(2), ask)
    assert out.pages[0]["mode"] == "failed"
    assert [r["page"] for r in out.results] == [2]
    assert any("page 1" in w and "skipped" in w for w in out.warnings)
    assert out.replies[0] == {"page": 1, "mode": "text", "reply": None}


def test_every_page_failing_is_fatal():
    ask = ScriptedAsk({1: [INVALID, INVALID], 2: [INVALID, INVALID]})
    with pytest.raises(ExtractError, match="every page"):
        extract_pages(pages(2), ask)


def test_text_is_cleaned_and_rows_without_value_dropped():
    ask = ScriptedAsk({1: [reply([
        row(value="  7.2 ", unit="", ref="4.0  -  5.6"),
        row("HB", "Haemoglobin", "", "g/dL", None),
    ])]})
    out = extract_pages(pages(1), ask)
    assert out.results == [{
        "page": 1, "test_code": "HBA1C", "raw_name": "Glycosylated Haemoglobin (HbA1c)",
        "value_text": "7.2", "unit": None, "ref_text": "4.0 - 5.6",
    }]
    assert any("HB" in w and "no value" in w for w in out.warnings)


def test_same_test_on_two_pages_is_kept_twice_with_warning():
    ask = ScriptedAsk({1: [reply([row()])], 2: [reply([row()])]})
    out = extract_pages(pages(2), ask)
    assert [(r["page"], r["test_code"]) for r in out.results] == [(1, "HBA1C"), (2, "HBA1C")]
    assert any("HBA1C" in w and "pages 1, 2" in w for w in out.warnings)


def test_replies_are_kept_for_raw_json():
    out = extract_pages(pages(1), ScriptedAsk({1: [reply([row()], lab_name="SUNRISE DIAGNOSTICS")]}))
    assert out.replies[0]["page"] == 1 and out.replies[0]["mode"] == "text"
    assert out.replies[0]["reply"]["results"][0]["value_text"] == "7.2"


def test_progress_is_logged_to_stderr(capsys):
    extract_pages(pages(1), ScriptedAsk({1: [reply([row()])]}))
    assert "page 1/1 · text · 1 result ·" in capsys.readouterr().err
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_extract_pipeline.py -v`
Expected: collection error, `ImportError: cannot import name 'extract_pages'`

- [ ] **Step 3: Add the imports to the top of `app/extract.py`**

The import block becomes:
```python
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Literal

import httpx
import ollama
import pymupdf
from dateutil import parser as dateparser
from pydantic import BaseModel, ValidationError
```

- [ ] **Step 4: Append the pipeline section to `app/extract.py`**

```python
HEADER_FIELDS = ("patient_name", "age", "sex", "lab_name", "sample_date", "report_date")
DATE_FIELDS = ("sample_date", "report_date")

Ask = Callable[[PageInput, bool], PageExtraction]  # (page, retry) -> reply


@dataclass
class Extraction:
    header: dict[str, str | None]
    results: list[dict] = field(default_factory=list)
    pages: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    replies: list[dict] = field(default_factory=list)   # stored as reports.raw_json


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _clean(text: str | None) -> str | None:
    """Collapse whitespace; empty text becomes None."""
    if text is None:
        return None
    return " ".join(text.split()) or None


def _same(field_name: str, a: str, b: str) -> bool:
    if field_name in DATE_FIELDS and parse_date(a) is not None:
        return parse_date(a) == parse_date(b)
    return a.casefold() == b.casefold()


def _ask_with_retry(ask: Ask, page: PageInput) -> PageExtraction | None:
    for retry in (False, True):
        try:
            return ask(page, retry)
        except ValidationError:
            continue
    return None


def extract_pages(pages: list[PageInput], ask: Ask) -> Extraction:
    """Ask Gemma about each page and merge the answers into one report."""
    out = Extraction(header=dict.fromkeys(HEADER_FIELDS))
    header_page: dict[str, int] = {}
    for page in pages:
        started = time.perf_counter()
        reply = _ask_with_retry(ask, page)
        seconds = round(time.perf_counter() - started, 1)
        if reply is None:
            out.pages.append({"page": page.number, "mode": "failed", "results": 0, "seconds": seconds})
            out.replies.append({"page": page.number, "mode": page.mode, "reply": None})
            out.warnings.append(f"page {page.number}: Gemma's answer was unreadable twice; page skipped")
            log(f"page {page.number}/{page.total} · {page.mode} · FAILED · {seconds} s")
            continue
        out.replies.append({"page": page.number, "mode": page.mode, "reply": reply.model_dump()})

        for name in HEADER_FIELDS:
            value = _clean(getattr(reply, name))
            if value is None:
                continue
            if out.header[name] is None:
                out.header[name], header_page[name] = value, page.number
            elif not _same(name, out.header[name], value):
                first = header_page[name]
                out.warnings.append(
                    f"page {page.number}: {name} '{value}' differs from page {first} "
                    f"('{out.header[name]}'); kept page {first}"
                )

        kept = 0
        for row in reply.results:
            value_text = _clean(row.value_text)
            if value_text is None:
                out.warnings.append(f"page {page.number}: {row.test_code} had no value; row dropped")
                continue
            out.results.append({
                "page": page.number,
                "test_code": row.test_code,
                "raw_name": _clean(row.raw_name) or "",
                "value_text": value_text,
                "unit": _clean(row.unit),
                "ref_text": _clean(row.ref_text),
            })
            kept += 1
        out.pages.append({"page": page.number, "mode": page.mode, "results": kept, "seconds": seconds})
        log(f"page {page.number}/{page.total} · {page.mode} · {kept} result{'' if kept == 1 else 's'} · {seconds} s")

    if all(p["mode"] == "failed" for p in out.pages):
        raise ExtractError(
            "Gemma's answer was unreadable for every page, so nothing was saved. "
            "Try again, or try --model gemma4:e2b."
        )

    found_on: dict[str, list[int]] = {}
    for result in out.results:
        found_on.setdefault(result["test_code"], []).append(result["page"])
    for code, on_pages in found_on.items():
        if len(on_pages) > 1:
            out.warnings.append(
                f"{code} appears {len(on_pages)} times (pages {', '.join(map(str, on_pages))}); all kept"
            )
    return out
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_extract_pipeline.py -v`
Expected: `10 passed`

- [ ] **Step 6: Run everything so far**

Run: `.venv/Scripts/python -m pytest -q`
Expected: `53 passed`

- [ ] **Step 7: Commit**

```bash
git add app/extract.py tests/test_extract_pipeline.py
git commit -m "feat(extract): per-page extraction with retry and header merge" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Command-line tool: print JSON and save to SQLite

**Files:**
- Modify: `app/extract.py` (imports, `run`, `main`, `__main__` guard)
- Create: `README.md`, `tests/test_live_gemma.py`
- Test: `tests/test_extract_run.py`

**Interfaces:**
- Consumes: `db.connect`, `db.find_report_id`, `db.save_report`, `db.ROOT`, `db.DB_PATH`, `db.ORIGINALS_DIR` (Task 1); `open_pdf`, `read_pages` (Task 2); `parse_date` (Task 3); `ask_gemma`, `DEFAULT_MODEL` (Task 4); `extract_pages`, `log` (Task 5).
- Produces:
  - `run(pdf_path: Path, *, model: str = DEFAULT_MODEL, force: bool = False, db_path: Path | None = None, originals_dir: Path | None = None) -> dict | None` — the output JSON as a dict; `None` when the file was already extracted and `force` is False. Calls the module-level `ask_gemma` at call time (tests monkeypatch it).
  - `main(argv: list[str] | None = None) -> int` — exit codes: 0 ok or skipped, 1 `ExtractError`, 130 Ctrl+C.

- [ ] **Step 1: Write the failing tests `tests/test_extract_run.py`**

```python
import json
from contextlib import closing

import pytest

from app import db, extract
from app.extract import ExtractError, PageExtraction, main, run

GOOD_REPLY = {
    "patient_name": "Mrs. Sunita Patil", "age": "62 Y", "sex": "F", "lab_name": "SUNRISE DIAGNOSTICS",
    "sample_date": "12/09/2026 08:10", "report_date": "13/09/2026 14:02",
    "results": [
        {"test_code": "HBA1C", "raw_name": "Glycosylated Haemoglobin (HbA1c)", "value_text": "7.2",
         "unit": "%", "ref_text": "4.0 - 5.6"},
        {"test_code": "HB", "raw_name": "Haemoglobin", "value_text": "12.1",
         "unit": "g/dL", "ref_text": "12.0 - 15.0"},
    ],
}
EMPTY_REPLY = {**dict.fromkeys(["patient_name", "age", "sex", "lab_name", "sample_date", "report_date"]),
               "results": []}


class FakeGemma:
    """Stands in for ask_gemma: page 1 gets `first`, other pages get an empty reply."""

    def __init__(self, first=GOOD_REPLY):
        self.first, self.pages = first, []

    def __call__(self, page, model, retry=False):
        self.pages.append(page)
        return PageExtraction.model_validate(self.first if page.number == 1 else EMPTY_REPLY)


@pytest.fixture
def storage(tmp_path, monkeypatch):
    """Point the app's storage at a temp folder so tests never touch the real storage/."""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "storage" / "arogya.db")
    monkeypatch.setattr(db, "ORIGINALS_DIR", tmp_path / "storage" / "originals")
    return tmp_path / "storage"


@pytest.fixture
def gemma(monkeypatch):
    fake = FakeGemma()
    monkeypatch.setattr(extract, "ask_gemma", fake)
    return fake


def query(sql, *args):
    with closing(db.connect()) as conn:
        return [tuple(row) for row in conn.execute(sql, args)]


def test_run_saves_report_and_returns_output(storage, gemma, make_pdf, report_page):
    pdf = make_pdf([report_page])
    out = run(pdf)
    assert out["report_id"] == 1
    assert out["model"] == "gemma4:e4b"
    assert out["patient_name"] == "Mrs. Sunita Patil" and out["lab_name"] == "SUNRISE DIAGNOSTICS"
    assert (out["sample_date"], out["sample_date_text"]) == ("2026-09-12", "12/09/2026 08:10")
    assert (out["report_date"], out["report_date_text"]) == ("2026-09-13", "13/09/2026 14:02")
    assert out["pages"][0]["mode"] == "text"
    assert out["warnings"] == []
    assert [(r["test_code"], r["value_text"], r["status"]) for r in out["results"]] == [
        ("HBA1C", "7.2", "needs_check"), ("HB", "12.1", "needs_check"),
    ]

    (report,) = query(
        "SELECT sample_date, source, file_path, is_scanned, patient_name_raw, extract_model, raw_json FROM reports"
    )
    assert report[:2] == ("2026-09-12", "upload")
    assert report[2].endswith(f"{out['sha256']}.pdf")
    assert report[3:6] == (0, "Mrs. Sunita Patil", "gemma4:e4b")
    assert json.loads(report[6])["pages"][0]["reply"]["results"][0]["value_text"] == "7.2"
    assert query("SELECT test_code, raw_value_text, page, status, check_notes FROM results ORDER BY id") == [
        ("HBA1C", "7.2", 1, "needs_check", "not verified yet"),
        ("HB", "12.1", 1, "needs_check", "not verified yet"),
    ]
    assert (storage / "originals" / f"{out['sha256']}.pdf").read_bytes() == pdf.read_bytes()


def test_same_file_twice_is_skipped_without_calling_gemma(storage, gemma, make_pdf, report_page, capsys):
    pdf = make_pdf([report_page])
    run(pdf)
    calls = len(gemma.pages)
    assert run(pdf) is None
    assert len(gemma.pages) == calls
    assert "already extracted as report #1" in capsys.readouterr().err


def test_force_replaces_the_old_report(storage, gemma, make_pdf, report_page):
    pdf = make_pdf([report_page])
    first = run(pdf)
    second = run(pdf, force=True)
    assert second["report_id"] != first["report_id"]
    assert query("SELECT COUNT(*) FROM reports") == [(1,)]
    assert query("SELECT COUNT(*) FROM results") == [(2,)]


def test_missing_date_and_no_tests_are_warnings_not_errors(storage, monkeypatch, make_pdf, report_page):
    monkeypatch.setattr(extract, "ask_gemma", FakeGemma(first=EMPTY_REPLY))
    out = run(make_pdf([report_page]))
    assert any("No sample collection date" in w for w in out["warnings"])
    assert any("No MVP tests" in w for w in out["warnings"])
    assert query("SELECT COUNT(*) FROM reports") == [(1,)]


def test_unreadable_date_is_a_warning(storage, monkeypatch, make_pdf, report_page):
    monkeypatch.setattr(extract, "ask_gemma", FakeGemma(first={**GOOD_REPLY, "sample_date": "Sep 2026"}))
    out = run(make_pdf([report_page]))
    assert out["sample_date"] is None and out["sample_date_text"] == "Sep 2026"
    assert any("could not be read as a date" in w for w in out["warnings"])


def test_scanned_page_goes_to_vision_and_marks_report(storage, gemma, make_pdf, report_page):
    run(make_pdf([report_page, []]))
    assert [p.mode for p in gemma.pages] == ["text", "vision"]
    assert query("SELECT is_scanned FROM reports") == [(1,)]


def test_ollama_failing_mid_report_saves_nothing(storage, monkeypatch, make_pdf, report_page):
    def ask(page, model, retry=False):
        if page.number == 2:
            raise ExtractError("Lost the connection to Ollama while reading the report. Is it still running?")
        return PageExtraction.model_validate(GOOD_REPLY)

    monkeypatch.setattr(extract, "ask_gemma", ask)
    with pytest.raises(ExtractError):
        run(make_pdf([report_page, report_page]))
    assert query("SELECT COUNT(*) FROM reports") == [(0,)]


def test_path_with_apostrophe_and_spaces(storage, gemma, make_pdf, report_page):
    pdf = make_pdf([report_page], name="Dnyanesh's Asus/lab reports/Mom's report 2026.pdf")
    out = run(pdf)
    assert out["report_id"] == 1
    assert (storage / "originals" / f"{out['sha256']}.pdf").exists()


def test_main_prints_json_on_stdout(storage, gemma, make_pdf, report_page, capsys):
    assert main([str(make_pdf([report_page]))]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["results"][0]["value_text"] == "7.2"
    assert "page 1/1 · text · 2 results" in captured.err


def test_main_keeps_non_ascii_text_readable(storage, monkeypatch, make_pdf, report_page, capsys):
    reply = {**GOOD_REPLY, "patient_name": "श्रीमती सुनीता पाटील", "results": [
        {"test_code": "TSH", "raw_name": "TSH", "value_text": "4.82", "unit": "µIU/mL", "ref_text": "0.35 - 5.50"},
    ]}
    monkeypatch.setattr(extract, "ask_gemma", FakeGemma(first=reply))
    assert main([str(make_pdf([report_page]))]) == 0
    stdout = capsys.readouterr().out
    assert "श्रीमती सुनीता पाटील" in stdout and "µIU/mL" in stdout


def test_main_reports_errors_in_one_line(storage, tmp_path, capsys):
    missing = tmp_path / "missing.pdf"
    assert main([str(missing)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.strip() == f"error: File not found: {missing}"


def test_main_ctrl_c_saves_nothing(storage, monkeypatch, make_pdf, report_page, capsys):
    def ask(page, model, retry=False):
        raise KeyboardInterrupt

    monkeypatch.setattr(extract, "ask_gemma", ask)
    assert main([str(make_pdf([report_page]))]) == 130
    assert "Cancelled" in capsys.readouterr().err
    assert query("SELECT COUNT(*) FROM reports") == [(0,)]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_extract_run.py -v`
Expected: collection error, `ImportError: cannot import name 'main'`

- [ ] **Step 3: Add the imports to the top of `app/extract.py`**

The final import block:
```python
import argparse
import hashlib
import json
import re
import shutil
import sys
import time
from contextlib import closing
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Literal

import httpx
import ollama
import pymupdf
from dateutil import parser as dateparser
from pydantic import BaseModel, ValidationError

from app import db
```

- [ ] **Step 4: Append `run`, `main` and the entry point to `app/extract.py`**

```python
def _storage_path(path: Path) -> str:
    """How a stored file is recorded in the DB: relative to the repo root when inside it."""
    try:
        return path.resolve().relative_to(db.ROOT).as_posix()
    except ValueError:
        return str(path.resolve())


def run(pdf_path: Path, *, model: str = DEFAULT_MODEL, force: bool = False,
        db_path: Path | None = None, originals_dir: Path | None = None) -> dict | None:
    """Extract one PDF, save it, and return the output JSON as a dict.

    Returns None (saving nothing) when the same file was already extracted and
    force is False. Raises ExtractError for problems the user can fix. Nothing is
    written to the database until every page has been read.
    """
    originals_dir = originals_dir or db.ORIGINALS_DIR
    with open_pdf(pdf_path) as doc, closing(db.connect(db_path)) as conn:
        sha256 = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
        existing = db.find_report_id(conn, sha256)
        if existing is not None and not force:
            log(f"{pdf_path.name} was already extracted as report #{existing}. Use --force to redo it.")
            return None

        pages = read_pages(doc)
        log(f"Reading {pdf_path.name}: {len(pages)} page(s) with {model}. "
            "The first page also loads the model, so it takes longer.")
        started = time.perf_counter()
        extraction = extract_pages(pages, lambda page, retry: ask_gemma(page, model, retry))
        seconds = round(time.perf_counter() - started, 1)

        header = extraction.header
        warnings = list(extraction.warnings)
        sample_date = parse_date(header["sample_date"])
        report_date = parse_date(header["report_date"])
        if header["sample_date"] is None:
            warnings.append("No sample collection date found; the report can't go on the timeline until one is added.")
        elif sample_date is None:
            warnings.append(f"Sample date '{header['sample_date']}' could not be read as a date.")
        if header["report_date"] is not None and report_date is None:
            warnings.append(f"Report date '{header['report_date']}' could not be read as a date.")
        if not extraction.results:
            warnings.append("No MVP tests found on any page.")

        originals_dir.mkdir(parents=True, exist_ok=True)
        stored = originals_dir / f"{sha256}.pdf"
        if not stored.exists():
            shutil.copyfile(pdf_path, stored)

        report = {
            "lab_name": header["lab_name"],
            "sample_date": sample_date,
            "report_date": report_date,
            "source": "upload",
            "file_path": _storage_path(stored),
            "sha256": sha256,
            "is_scanned": int(any(page.mode == "vision" for page in pages)),
            "patient_name_raw": header["patient_name"],
            "patient_age_raw": header["age"],
            "patient_sex_raw": header["sex"],
            "extract_model": model,
            "extract_seconds": seconds,
            "raw_json": json.dumps({"pages": extraction.replies}, ensure_ascii=False),
        }
        rows = [{
            "test_code": r["test_code"],
            "raw_name": r["raw_name"],
            "raw_value_text": r["value_text"],
            "unit": r["unit"],
            "ref_text": r["ref_text"],
            "page": r["page"],
            "status": "needs_check",
            "check_notes": "not verified yet",
        } for r in extraction.results]
        report_id = db.save_report(conn, report, rows, replace=force)

    for warning in warnings:
        log(f"warning: {warning}")
    log(f"Saved report #{report_id}: {len(rows)} result(s), {seconds} s.")
    return {
        "report_id": report_id,
        "file": str(pdf_path),
        "sha256": sha256,
        "model": model,
        "seconds": seconds,
        "patient_name": header["patient_name"],
        "age": header["age"],
        "sex": header["sex"],
        "lab_name": header["lab_name"],
        "sample_date": sample_date,
        "sample_date_text": header["sample_date"],
        "report_date": report_date,
        "report_date_text": header["report_date"],
        "pages": extraction.pages,
        "warnings": warnings,
        "results": [{
            "page": r["page"],
            "test_code": r["test_code"],
            "raw_name": r["raw_name"],
            "value_text": r["value_text"],
            "unit": r["unit"],
            "ref_text": r["ref_text"],
            "status": "needs_check",
        } for r in extraction.results],
    }


def _utf8_console() -> None:
    """Patient names and units (µ, Devanagari) must print even on a Windows code page."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


def main(argv: list[str] | None = None) -> int:
    _utf8_console()
    parser = argparse.ArgumentParser(
        prog="python -m app.extract",
        description="Extract the MVP lab tests from a lab report PDF with local Gemma, "
                    "print them as JSON and save them to storage/arogya.db.",
    )
    parser.add_argument("pdf", type=Path, help="path to the lab report PDF")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help=f"Ollama model (default: {DEFAULT_MODEL}; faster fallback: gemma4:e2b)")
    parser.add_argument("--force", action="store_true",
                        help="re-extract a report that is already saved, replacing its rows")
    args = parser.parse_args(argv)
    try:
        output = run(args.pdf, model=args.model, force=args.force)
    except ExtractError as error:
        log(f"error: {error}")
        return 1
    except KeyboardInterrupt:
        log("Cancelled. Nothing was saved.")
        return 130
    if output is not None:
        print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_extract_run.py -v`
Expected: `12 passed`

- [ ] **Step 6: Write `tests/test_live_gemma.py`**

```python
"""Calls the real local Gemma through Ollama (about a minute).

Run with:  AROGYA_LIVE=1 .venv/Scripts/python -m pytest tests/test_live_gemma.py -v
"""
import os

import pytest

from app.extract import run

pytestmark = pytest.mark.skipif(
    os.environ.get("AROGYA_LIVE") != "1",
    reason="set AROGYA_LIVE=1 to call the real local Gemma",
)


def test_real_gemma_extracts_synthetic_report(make_pdf, report_page, tmp_path):
    out = run(make_pdf([report_page]), db_path=tmp_path / "live.db", originals_dir=tmp_path / "originals")
    # 2 MVP tests found; "Estimated Average Glucose" (a look-alike) left out
    assert {r["test_code"]: r["value_text"] for r in out["results"]} == {"HBA1C": "7.2", "HB": "12.1"}
    assert out["sample_date"] == "2026-09-12"
    assert out["lab_name"] == "SUNRISE DIAGNOSTICS"
```

- [ ] **Step 7: Write `README.md`**

````markdown
# Arogya Vahi (आरोग्य वही)

Keeps a family's lab reports in one place and builds one timeline per test across labs,
using Gemma 4 running locally through Ollama. Reports never leave the laptop.

> **Status:** Friday checkpoint. Only the extraction command exists so far. It reads a
> report PDF and saves the test results to a local SQLite database. Every value is
> marked `needs_check` until the verification step is built.

## Setup (Windows, PowerShell)

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
ollama pull gemma4:e4b
```

## Extract a report

```powershell
.venv\Scripts\python -m app.extract "C:\path\to\report.pdf"
```

- Progress appears in the terminal. The JSON result goes to standard output, so
  `... > storage\report.json` saves it (`storage\` is never committed).
- `--force` re-extracts a report that is already saved.
- `--model gemma4:e2b` uses the smaller, faster model.

Each run stores the original PDF in `storage/originals/<sha256>.pdf` and the report and
its results in `storage/arogya.db`.

Only these 15 tests are extracted: HbA1c, fasting glucose, post-prandial glucose, TSH,
free T4, total cholesterol, LDL, HDL, triglycerides, creatinine, haemoglobin,
vitamin D (25-OH), vitamin B12, uric acid, urea.

## Tests

```powershell
.venv\Scripts\python -m pytest                    # fast tests, no Gemma needed
$env:AROGYA_LIVE = "1"; .venv\Scripts\python -m pytest tests/test_live_gemma.py -v
                                                  # calls the real local Gemma (~1 min)
```

## Privacy

`storage/`, `*.db` and PDFs outside `samples/` are gitignored. Never commit real reports.
````

- [ ] **Step 8: Run the full fast suite**

Run: `.venv/Scripts/python -m pytest -q`
Expected: `65 passed, 1 skipped`

- [ ] **Step 9: Run the live Gemma test**

Run: `AROGYA_LIVE=1 .venv/Scripts/python -m pytest tests/test_live_gemma.py -v`
Expected: `1 passed` (about a minute; Ollama must be running)

- [ ] **Step 10: Smoke-test the real command end to end**

First check that `storage/` does not exist yet (`ls storage` → "No such file or directory"). If it exists, it may hold real reports: **stop here and ask** instead of running Steps 10–11.

Build a synthetic PDF outside the repo (set `SCRATCH` to the session scratchpad directory if you have one), then run the CLI three times: first run, duplicate, `--force`.

```bash
SCRATCH="${SCRATCH:-$(mktemp -d)}"
.venv/Scripts/python -c "
import sys, pymupdf
doc = pymupdf.open(); page = doc.new_page()
for x, y, t in [(50,60,'SUNRISE DIAGNOSTICS'),(50,80,'Patient Name : Mrs. Sunita Patil'),(50,100,'Collected : 12/09/2026 08:10'),
                (50,150,'Glycosylated Haemoglobin (HbA1c)'),(260,150,'7.2'),(320,150,'%'),(380,150,'4.0 - 5.6'),
                (50,170,'TSH (Ultrasensitive)'),(260,170,'4.82'),(320,170,'µIU/mL'),(380,170,'0.35 - 5.50')]:
    page.insert_text((x, y), t, fontsize=10)
doc.save(sys.argv[1])" "$SCRATCH/synthetic report.pdf"
.venv/Scripts/python -m app.extract "$SCRATCH/synthetic report.pdf" > "$SCRATCH/out.json"; echo "exit $?"
.venv/Scripts/python -c "import json,sys; d=json.load(open(sys.argv[1],encoding='utf-8')); print(d['sample_date'], [(r['test_code'], r['value_text'], r['unit']) for r in d['results']])" "$SCRATCH/out.json"
.venv/Scripts/python -m app.extract "$SCRATCH/synthetic report.pdf"; echo "exit $?"
.venv/Scripts/python -m app.extract "$SCRATCH/synthetic report.pdf" --force > /dev/null; echo "exit $?"
```
Expected:
- first run: progress on stderr, `exit 0`, then `2026-09-12 [('HBA1C', '7.2', '%'), ('TSH', '4.82', 'µIU/mL')]`
- second run: `... was already extracted as report #1. Use --force to redo it.`, `exit 0`
- `--force`: `Saved report #2 ...`, `exit 0`

- [ ] **Step 11: Remove the smoke-test data from the real storage**

`storage/` was created by Step 10 and holds only the synthetic report. Check, then delete it so the real database starts empty:
```bash
.venv/Scripts/python -c "import sqlite3; print(sqlite3.connect('storage/arogya.db').execute('SELECT id, lab_name, patient_name_raw FROM reports').fetchall())"
ls storage/originals
```
Expected: `[(2, 'SUNRISE DIAGNOSTICS', 'Mrs. Sunita Patil')]` and exactly one file in `storage/originals`. If anything else is listed, stop and ask. Otherwise:
```bash
rm -rf storage
```

- [ ] **Step 12: Check nothing private is staged, then commit**

```bash
git status --short
```
Expected: only `README.md`, `app/extract.py`, `tests/test_extract_run.py`, `tests/test_live_gemma.py` (no `storage/`, no `.db`, no `.pdf`).
```bash
git add README.md app/extract.py tests/test_extract_run.py tests/test_live_gemma.py
git commit -m "feat(extract): command-line tool that prints JSON and saves to SQLite" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
