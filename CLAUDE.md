# Arogya Vahi (आरोग्य वही) — project brief for Claude Code

## Context
- Entry for the DEV "Hacktoberfest Weekend Challenge: Build for a Friend" (prompt: build something with open-source AI at its core that solves a real problem for someone you love).
- **Hard deadline: Mon 5 Oct 2026, 12:29 PM IST. Target publish: Mon 9:00 AM IST.**
- Judging: writing quality (heaviest), relevance (open-source AI at the core), creativity, technical execution, partner tech. Entering the **Gemma** featured category.
- Builder: solo, one weekend. Prefer simple, working, testable code over clever architecture.

## Problem statement
Indian families receive lab reports as PDFs on WhatsApp or Gmail, every few months, often from different labs. A new lab never has the previous reports. Parents managing diabetes, BP or thyroid can't easily read them in English, can't compare across labs, and walk into the doctor's office not knowing whether things are improving.

**Arogya Vahi** is an add-on to the apps they already use (WhatsApp, Gmail), not a new system. Share any lab report to it and it:
1. keeps every report (original PDF) in one place, regardless of lab or channel,
2. builds one verified timeline per test across labs,
3. says whether a change is **real or normal variation** (Reference Change Value),
4. explains in **Marathi, by voice**, what changed, and lists questions for the next doctor visit,
5. runs entirely on a home laptop with Gemma 4 — reports never go to any AI company or cloud.

Tagline: **"It never speaks a number it can't find in the report."**

Primary user: the builder's mother (Marathi speaker, Android phone). Assumptions to confirm: Android, laptop ~16 GB RAM.

## Non-negotiable rules
1. **The LLM never generates numbers.** All numbers in any output (text, voice, PDF) are inserted by code from verified DB values. Gemma writes phrasing with placeholders only; code fills them and re-checks.
2. **Every stored value must be verifiable** against the source PDF text (or flagged `needs_check`). Unverified values are shown as "please check", never spoken as fact.
3. **No medical advice.** Only: value vs that lab's printed reference range, "real change" vs "within normal variation", and "discuss with your doctor". No diagnosis, diet, or medication advice.
4. **Never commit real reports or the database.** `storage/` and `*.db` are gitignored. Ship synthetic sample reports in `samples/`.
5. **Never invent biological-variation constants.** Every CVi/CVa in `data/tests.csv` must have a source column (EFLM Biological Variation Database: https://biologicalvariation.eu/). Leave TODO if unknown.
6. Weekend scope. If a feature isn't in the MVP list, don't build it unless the MVP is done and tested.

## Stack
- Python 3.11+, FastAPI, Uvicorn
- Ollama + `gemma4:e4b` (fallback `gemma4:e2b` if too slow). Use Ollama's structured output (JSON schema via `format`).
- PyMuPDF (`pymupdf`) for PDF text + page rendering; scanned PDFs → page PNG → Gemma vision
- SQLite (stdlib `sqlite3`)
- Frontend: plain HTML/CSS/JS + Chart.js, served by FastAPI. No React, no build step.
- PWA: `manifest.json` + `sw.js` + `share_target` (POST multipart, accepts `application/pdf` and `image/*`)
- Voice: browser `speechSynthesis` with `mr-IN` (upgrade to an open Indic TTS only if time)
- Phone ↔ laptop: Tailscale `tailscale serve` (HTTPS, needed for PWA install/share target). Fallback: `cloudflared tunnel --url http://localhost:8000`
- iPhone fallback: iOS Shortcut in share sheet that POSTs the file to `/api/upload`

## Repo layout
```
arogya-vahi/
  CLAUDE.md
  README.md
  requirements.txt
  .gitignore            # storage/, *.db, .env, __pycache__/
  app/
    main.py             # FastAPI routes
    db.py               # schema + queries
    ingest.py           # upload, share target, bulk import, dedupe, patient matching
    extract.py          # PDF text / page images -> Gemma -> structured rows
    normalize.py        # test-name aliases, unit conversion
    verify.py           # value-in-source check, plausibility, cross-test consistency
    change.py           # RCV engine (same-lab and cross-lab)
    summary.py          # Marathi templated summary + doctor questions
    doctor_pdf.py       # one-page history PDF (stretch)
    gmail_import.py     # IMAP + app password backfill (stretch)
  web/
    index.html  app.js  styles.css  manifest.json  sw.js  icons/
  data/
    tests.csv           # code, display_en, display_mr, std_unit, CVi, CVa, between_lab_CV, source
    aliases.csv         # raw_name -> code (e.g. "Glycated Haemoglobin" -> HBA1C)
    units.csv           # code, from_unit, to_unit, factor/formula
  samples/              # synthetic lab reports (different layouts) for README + tests
  eval/
    run_eval.py         # extraction accuracy, change classification, chatbot comparison sheet
    ground_truth.csv
  storage/              # gitignored: originals/, arogya.db
```

## Data model (SQLite)
- `patients(id, display_name, aliases_json, sex, birth_year)`
- `reports(id, patient_id, lab_name, sample_date, report_date, source[whatsapp|gmail|upload|gmail_import], file_path, sha256, is_scanned, created_at)`
- `results(id, report_id, test_code, raw_name, raw_value_text, value, unit, value_std, unit_std, ref_low, ref_high, ref_text, page, bbox_json, status[verified|needs_check|rejected], check_notes)`

Dedupe: identical `sha256` → skip; same (patient, lab, sample_date) → treat as duplicate, ask.
Patient matching: compare name on report to patient aliases; if no confident match, ask "Is '<name>' the same person as <patient>?"
Timeline date = **sample collection date**, not report date.

## Pipeline
1. **Ingest** (`/api/upload`, `/share-target`, bulk multi-file upload) → save original to `storage/originals/<sha256>.pdf`.
2. **Extract**: PyMuPDF text per page. If little/no text → render page to PNG → Gemma vision. Gemma returns JSON: `{patient_name, age, sex, lab_name, sample_date, report_date, results:[{raw_name, value_text, unit, ref_text, page}]}`.
3. **Normalize**: `aliases.csv` maps raw_name → test_code (Gemma only for unknown names, then human confirms and alias is saved). Convert units to standard (`units.csv`). Parse ref ranges.
4. **Verify** (plain Python):
   - `value_text` must appear in that page's PDF text (normalize Devanagari digits, commas, spaces). Use PyMuPDF `search_for` to store bbox for highlighting.
   - Plausibility bounds per test (catch 69 vs 6.9).
   - Cross-test consistency: Friedewald `LDL ≈ TC − HDL − TG/5` (mg/dL, only if TG < 400); flag if lab LDL differs a lot. ADAG `eAG = 28.7 × HbA1c − 46.7` vs fasting glucose → only a "worth asking the doctor" note, never a verdict.
5. **Change engine** (`change.py`): `RCV% = √2 × 1.96 × √(CVa² + CVi²)`. If the two reports are from different labs, use `CVa_eff = √(CVa² + between_lab_CV²)`. Classify each consecutive pair: `real_increase | real_decrease | within_normal_variation`. Show the threshold used.
6. **Summary**: max 3 Marathi sentences + doctor questions, generated from rules over verified history (e.g. out of range in latest report; real change; same direction real change in 3 consecutive reports). Gemma may rephrase templates; code fills numbers and verifies output numbers match.
7. **UI**: big, simple, Marathi-first. Screens: (a) Add report (share/upload/bulk), (b) Confirm questions (unclear values, patient match, unknown test names), (c) Timeline per test (Chart.js, labels in words not just colour, tap point → open original PDF at that page), (d) "ऐका" listen button for summary.

## Unit conversions (to standard)
- Glucose mmol/L → mg/dL: × 18.016
- Total/LDL/HDL cholesterol mmol/L → mg/dL: × 38.67
- Triglycerides mmol/L → mg/dL: × 88.57
- Creatinine µmol/L → mg/dL: ÷ 88.4
- Vitamin D (25-OH) nmol/L → ng/mL: ÷ 2.496
- HbA1c IFCC mmol/mol → NGSP %: `0.0915 × IFCC + 2.15`

## MVP test list (~15)
HbA1c, fasting glucose, post-prandial glucose, TSH, free T4, total cholesterol, LDL, HDL, triglycerides, creatinine, haemoglobin, vitamin D (25-OH), vitamin B12, uric acid, urea.

## MVP vs stretch
MVP: ingest (upload + share target + bulk), extract, normalize, verify, RCV, timeline, Marathi summary + voice, tap-through to original PDF, eval script.
Stretch (only after MVP works end to end): Gmail IMAP backfill (app password, local only), doctor one-page PDF to share on WhatsApp, PDF highlight of the source value, open Indic TTS.
Out of scope: accounts/login, cloud sync, ABHA integration, diagnosis/advice, native app, fine-tuning (only if eval shows extraction failures on scanned reports).

## Evaluation (for the DEV post)
1. Extraction: every value in all real reports vs manually typed ground truth → accuracy, and count of wrong numbers that would have been spoken (target 0).
2. Change classification: consecutive pairs → our classification vs manual RCV calc.
3. Chatbot comparison: give ChatGPT/Gemini the same report pairs, ask "what changed?", record (a) changes it reports that RCV says are normal variation, (b) wrong numbers, (c) taps needed by Mom, (d) Marathi quality, (e) whether data left the house. Report honestly, whatever the result.
4. Latency per report on the laptop.

## Build order / checkpoints (IST)
- Fri: setup, Ollama + gemma4:e4b, `extract.py` CLI working on real reports → rows in SQLite.
- Sat AM: normalize, verify, change engine + unit tests.
- Sat PM: FastAPI + web UI + timeline + PWA share target + Marathi summary/voice.
- Sat night: eval run; stretch features only if MVP is solid.
- Sun AM: hand to Mom, record reaction (with permission). Sun PM: write the post.
- Mon by 9 AM: publish.

## DEV post outline
Title idea: "My mother has 3 years of blood reports in WhatsApp. I built an open-source AI that finally lets her see them — and never speaks a number it can't prove."
Sections: Mom's problem in her words → demo video → how it works (diagram) → the "real change or noise?" engine → eval results incl. chatbot comparison → why open source (medical data stays home, runs on a laptop, full control of the pipeline) → what Mom said → limitations.
