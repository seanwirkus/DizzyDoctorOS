"""Eligibility: which payer portal each patient's plan is verified on, and the verifications on file.

A verification comes from one of two places:
  - someone marks it in the Today screen after checking the portal (right-click a visit), or
  - a portal run by the clinic's terminal Claude, which saves its results as JSON lines in
    ClaudeRoutines\\eligibility\\elig_*.jsonl; the app imports them like the Veradigm balances.
The app never logs in to a portal itself.
"""
from __future__ import annotations

import glob
import json
import os
import re
from dataclasses import dataclass
from datetime import date, datetime

from .superbills import _fold

SCHEMA = """
CREATE TABLE IF NOT EXISTS eligibility(
    last_key TEXT, dob TEXT, checked TEXT, status TEXT, plan TEXT, detail TEXT, by TEXT, source TEXT, acct TEXT,
    PRIMARY KEY(last_key, dob, checked, source));
"""
DEFAULT_FOLDER = r"C:\Users\admin\Documents\ClaudeRoutines\eligibility"
STATUSES = ("Active", "Inactive", "Problem")
FRESH_DAYS = 30   # a verification older than this is shown as due again

# (plan pattern, portal) - first match wins; the medical group before the health plan (same map as the office runbook)
PORTALS = [
    (r"\bCash\b|Self[- ]?Pay", "Cash pay - nothing to verify"),
    (r"SCMG|Sharp|Rees|Graybill|Palomar", "Sharp Link"),
    (r"Scripp", "Scripps Care Link"),
    (r"Tricare|TFL", "UHC Military West (Tricare)"),
    (r"Health ?Net|Hnet", "Health Net portal"),
    (r"Alignment", "Alignment AVA"),
    (r"\bUMR\b", "UMR portal"),
    (r"UHC|United|AARP|Optum", "UHC Provider portal"),
    (r"Cigna", "Cigna for HCP"),
    (r"GEHA", "GEHA portal"),
    (r"Anthem|\bBC\b|Blue|BCBS|\bBSCA\b|\bBS\b|Aetna|Humana|Meritain|Prime", "Availity"),
    (r"\bMCR\b|Medicare|Medi[-/ ]?Medi", "Noridian"),
    (r"PHCS|Multiplan|First Health|Beech|Galaxy|HealthSmart", "Payer on the card (PPO network)"),
]


@dataclass
class Check:
    checked: date
    status: str
    plan: str = ""
    detail: str = ""
    by: str = ""
    source: str = ""

    @property
    def fresh(self) -> bool:
        return (date.today() - self.checked).days <= FRESH_DAYS

    def label(self) -> str:
        return f"{self.status} {self.checked:%m/%d}" + (f" {self.by}" if self.by else "")


def portal_for(primary: str, secondary: str = "") -> str:
    """The portal to verify this plan on; an SCMG / Sharp / Scripps secondary means the medical group's portal."""
    primary, secondary = primary or "", secondary or ""
    group = re.search(r"SCMG|Scripp|Sharp", f"{primary} {secondary}", re.I)
    text = f"{primary} {secondary}" if group else primary
    return next((p for pat, p in PORTALS if re.search(pat, text, re.I)), "Unknown plan - check the card")


def ensure_schema(store) -> None:
    with store._lock:
        store.db.executescript(SCHEMA)
        store.db.commit()


def record(store, last_key: str, dob: str, status: str, plan: str = "", detail: str = "", by: str = "",
           source: str = "marked in DizzyDoctor", acct: str = "", when: date | None = None) -> None:
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    ensure_schema(store)
    with store._lock:
        store.db.execute("INSERT OR REPLACE INTO eligibility VALUES(?,?,?,?,?,?,?,?,?)",
                         (last_key, dob, (when or date.today()).isoformat(), status, plan, detail, by, source, acct))
        store.db.commit()


def latest(store, last_key: str, dob: str) -> Check | None:
    ensure_schema(store)
    r = store.reader().execute("""SELECT checked, status, plan, detail, by, source FROM eligibility
                                  WHERE last_key = ? AND dob = ? ORDER BY checked DESC, rowid DESC LIMIT 1""",
                               (last_key, dob)).fetchone()
    return Check(date.fromisoformat(r[0]), *r[1:]) if r else None


def import_folder(store, folder: str = DEFAULT_FOLDER) -> int:
    """Import portal-run results: one JSON object per line with last, first (optional), dob (MM/DD/YYYY),
    status (Active / Inactive / Problem), and optionally plan, detail, checked (MM/DD/YYYY), by, portal, acct."""
    ensure_schema(store)
    if not folder or not os.path.isdir(folder):
        return 0
    try:
        seen = json.loads(store.meta_get("eligibility_imported") or "{}")
    except ValueError:
        seen = {}
    rows = []
    for path in sorted(glob.glob(os.path.join(folder, "elig_*.jsonl")), key=os.path.getmtime):
        st = os.stat(path)
        stamp = f"{st.st_size}:{int(st.st_mtime)}"
        if seen.get(os.path.basename(path)) == stamp:
            continue
        with open(path, encoding="utf-8-sig") as f:
            for line in f:
                try:
                    d = json.loads(line) if line.strip().startswith("{") else None
                except ValueError:
                    d = None
                if not d:
                    continue
                dob, checked = _day(d.get("dob")), _day(d.get("checked")) or datetime.fromtimestamp(st.st_mtime).date()
                status = str(d.get("status", "")).strip().title()
                if not (d.get("last") and dob and status in STATUSES):
                    continue
                rows.append((_fold(str(d["last"]).replace(" ", "")), dob.isoformat(), checked.isoformat(), status,
                             str(d.get("plan", "")), str(d.get("detail", "")), str(d.get("by", "Claude")),
                             f"portal run: {d.get('portal', '')}".strip(": "), re.sub(r"\D", "", str(d.get("acct", "")))))
        seen[os.path.basename(path)] = stamp
    if rows:
        with store._lock:
            store.db.executemany("INSERT OR REPLACE INTO eligibility VALUES(?,?,?,?,?,?,?,?,?)", rows)
            store.db.commit()
    store.meta_set("eligibility_imported", json.dumps(seen))
    return len(rows)


def _day(s) -> date | None:
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(s or "").strip(), fmt).date()
        except ValueError:
            pass
    return None
