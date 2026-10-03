# Arogya Vahi (आरोग्य वही)

Keeps a family's lab reports in one place, checks every value against the report it came
from, and says in Marathi what changed and whether the change is real or normal
variation. It runs on a home laptop with Gemma 4 through Ollama: reports never leave it.

> **Not medical advice.** Arogya Vahi only compares results with the lab's printed range
> and with earlier results, and suggests questions to ask the doctor. It does not
> diagnose, and it never advises on diet or medicine.

## Requirements

- Python 3.11 or newer
- [Ollama](https://ollama.com), running on the same computer, with `gemma4:e4b`
  (or the smaller, faster `gemma4:e2b`)
- A laptop with about 16 GB of RAM; a GPU makes reading faster but is not required

## Install

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
ollama pull gemma4:e4b
```

On macOS or Linux use `.venv/bin/python` instead of `.venv\Scripts\python`.

## Use

```powershell
arogya-extract "C:\path\to\report.pdf"   # read a report, verify it, save it, print it as JSON
arogya-summary                           # the Marathi summary of the latest report
arogya-summary --json                    # ... with every finding and judged change
arogya-recheck                           # re-verify everything saved, without calling Gemma
```

Each command is also `python -m arogya_vahi.<extract|summary|recheck>`, and each has
`--help`. Progress and warnings go to the terminal; JSON goes to standard output.

- `arogya-extract --force` reads a report that is already saved again; `--model
  gemma4:e2b` uses the smaller model.
- Run `arogya-recheck` after editing the catalog (names, units, believable limits) or
  after updating the app.

## How it works

1. **Reading.** Digital pages are rebuilt line by line so a table row stays together
   (`HbA1c | 7.2 | % | 4.0 - 5.6`), and Gemma copies each value exactly as printed into a
   fixed JSON schema. Scanned pages are first transcribed by Gemma, then read the same way.
   Page numbers and dates (read day-first, never guessed) come from code, and a row whose
   printed name contradicts its test code (say "Estimated Average Glucose" filed as fasting
   glucose) is dropped with a warning.
2. **Normalising.** Each value becomes a number in the test's standard unit (vitamin D
   150 nmol/L is 60.1 ng/mL; "< 148" is 148 with qualifier "<"), and the lab's normal
   range ("74 - 106", "<200", "Normal : <150") becomes limits in the same unit. Nothing is
   guessed: an unknown unit, a word result or a risk-category table ("Low: <40") is left
   empty with a note.
3. **Verifying.** Code looks for each value in the PDF's own text, never in Gemma's reading
   of it. A result is `verified` only when:
   - its value is printed with the same qualifier, as a value of its own (not inside a
     sentence), in a row whose label names the test, with its unit printed in that row;
   - it is within the test's believable limits (69 % HbA1c is a dropped decimal point);
   - LDL and HDL are not more than total cholesterol.

   Anything else is `needs_check`, with notes saying why. Values on scanned pages have no
   PDF text to check against, so they always need a check. The normal range is used only
   when its limits are printed in the value's row too, and the sample date only when it is
   printed in the PDF. Where each value is printed is saved, for highlighting it later.
4. **Changes.** Two results of a test differ for real only when the change is bigger than
   the Reference Change Value, the log-normal one the EFLM recommends (a rise must be
   bigger than a fall), from `CV = √(CVa² + CVi²)`, with between-lab variation added to CVa
   when the labs differ. CVi comes from the
   [EFLM Biological Variation Database](https://biologicalvariation.eu/); CVa is EFLM's
   minimum standard, 0.75 × CVi, since a lab's own precision is unknown, which keeps the
   judgement cautious; between-lab CVs come from published external quality assessment.
   Each source is cited in `arogya_vahi/data/lab_tests.toml`. A test without sourced
   constants, or two labs without a between-lab CV, is not judged rather than guessed.
5. **The Marathi summary.** At most three sentences, most important first: a real change
   in the same direction three times in a row, a real change since the previous sample, a
   result outside that lab's verified range. Each comes with a question for the doctor.
   Sentences are templates whose numbers are placeholders, filled by code with results
   exactly as the report prints them, in Devanagari digits. A result that needs checking is
   never stated; the summary only says how many there are. It covers the person named on
   the latest report; reports naming someone else, or no one, are left out with a warning.

## Configuration

| What | Where |
|---|---|
| The tests read, the names labs print for them, look-alike tests to reject, standard units, conversions, believable limits, variation constants with their sources, cross-test checks | `arogya_vahi/data/lab_tests.toml` |
| The summary's Marathi sentences and doctor questions | `arogya_vahi/data/summary_mr.toml` |
| The instructions sent to Gemma | `arogya_vahi/prompts/` |
| Model, Ollama host and options, page-reading and verification thresholds, storage folder | `arogya_vahi/config.py` |

Every setting in `config.py` can be overridden with an `AROGYA_*` environment variable or
a `.env` file in the folder you run the commands from, for example:

```ini
AROGYA_MODEL=gemma4:e2b
AROGYA_STORAGE_DIR=D:/arogya
```

The database (`arogya.db`) and the stored original PDFs (`originals/<sha256>.pdf`) live in
the storage folder: `storage/` in a source checkout, otherwise the user's data folder
(`%LOCALAPPDATA%\arogya-vahi` on Windows).

### Adding a test

Add a `[[tests]]` entry to `arogya_vahi/data/lab_tests.toml` (the comments at the top of
the file describe every field), then run `arogya-recheck`. No code changes are needed.
Leave `variation` out until its constants can be cited: changes in the test are then shown
but not judged.

## Privacy

- Reports are sent only to the Ollama at `AROGYA_OLLAMA_HOST`, which must be this computer
  (`http://127.0.0.1:11434` by default). A remote host is refused unless
  `AROGYA_ALLOW_REMOTE_OLLAMA=true`. The `OLLAMA_HOST` environment variable is ignored.
- `storage/`, databases, PDFs and report photos are gitignored. Never commit real reports;
  tests use synthetic ones only.

## Limitations

- 15 tests are read: HbA1c, fasting and post-prandial glucose, TSH, free T4, total, LDL
  and HDL cholesterol, triglycerides, creatinine, haemoglobin, vitamin D (25-OH),
  vitamin B12, uric acid and urea.
- Values on scanned pages are never verified; a confirmation screen is planned.
- Until patient matching exists, a person is recognised by the name printed on the report.
- Post-prandial glucose changes are never judged (no published biological variation);
  TSH, free T4, haemoglobin and B12 changes are judged only within one lab.

## Troubleshooting

| Message | What to do |
|---|---|
| `Can't reach Ollama` | Start the Ollama app (or run `ollama serve`) and try again |
| `Model gemma4:e4b is not installed` | `ollama pull gemma4:e4b` |
| `... is password-protected` | Open the PDF once, save a copy without a password, and use the copy |
| `The database could not be used (database is locked)` | Another command is still running; wait for it to finish |
| `... is not printed in the PDF's text (a scan?)` | The report is saved, but stays off the timeline until checked |

## Development

```powershell
.venv\Scripts\python -m pytest                                # fast tests; never calls Gemma
$env:AROGYA_LIVE = "1"; .venv\Scripts\python -m pytest -m live; Remove-Item Env:AROGYA_LIVE
                                                              # the real local Gemma (~2 min)
.venv\Scripts\ruff check arogya_vahi tests; .venv\Scripts\ruff format arogya_vahi tests
```

Tests run with the default settings and a temporary storage folder, whatever is in your
`.env`. Parsing and the summary's numbers are property-tested with
[Hypothesis](https://hypothesis.readthedocs.io/); `HYPOTHESIS_PROFILE=ci` makes those runs
reproducible. CI (`.github/workflows/ci.yml`) runs the linters and tests on Windows and
Linux, and installs the built wheel in a clean environment to check that it runs.

| Module | Job |
|---|---|
| `pages.py` | PDF to one page input per page (rebuilt text, or an image for scans) |
| `gemma.py` | The calls to Gemma and the JSON schema its answers must fit |
| `pipeline.py` | Merges the pages' answers into one report |
| `normalize.py` | Values, units and normal ranges as numbers in standard units |
| `verify.py` | Each result checked against the PDF, its believable limits and the report's other results |
| `change.py` | Real change or normal variation, by the Reference Change Value |
| `summary.py` | The Marathi summary and doctor questions (the `arogya-summary` command) |
| `extract.py`, `recheck.py`, `cli.py` | The other commands, and what all commands share |
| `db.py`, `schema.sql` | SQLite storage and migrations |
| `lab_tests.py`, `dates.py`, `text.py`, `marathi.py` | The catalog; printed dates, numbers and names; Marathi numbers and dates |
| `models.py`, `config.py`, `resources.py`, `console.py`, `errors.py` | Data models, settings, packaged files, console output, user-facing errors |
