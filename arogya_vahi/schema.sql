-- SQLite schema. CREATE ... IF NOT EXISTS, so it is safe to run on every connect.
CREATE TABLE IF NOT EXISTS patients (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    display_name  TEXT NOT NULL,
    aliases_json  TEXT NOT NULL DEFAULT '[]',  -- other names printed on their reports
    sex           TEXT CHECK (sex IN ('F', 'M')),
    birth_year    INTEGER                      -- estimated from a printed age, +/- 1 year
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
    is_scanned        INTEGER NOT NULL DEFAULT 0 CHECK (is_scanned IN (0, 1)),
    created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    patient_name_raw  TEXT,                 -- as printed; patient_id is who it was matched to
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
    value           REAL,                 -- the printed number, in the printed unit
    qualifier       TEXT,                 -- '<' when the lab printed '< 148'
    unit            TEXT,
    value_std       REAL,
    unit_std        TEXT,
    ref_low         REAL,
    ref_high        REAL,
    ref_text        TEXT,
    flag            TEXT,                 -- the lab's high/low mark as printed (H, L, ...)
    page            INTEGER NOT NULL,
    bbox_json       TEXT,
    ref_verified    INTEGER NOT NULL DEFAULT 0 CHECK (ref_verified IN (0, 1)),
    status          TEXT NOT NULL DEFAULT 'needs_check'
                    CHECK (status IN ('verified', 'needs_check', 'rejected')),
    check_notes     TEXT,
    reviewed        TEXT CHECK (reviewed IN ('verified', 'rejected'))  -- a person's decision; outranks status
);

-- Files waiting to be read, being read, or read: one worker reads them one at a time.
CREATE TABLE IF NOT EXISTS uploads (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    file_name   TEXT NOT NULL,                -- as sent
    sha256      TEXT NOT NULL,                -- of the bytes sent
    status      TEXT NOT NULL DEFAULT 'queued'
                CHECK (status IN ('queued', 'reading', 'saved', 'already_saved', 'failed')),
    message     TEXT,                         -- warnings, or why it failed
    report_id   INTEGER REFERENCES reports(id) ON DELETE SET NULL,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at  TEXT
);

CREATE INDEX IF NOT EXISTS idx_results_report ON results(report_id);
CREATE INDEX IF NOT EXISTS idx_results_test ON results(test_code);
CREATE INDEX IF NOT EXISTS idx_reports_patient ON reports(patient_id);
CREATE INDEX IF NOT EXISTS idx_uploads_status ON uploads(status);
