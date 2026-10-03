"""The commands as a user runs them: `python -m app.<module>` in a fresh process.

Run this way a module is named __main__, not app.<module>, so these catch problems
(like its log lines going missing) that tests importing the module cannot.
"""

import os
import subprocess
import sys

from app.config import ROOT


def run_command(*args, storage):
    env = {**os.environ, "AROGYA_STORAGE_DIR": str(storage), "PYTHONIOENCODING": "utf-8"}
    return subprocess.run(
        [sys.executable, "-m", *args], cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8"
    )


def test_extract_command_reports_a_missing_file_in_one_line(tmp_path):
    missing = tmp_path / "missing.pdf"
    finished = run_command("app.extract", str(missing), storage=tmp_path / "storage")
    assert finished.returncode == 1
    assert finished.stdout == ""
    assert finished.stderr.strip() == f"error: File not found: {missing}"


def test_normalize_command_says_what_it_did(tmp_path):
    finished = run_command("app.normalize", storage=tmp_path / "storage")
    assert finished.returncode == 0
    assert finished.stderr.strip() == "Re-normalised 0 saved results."
