# Arogya Vahi (आरोग्य वही)

Keeps a family's lab reports in one place and builds one timeline per test across labs,
using Gemma 4 running locally through Ollama. Reports never leave the laptop.

> **Status:** the extraction command works. It reads a report PDF and saves the test
> results to a local SQLite database. Every value is marked `needs_check` until the
> verification step is built.

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

## How it reads a report

- **Digital pages:** the text is rebuilt line by line, so a table row stays together
  (`HbA1c | 7.2 | % | 4.0 - 5.6`), and Gemma copies each value exactly as printed into a
  fixed JSON schema.
- **Scanned pages:** Gemma first transcribes the page image to text, then that text goes
  through the same step.
- **Checks in code, not in the model:** page numbers and dates (read day-first) come from
  code; a row whose printed name contradicts its test code (for example "Estimated Average
  Glucose" filed as fasting glucose) is dropped with a warning.
- On this laptop (RTX 3050, 4 GB) a digital page takes about 20–50 s and a scanned page
  about 45–60 s; the first page also loads the model.

## Tests

```powershell
.venv\Scripts\python -m pytest                    # fast tests, no Gemma needed
$env:AROGYA_LIVE = "1"; .venv\Scripts\python -m pytest tests/test_live_gemma.py -v
                                                  # calls the real local Gemma (~2 min)
```

## Privacy

`storage/`, `*.db`, PDFs and report photos outside `samples/` are gitignored. Never commit
real reports.
