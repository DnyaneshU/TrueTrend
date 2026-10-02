# Arogya Vahi (आरोग्य वही)

Keeps a family's lab reports in one place and builds one timeline per test across labs,
using Gemma 4 running locally through Ollama. Reports never leave the laptop.

> **Status:** the extraction command works. It reads a report PDF and saves the test
> results to a local SQLite database. Every value is marked `needs_check` until the
> verification step is built.

## Setup (Windows, PowerShell)

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
ollama pull gemma4:e4b
```

## Extract a report

```powershell
.venv\Scripts\python -m app.extract "C:\path\to\report.pdf"
```

- Progress and warnings appear in the terminal; the JSON result goes to standard output.
- `--force` re-extracts a report that is already saved.
- `--model gemma4:e2b` uses the smaller, faster model.

Each run stores the original PDF in `storage/originals/<sha256>.pdf` and the report and
its results in `storage/arogya.db`.

## How it reads a report

- **Digital pages:** the text is rebuilt line by line so a table row stays together
  (`HbA1c | 7.2 | % | 4.0 - 5.6`); Gemma copies each value exactly as printed into a
  fixed JSON schema.
- **Scanned pages:** Gemma first transcribes the page image to text, then that text goes
  through the same step.
- **Checks in code, not in the model:** page numbers and dates (read day-first, never
  guessed) come from code, and a row whose printed name contradicts its test code (for
  example "Estimated Average Glucose" filed as fasting glucose) is dropped with a warning.

## Configuration

| What | Where |
|---|---|
| The tests that are extracted, the names labs print for them, and look-alike tests to reject | `data/lab_tests.toml` |
| The instructions sent to Gemma | `app/prompts/` |
| Model, Ollama options, page-reading thresholds, storage folder | `app/config.py`, overridable with `AROGYA_*` environment variables or a `.env` file, e.g. `AROGYA_MODEL=gemma4:e2b` |

Changing the catalog or prompts needs no code changes.

## Code layout

| Module | Job |
|---|---|
| `app/pages.py` | PDF to one page input per page (rebuilt text, or an image for scans) |
| `app/gemma.py` | The calls to Gemma and the JSON schema its answers must fit |
| `app/lab_tests.py` | Loads the test catalog; checks a printed name against a test code |
| `app/dates.py` | Reads printed dates |
| `app/pipeline.py` | Merges the pages' answers into one report |
| `app/extract.py` | The command: stores the original, saves to SQLite, prints JSON |
| `app/models.py`, `app/db.py`, `app/schema.sql`, `app/config.py` | Data models, storage, settings |

## Tests

```powershell
.venv\Scripts\python -m pytest                    # fast tests, no Gemma needed
$env:AROGYA_LIVE = "1"; .venv\Scripts\python -m pytest tests/test_live_gemma.py
                                                  # calls the real local Gemma (~1 min)
.venv\Scripts\ruff check app tests; .venv\Scripts\ruff format app tests
```

Date reading is tested with [Hypothesis](https://hypothesis.readthedocs.io/): it generates
dates in every common Indian lab format and checks each reads back exactly.

## Privacy

`storage/`, `*.db`, PDFs and report photos outside `samples/` are gitignored. Never commit
real reports.
