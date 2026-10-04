# TrueTrend (आरोग्य वही)

Keeps a family's lab reports in one place, checks every value against the report it came
from, and says in Marathi what changed and whether the change is real or normal
variation. It runs on a home laptop with Gemma 4 through Ollama: reports never leave it.

> **Not medical advice.** TrueTrend only compares results with the lab's printed range
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
truetrend-extract "C:\path\to\report.pdf"   # read a report, verify it, save it, print it as JSON
truetrend-summary                           # the Marathi summary of the latest report
truetrend-summary --json                    # ... with every finding and judged change
truetrend-summary --patient 2               # ... of patient #2's latest report
truetrend-patients                          # who each report is for
truetrend-patients merge 1 3                # patients #1 and #3 are the same person
truetrend-patients assign 7 2               # report #7 is patient #2's ("new": someone new)
truetrend-recheck                           # re-verify everything saved, without calling Gemma
truetrend-serve                             # the web server and its API, on this computer
```

Each command is also `python -m truetrend.<extract|summary|patients|recheck|server>`, and each has
`--help`. Progress and warnings go to the terminal; JSON goes to standard output.

- `truetrend-extract --force` reads a report that is already saved again; `--model
  gemma4:e2b` uses the smaller model.
- Run `truetrend-recheck` after editing the catalog (names, units, believable limits) or
  after updating the app. It also matches reports saved before patient matching existed
  (so do `truetrend-summary` and `truetrend-patients`).

## The web app

`truetrend-serve` opens the website at `http://127.0.0.1:8000`. It is built for a phone held
by someone who is not looking for a computer, and it needs no internet: even the chart
library is served from this computer.

**Two languages, on purpose.** The interface is English -- every label, button and screen
name. What the app *says about her results* is Marathi: the summary sentences, the
questions for the doctor, and the voice. Those are the sentences she is meant to read and
hear, and they are built by the server from templates in
`truetrend/data/summary_mr.toml`, with every number filled in by code. The page never
writes one of them itself; it passes them through as they come.

The first person to open it makes an account, and after that the reports are behind it.
The account is made here and only here -- sign-in is the OAuth 2.0 password grant served
by this app itself, so no account is made with Google or anyone else and no password
leaves the laptop. A session lasts a year, so she signs in once on her phone.

**The five screens**, along the bottom of the phone:

| Screen | What it is for |
|---|---|
| **Summary** | What changed in the latest report, in at most three Marathi sentences, with a **Listen** button that reads them aloud, and the questions to ask at the next visit |
| **Add** | Pick a report from the phone, and watch it go from Waiting to Reading to Done. It shows the WhatsApp -> Save to Files steps for anyone who has not done it before |
| **Changes** | One row per test with its latest value; tap it for the chart, the lab's normal range behind it, and every change judged as a real change or normal variation |
| **Check** | Everything waiting for a person: a value the code could not find, two patients who may be one person, the same report sent twice |
| **People** | Which report belongs to whom, and the account |

A first-time reader is shown a short guide -- what the app does, what each of the five
buttons is for, what each colour means, and that it never gives medical advice. It can be
read again any time from **Guide**.

**Tapping a value shows where it is printed.** Safari ignores a PDF link's `#page=N`, so
tapping a point on a chart would only ever open page 1 of a 19-page report. Instead the
server draws that page as a picture with the value ringed on it, so she sees the number
in its own row, in the lab's own layout.

**On an iPhone**, she saves the report from WhatsApp (share -> Save to Files), opens the
website, taps **Add** and picks it. On Android the browser's share sheet can send it
straight to the app.

## The server and its API

`truetrend-serve` runs on `http://127.0.0.1:8000`, on this computer only. Sent files
wait in a queue, and one background worker reads them with Gemma, one at a time, in the
order they came (each takes minutes and the whole GPU). The queue is kept in the database,
so files left half-read by a stop are read again on the next start. A phone reaches the
server through [Tailscale](https://tailscale.com): `tailscale serve 8000` adds HTTPS and
lets in only the family's own devices.

| Request | What it does |
|---|---|
| `POST /api/uploads` (files) | Queue PDFs or photos of reports; a photo becomes a one-page PDF, and a file sent twice is read once |
| `GET /api/uploads` | The latest uploads and how reading each went |
| `POST /share-target` (files) | The same, for a report shared from WhatsApp; returns to the app |
| `GET /api/summary?patient=N` | The Marathi summary (the latest report's patient by default) |
| `GET /api/timelines?patient=N` | Every test's results, oldest first, with each change judged |
| `GET /api/reports/{id}/original` | The original PDF, to save or print |
| `GET /api/reports/{id}/page/{n}?result=R` | Page `n` as a picture, with result `R`'s value ringed where the report prints it |
| `GET /api/questions?patient=N` | What waits for a person: values to check, same-named patients, reports that could be two patients, likely duplicates |
| `POST /api/results/{id}/review` | `{"decision": "verified" \| "rejected"}`: a person compared the value with the original |
| `GET /api/patients` | Every patient and their reports |
| `POST /api/patients/merge` | `{"keep": 1, "other": 3}`: two patients are one person |
| `PUT /api/reports/{id}/patient` | `{"patient_id": 2}`, or `null` for someone new |
| `DELETE /api/reports/{id}` | Delete a report sent twice (its stored original is kept) |
| `GET /api/me` | Who this browser is signed in as, and whether an account exists yet |
| `POST /api/sign-up` | `{"name": ..., "password": ...}`: make an account (the first one is open; after that only a signed-in person adds more) |
| `POST /api/sign-in` | The OAuth 2.0 password grant; returns a bearer token and sets the session cookie |
| `POST /api/sign-out` | Sign this device out; the account's other devices stay signed in |

Every request above needs a session once an account exists, as a cookie (what the browser
keeps) or an `Authorization: Bearer` header (what a script or an iOS Shortcut sends).
Before the first account is made the app is open, because refusing everything would lock
the only person who can make one out of her own laptop.

A person's decision on a value outranks the code's: `truetrend-recheck` never undoes it.
`/docs` describes every request.

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
   Each source is cited in `truetrend/data/lab_tests.toml`. A test without sourced
   constants, or two labs without a between-lab CV, is not judged rather than guessed.
5. **The Marathi summary.** At most three sentences, most important first: a real change
   in the same direction three times in a row, a real change since the previous sample, a
   result outside that lab's verified range. Each comes with a question for the doctor.
   Sentences are templates whose numbers are placeholders, filled by code with results
   exactly as the report prints them, in Devanagari digits. A result that needs checking is
   never stated; the summary only says how many there are. It covers the patient the latest
   report is for; reports for someone else, or no one, are left out with a warning.
6. **Patients.** Each report is matched to a family member by its printed name, ignoring
   case, punctuation, word order and titles ("Mrs. Sunita Patil" = "PATIL SUNITA"), and a
   middle initial only one of the two prints ("Sunita R. Patil" = "Sunita Patil"). Nothing
   else is guessed: "S. Patil" or "Sunita Ramesh Patil" is a new patient until
   `truetrend-patients merge` joins the two, and then that spelling is remembered. Someone
   else is someone else:
   - "Sunita K. Patil", when the patient is also printed as "Sunita R. Patil" (a middle
     initial is a father's or husband's name);
   - "B/O Sunita Patil", Sunita's baby (and S/O, D/O, W/O);
   - a same-named patient of the other sex (printed, or from "Mr."/"Mrs."), or born more
     than a year apart (from a printed age or date of birth);

   each with a warning saying how to merge them if they are the same person. A report
   that could be two patients, or names no one ("Mrs.", "Patient"), is matched to no one
   and compared with nothing. `truetrend-patients assign` moves a report; a report read
   again with `--force` stays with the patient it was for. Two reports read at once can't
   both add the same new patient.

## Configuration

| What | Where |
|---|---|
| The tests read, the names labs print for them, look-alike tests to reject, standard units, conversions, believable limits, variation constants with their sources, cross-test checks | `truetrend/data/lab_tests.toml` |
| How labs print a person: titles, sex words, relation markers (B/O), placeholder names, age units | `truetrend/data/people.toml` |
| The summary's Marathi sentences and doctor questions | `truetrend/data/summary_mr.toml` |
| The instructions sent to Gemma | `truetrend/prompts/` |
| Model, Ollama host and options, page-reading and verification thresholds, storage folder | `truetrend/config.py` |

Every setting in `config.py` can be overridden with an `TRUETREND_*` environment variable or
a `.env` file in the folder you run the commands from, for example:

```ini
TRUETREND_MODEL=gemma4:e2b
TRUETREND_STORAGE_DIR=D:/truetrend
```

The database (`truetrend.db`) and the stored original PDFs (`originals/<sha256>.pdf`) live in
the storage folder: `storage/` in a source checkout, otherwise the user's data folder
(`%LOCALAPPDATA%\truetrend` on Windows).

### Adding a test

Add a `[[tests]]` entry to `truetrend/data/lab_tests.toml` (the comments at the top of
the file describe every field), then run `truetrend-recheck`. No code changes are needed.
Leave `variation` out until its constants can be cited: changes in the test are then shown
but not judged.

## Privacy

- Reports are sent only to the Ollama at `TRUETREND_OLLAMA_HOST`, which must be this computer
  (`http://127.0.0.1:11434` by default). A remote host is refused unless
  `TRUETREND_ALLOW_REMOTE_OLLAMA=true`. The `OLLAMA_HOST` environment variable is ignored.
- `storage/`, databases, PDFs and report photos are gitignored. Never commit real reports;
  tests use synthetic ones only.
- Accounts are made on this computer and nowhere else. Signing in contacts no one: there
  is no Google, no GitHub, no server but this one, and the app works with the internet
  unplugged. A password is kept only as a PBKDF2-SHA256 hash (600,000 rounds, its own
  salt) and a session token only as its SHA-256, so neither the database nor a backup of
  it reveals either one.
- The website loads nothing from the internet -- no fonts, no analytics, no CDN. Chart.js
  is served from `truetrend/web/vendor/`.

## Limitations

- 15 tests are read: HbA1c, fasting and post-prandial glucose, TSH, free T4, total, LDL
  and HDL cholesterol, triglycerides, creatinine, haemoglobin, vitamin D (25-OH),
  vitamin B12, uric acid and urea.
- Values on scanned pages are never verified; a confirmation screen is planned.
- Patients are matched by printed name, sex and age only; a lab's patient ID is not read.
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
$env:TRUETREND_LIVE = "1"; .venv\Scripts\python -m pytest -m live; Remove-Item Env:TRUETREND_LIVE
                                                              # the real local Gemma (~2 min)
.venv\Scripts\ruff check truetrend tests; .venv\Scripts\ruff format truetrend tests
```

Tests run with the default settings and a temporary storage folder, whatever is in your
`.env`. Parsing and the summary's numbers are property-tested with
[Hypothesis](https://hypothesis.readthedocs.io/); `HYPOTHESIS_PROFILE=ci` makes those runs
reproducible. CI (`.github/workflows/ci.yml`) runs the linters and tests on Windows and
Linux, and installs the built wheel in a clean environment to check that it runs.

`samples/` holds four made-up reports -- three labs, three layouts, one family -- so the
app can be tried without anyone's real results. `python samples/make_samples.py` builds
them again. Their values were chosen against the real Reference Change Values so that the
samples show each verdict the app can give: a rise that beats the threshold, a drift that
does not, and a comparison that is honestly declined because no between-lab CV is
published for that test.

| Module | Job |
|---|---|
| `pages.py` | PDF to one page input per page (rebuilt text, or an image for scans) |
| `gemma.py` | The calls to Gemma and the JSON schema its answers must fit |
| `pipeline.py` | Merges the pages' answers into one report |
| `normalize.py` | Values, units and normal ranges as numbers in standard units |
| `verify.py` | Each result checked against the PDF, its believable limits and the report's other results |
| `change.py` | Real change or normal variation, by the Reference Change Value |
| `summary.py` | The Marathi summary and doctor questions (the `truetrend-summary` command) |
| `patients.py`, `people.py` | Which family member a report is for (the `truetrend-patients` command); printed names, sex and ages |
| `server.py`, `web.py`, `ingest.py`, `jobs.py` | The web server and its API; who is signed in; files people send; the worker that reads them |
| `accounts.py`, `highlight.py` | Accounts and sessions on this computer; a report page drawn with one value ringed |
| `web/` | The website: `index.html`, `styles.css`, and `app.js` over `api.js` (requests), `text.js` (what the page says, and dates), `dom.js`, `chart.js`, `guide.js` |
| `extract.py`, `recheck.py`, `cli.py` | The other commands, and what all commands share |
| `db.py`, `schema.sql` | SQLite storage and migrations |
| `lab_tests.py`, `dates.py`, `text.py`, `marathi.py` | The catalog; printed dates, numbers and names; Marathi numbers and dates |
| `models.py`, `config.py`, `resources.py`, `files.py`, `console.py`, `errors.py` | Data models, settings, packaged files, whole-file writes, console output, user-facing errors |
