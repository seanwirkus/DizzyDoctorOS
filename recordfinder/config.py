"""User settings, stored as JSON in %APPDATA%\\DizzyDoctor\\config.json.

A config.json placed next to the .exe is used as the starting point on first run, so an
office can pre-configure the drive paths once and copy the same folder to every PC.
"""
from __future__ import annotations

import json
import os
import shutil
import sys

APP = "DizzyDoctor"
LEGACY_APPS = ("dizzdocOs", "ClinicRecordFinder")   # earlier names of the same folders, newest first

DEFAULTS = {
    "search_folders": [],            # e.g. ["S:\\Patients", "\\\\server\\scans"]
    "missing_folder": "",            # where billing looks for requested documents
    "per_patient_subfolder": False,  # copy into "<Last>, <First> <acct>" inside the Missing Folder
    "staff_names": [],               # our own people: their messages are replies, not requests
    "signature": "",                 # appended to reply text, e.g. "*AB"
    "reindex_hours": 12,
    "skip_folders": [],
    # Superbill workflow (see superbills.py / rules.py / amend.py)
    "superbill_root": "",            # scanned superbill batches, in "<Month YYYY>" folders
    "office_copy_folder": "",        # the office's own copy of what goes to billing (e.g. M:\\1 MPS)
    "billing_folder": "",            # where billing picks up amended superbills (e.g. a ShareFile folder);
                                     # {yyyy} {Month} {mm} are filled in from the date of service
    "initials": "",                  # stamped on amendments; falls back to the reply signature
    "roster": [],                    # providers: code, name, kind, calendar, column, aliases, codes
    "attending_rules": {},           # default / payer_excludes / present_categories
    "ai_read_chats": True,           # have Claude read pasted chats (once a key is saved)
    "ai_baa_confirmed": False,       # the practice's BAA with Anthropic is signed and HIPAA is enabled
    "ai_daily_limit": 5.0,           # USD of Claude use per day on this PC; 0 = no limit
    # Clinic-wide install (see shared.py): the server folder the app and the clinic data are published to
    "share_folder": "",              # e.g. "V:\\DizzyDoctor App"; set in the config.json next to the installed .exe
    "publish_data": False,           # true only on the main PC: it refreshes the data and publishes it for the others
    "publish_minutes": 60,           # at most this often
    "veradigm_reports_folder": "",   # Revenue Detail PDFs (default: Desktop\ALL on the main PC)
    "mips_folder": "",               # the MIPS measure workbooks (default: V:\MIPS 2024\MIPS\MIPS 2026)
    # Charting (see charting/): the progress note form, where drafts and finished notes go, local dictation
    "notes_folders": [],             # final notes, in year subfolders (default: V:\1111 Progress Notes)
    "prepped_notes_folder": "",      # drafts (default: the app's data folder, "Prepped Notes")
    "note_template": "",             # a newer blank form than the one built in, if the office revises it
    "dictation_model": "small.en",   # Whisper model run on this PC: tiny.en / base.en / small.en / medium.en
    "demo_mode": False,              # open the app on the made-up demo clinic instead of the clinic's data
}


def _dir(env: str, fallback: str, app: str = APP) -> str:
    base = os.environ.get(env) or os.path.join(os.path.expanduser("~"), fallback)
    return os.path.join(base, app)


def _adopt_legacy(env: str, fallback: str) -> str:
    """Move the previous product folder once, preserving every setting and indexed chart."""
    current = _dir(env, fallback)
    if os.path.exists(current):
        return current
    for name in LEGACY_APPS:
        legacy = _dir(env, fallback, name)
        if os.path.exists(legacy):
            try:
                os.makedirs(os.path.dirname(current), exist_ok=True)
                shutil.move(legacy, current)
            except OSError:
                # A locked profile should not make the app unlaunchable. The move is retried next time.
                return legacy
            break
    return current


CONFIG_DIR = _adopt_legacy("APPDATA", ".config")
DATA_DIR = _adopt_legacy("LOCALAPPDATA", ".local/share")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
DB_PATH = os.path.join(DATA_DIR, "index.db")


def asset(name: str) -> str:
    """A file from assets/ (the app icon): inside the built app, or in the source tree. "" when it isn't there."""
    for base in (getattr(sys, "_MEIPASS", ""), os.path.dirname(os.path.dirname(os.path.abspath(__file__)))):
        path = os.path.join(base, "assets", name) if base else ""
        if path and os.path.isfile(path):
            return path
    return ""


def _bundled_config() -> str:
    here = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(sys.argv[0]))
    return os.path.join(here, "config.json")


def load() -> dict:
    cfg = dict(DEFAULTS)
    for path in (_bundled_config(), CONFIG_PATH):
        try:
            with open(path, encoding="utf-8") as f:
                cfg.update(json.load(f))
        except (OSError, ValueError):
            pass
    return cfg


def save(cfg: dict) -> None:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
