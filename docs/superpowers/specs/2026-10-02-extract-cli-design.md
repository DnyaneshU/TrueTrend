# Extract CLI — design (Friday checkpoint)

**Goal:** `python -m app.extract path/to/report.pdf` reads a lab report PDF, asks local
Gemma (`gemma4:e4b` via Ollama) for the MVP test rows, prints them as JSON and saves them
to SQLite. Stop there so the builder can test it on real reports.

**In scope today:** repo skeleton, `requirements.txt`, `.gitignore`, short `README.md`,
SQLite schema (`app/db.py`), `app/extract.py`, quick tests.
**Not today:** normalize, verify, RCV, web UI, patient matching (all Saturday or later).
Every saved row is `status = needs_check` with `check_notes = 'not verified yet'`.

## Decisions

| Topic | Decision |
|---|---|
| Granularity | One Gemma call per page (approach A). The code assigns page numbers, not the model. |
| Digital pages | PyMuPDF words are regrouped into visual rows: words whose vertical centres are within half a text height form one line, sorted left→right; a horizontal gap wider than 0.6 × the text height becomes ` \| ` (measured: word spaces ≈ 0.2 ×, table column gaps > 3 ×). So a table row reads `HbA1c \| 6.8 \| % \| 4.0 - 5.6`. |
| Scanned pages | Fewer than 50 visible characters of text, **or** fewer than 200 while images cover at least half the page (a scan with a typed header/footer) → render the page to PNG at 150 DPI → same call with the image (Gemma vision). A text page printed over a full-page letterhead image stays text. Thresholds to be tuned on real reports. |
| Ligatures | Word extraction expands typographic ligatures (`ﬁ` → `fi`), so names like "Lipid Profile" match later. |
| Which tests | Only the 15 MVP tests. The prompt lists them with common alternate names and explicit exclusions: Total T4 (only Free T4), LDL/HDL ratio, VLDL, urine glucose/creatinine, random blood sugar, BUN, and Hb vs HbA1c confusion. Switching to "every test" later is a prompt/schema change. |
| Test codes | `HBA1C, GLU_F, GLU_PP, TSH, FT4, CHOL, LDL, HDL, TG, CREAT, HB, VITD, B12, URIC, UREA` — an enum in the JSON schema, so Gemma can only answer with these. Saved to `results.test_code` as Gemma's suggestion; normalize.py confirms it on Saturday. |
| Name check | Code checks each row's printed name against its test code (`NAME_RULES`: per code, a pattern the name must match and one it must not). A row that fails is dropped with a warning in phase 5; Gemma's full reply stays in `raw_json`. Added after a live run where Gemma vision filed "Estimated Average Glucose" as GLU_F and "Total T4" as FT4. |
| Copy, don't compute | Gemma returns name, value, unit, reference range and dates **exactly as printed**. No numeric parsing today (`value`, `ref_low`, … stay NULL for normalize.py). |
| Dates | Code parses the printed text day-first (DD/MM) with `python-dateutil` into ISO `YYYY-MM-DD`; text already in `YYYY-MM-DD` form is read year-first (day-first parsing would turn `2026-09-12` into 9 Dec). Partial or unparseable dates → NULL + warning. `sample_date` = collection date. |
| Ollama call | Official `ollama` package. `format` = JSON schema from a Pydantic model; reply validated with the same model. Options: `think=False`, `temperature=0`, `num_ctx=8192`, `num_predict=2048`. The prompt asks for compact single-line JSON (measured: 30 s vs 48 s for the same page). The one retry uses `temperature=0.3`, since repeating an identical temperature-0 call would usually give the same broken answer. |
| Measured baseline | Synthetic page, 5 MVP tests + 4 look-alikes: all 5 correct, all 4 excluded; ~31 s per page warm, ~30 s extra for the first model load. |
| Merging pages | Header fields (patient name/age/sex, lab, dates): first non-null value in page order; a later page with a different non-null value adds a warning. Results: concatenated in page order. |

**Per-page JSON schema (Pydantic):**
`{patient_name, age, sex, lab_name, sample_date, report_date: str|null,
results: [{test_code: enum, raw_name: str, value_text: str, unit: str|null, ref_text: str|null}]}`
All keys required (nullable where shown), header first so it is generated before results.

## Storage

- Original copied to `storage/originals/<sha256>.pdf`; database at `storage/arogya.db`. Both gitignored.
- Tables exactly as in CLAUDE.md (`patients`, `reports`, `results`), with CHECK constraints on
  `source` and `status`, `UNIQUE(reports.sha256)`, `results.report_id → reports.id ON DELETE CASCADE`,
  foreign keys switched on per connection.
- Extra columns on `reports`: `patient_name_raw`, `patient_age_raw`, `patient_sex_raw`
  (`patient_id` stays NULL until matching exists), `extract_model`, `extract_seconds`
  (latency number for the post), `raw_json` (merged Gemma output, for debugging and eval).
- `source = 'upload'` for the CLI; `is_scanned = 1` if any page used vision.
- All Gemma calls finish before any DB write; report + results are inserted in one transaction.

## CLI behaviour

```
python -m app.extract report.pdf [--force] [--model gemma4:e2b]
```
- stdout: the final JSON only (`> out.json` works). stderr: progress, e.g.
  `page 2/3 · text · 4 results · 21.3 s`, and warnings.
- Output JSON: `report_id, file, sha256, model, seconds, patient_name, age, sex, lab_name,
  sample_date, sample_date_text, report_date, report_date_text,
  pages[{page, mode: text|vision|failed, results, seconds}],
  warnings[], results[{page, test_code, raw_name, value_text, unit, ref_text, status}]`.
- Same file again (same sha256) → checked **before** calling Gemma:
  `already extracted as report #3 — use --force to redo`, exit 0, nothing printed to stdout.
- `--force` re-extracts; the old report (results cascade) is deleted in the **same transaction**
  that inserts the new one, so a failed re-run keeps the old data.

## Errors and partial failures

Fatal — one clear sentence on stderr, exit 1, nothing saved:
- File missing / not a PDF; password-protected PDF (“open it once and save a copy without a password”).
- Ollama not reachable (“is Ollama running?”), at the start or mid-report; model not pulled
  (“run: ollama pull <model>”).
- Every page failed validation.

Non-fatal — warning on stderr and in the JSON, report saved, exit 0:
- A page whose reply fails schema validation is retried once; if it fails again that page is
  skipped (`mode: failed`).
- No MVP tests found on any page (report saved with zero results, so a re-run is skipped
  unless `--force`).
- Unparseable or missing sample date; pages disagreeing on a header field; the same test
  appearing on more than one page (all occurrences are kept, each with its page).

## Testing (pytest, no Gemma needed)

- Row rebuilding on a small PDF generated inside the test with PyMuPDF.
- Date parsing: `14/09/2026`, `02-Oct-2026 10:15 AM`, partial and garbage input.
- DB: schema creation, save, duplicate detection, `--force` replace with cascade.
- Pipeline with a fake Gemma function injected: page numbers come from code, header merge and
  conflict warning, empty page → vision mode, failed page → retry then skip.
- Real check: the builder runs the CLI on real reports and reads the JSON.

## Files

```
requirements.txt   # exact pins: pymupdf, ollama, pydantic, python-dateutil, httpx, pytest
.gitignore         # storage/, *.db, .env, __pycache__/, .venv/, and *.pdf outside samples/
pytest.ini         # pythonpath = . so `pytest` finds the app package
README.md          # setup + the one command
app/__init__.py
app/errors.py      # ExtractError: a problem the user can fix
app/db.py          # schema, connect(), find_report_id(), save_report()
app/pages.py       # PDF -> PageInput per page (rebuilt text, or PNG for scans)
app/dates.py       # parse_date(): printed date -> ISO, day-first
app/lab_tests.py   # the 15 MVP tests in one catalog: prompt wording + name rules
app/gemma.py       # schema, prompt, ask_gemma(): one Ollama call per page
app/extract.py     # merge pages into one report; CLI entry point (phase 6)
tests/             # conftest.py (synthetic PDF builder), test_db.py, test_extract_*.py
                   # (pdf, dates, gemma, pipeline, run), test_live_gemma.py (real Gemma,
                   # runs only with AROGYA_LIVE=1)
```
`*.pdf` is ignored outside `samples/` so a real report dropped into the repo for testing can't be committed by accident.
`web/`, `data/`, `eval/`, `samples/` are created when their part is built.
