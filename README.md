# Arogya Vahi (आरोग्य वही)

Keeps a family's lab reports in one place and builds one timeline per test across labs,
using Gemma 4 running locally through Ollama. Reports never leave the laptop.

> **Status:** the extraction command works. It reads a report PDF, converts each result to
> numbers in a standard unit, checks it against the PDF, and saves it to a local SQLite
> database. A result is `verified` only when code finds it printed in its test's row and it
> passes the checks below; anything else is `needs_check`, with notes saying why. The
> summary command says, in Marathi, what changed since the previous report and whether
> each change is real or normal variation.

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

After editing the catalog (names, units, believable limits) or updating the app, re-check
everything already saved. Gemma is not called; each result is rebuilt from what was printed
and checked against the stored original:

```powershell
.venv\Scripts\python -m app.recheck
```

## The Marathi summary

```powershell
.venv\Scripts\python -m app.summary          # up to 3 sentences + questions for the doctor
.venv\Scripts\python -m app.summary --json   # with every finding and judged change
```

It covers the latest report of the person named on it, compared with their earlier results:

- **Real change or normal variation:** two results differ for real only when the change
  is bigger than the Reference Change Value, the log-normal one EFLM recommends (so a
  rise must be bigger than a fall), from `CV = √(CVa² + CVi²)` with between-lab
  variation added to CVa when the labs differ. CVi comes from the
  [EFLM Biological Variation Database](https://biologicalvariation.eu/); CVa is EFLM's
  minimum standard, 0.75 × CVi, since a lab's own precision is unknown, which keeps the
  judgement cautious; between-lab CVs come from published external quality assessment.
  Each test's sources are in `data/lab_tests.toml`. A test without sourced constants
  (post-prandial glucose), or two labs without a between-lab CV (TSH, free T4,
  haemoglobin, B12), is not judged rather than guessed.
- **What is said, most important first:** a real change in the same direction three
  sample dates in a row, a real change since the previous sample, a result outside that
  lab's printed range (only when the range itself was found in the value's row in the
  PDF). A result that needs checking is never stated; the summary says how many there are.
- **Numbers come from code only.** Sentences are templates in `data/summary_mr.toml`
  whose numbers are placeholders, filled with results exactly as the report prints them,
  in Devanagari digits. The templates read right whatever the test name's gender.
- **One person:** until reports are matched to patients, reports naming someone other
  than the person on the latest report are left out, with a warning.

Each run stores the original PDF in `storage/originals/<sha256>.pdf` and the report and
its results in `storage/arogya.db`.

## How it reads a report

- **Digital pages:** the text is rebuilt line by line so a table row stays together
  (`HbA1c | 7.2 | % | 4.0 - 5.6`); Gemma copies each value exactly as printed into a
  fixed JSON schema.
- **Scanned pages:** Gemma first transcribes the page image to text, then that text goes
  through the same step.
- **Normalising:** each value becomes a number in the test's standard unit (vitamin D
  150 nmol/L -> 60.1 ng/mL; "< 148" -> 148 with qualifier "<"), and the lab's normal range
  ("74 - 106", "<200", "Normal : <150") becomes low/high numbers in the same unit. Nothing
  is guessed: an unknown unit, a word result or a risk-category table ("Low: <40") is left
  empty with a note.
- **Checks in code, not in the model:** page numbers and dates (read day-first, never
  guessed) come from code, and a row whose printed name contradicts its test code (for
  example "Estimated Average Glucose" filed as fasting glucose) is dropped with a warning.
- **Verifying:** code looks for each value in the PDF's own text, never in Gemma's reading
  of it. The value must be printed as a word of its own in a row whose label names the
  test, so a number from the neighbouring row, the reference range or a paragraph is not
  accepted; where it is found is saved (`bbox_json`) for highlighting later. The value must
  also be in a known unit and within the test's believable limits (69 % HbA1c is a dropped
  decimal point), and LDL and HDL can't exceed total cholesterol. Values on scanned pages
  have no PDF text to check against, so they always need a check. Two further checks only
  add a note: LDL against the Friedewald estimate, and HbA1c's average glucose (ADAG)
  against fasting glucose.

## Configuration

| What | Where |
|---|---|
| The tests that are extracted, the names labs print for them, look-alike tests to reject, standard units, unit conversions, believable limits, and the cross-test checks | `data/lab_tests.toml` |
| Variation constants (CVi, CVa, between-lab CV) and their sources | `data/lab_tests.toml` |
| The summary's Marathi sentences and doctor questions | `data/summary_mr.toml` |
| The instructions sent to Gemma | `app/prompts/` |
| Model, Ollama options, page-reading and verification thresholds, storage folder | `app/config.py`, overridable with `AROGYA_*` environment variables or a `.env` file, e.g. `AROGYA_MODEL=gemma4:e2b` |

Changing the catalog or prompts needs no code changes.

## Code layout

| Module | Job |
|---|---|
| `app/pages.py` | PDF to one page input per page (rebuilt text, or an image for scans) |
| `app/gemma.py` | The calls to Gemma and the JSON schema its answers must fit |
| `app/lab_tests.py` | Loads the test catalog; checks a printed name against a test code |
| `app/dates.py` | Reads printed dates |
| `app/normalize.py` | Values, units and normal ranges as numbers in standard units |
| `app/verify.py` | Each result checked against the PDF, its believable limits and the report's other results |
| `app/pipeline.py` | Merges the pages' answers into one report |
| `app/extract.py` | The command: stores the original, saves to SQLite, prints JSON |
| `app/recheck.py` | The command that re-normalises and re-verifies everything saved |
| `app/change.py` | Real change or normal variation, by the Reference Change Value |
| `app/summary.py` | The command for the Marathi summary and doctor questions |
| `app/marathi.py` | Devanagari digits and Marathi dates |
| `app/models.py` | The data passed between steps, saved, and printed (Pydantic models) |
| `app/db.py`, `app/schema.sql` | SQLite storage |
| `app/config.py`, `app/text.py`, `app/console.py`, `app/errors.py` | Settings, shared text helpers, console output, user-facing errors |

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
