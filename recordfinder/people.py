"""Patient directory: every patient in the Outlook history, each linked to every file on the drive that names them.

Built on this PC from the two local caches (Outlook bookings in `appts`, drive file names in `files`) after a
drive scan or an Outlook sync. It reads only file and folder NAMES, never file contents, and nothing leaves the PC.

A file belongs to a patient when its name (or its folder's name) holds their last and first name in one of the
office's naming styles:
    "Last, First 09 24 26.pdf"        chair / EEG reports, records, faxes
    "LastFirst 09 24 26.pdf"          progress notes, EMG reports, prepped notes, provider folders
    "LastF 09 24 26.pdf"              older progress notes: last name + first initial
    "TY Last First Walmart Refill"    fax labels (provider tag first)
When several patients share a name, a DOB or account number in the path, or a visit date that only one of them
had, picks the patient; otherwise the file is linked to all of them and marked "same name".
"""
from __future__ import annotations

import os
import re
import sqlite3
import threading
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .matcher import date_variants
from .schedule import parse_subject
from .superbills import _fold

SCHEMA = """
CREATE TABLE IF NOT EXISTS patients(
    id INTEGER PRIMARY KEY, last TEXT, first TEXT, last_key TEXT, first_key TEXT, dob TEXT, acct TEXT,
    first_visit TEXT, last_visit TEXT, bookings INTEGER, files INTEGER);
CREATE INDEX IF NOT EXISTS patients_last ON patients(last_key);
CREATE INDEX IF NOT EXISTS patients_first ON patients(first_key);
CREATE INDEX IF NOT EXISTS patients_dob ON patients(dob);
CREATE INDEX IF NOT EXISTS patients_acct ON patients(acct);
CREATE TABLE IF NOT EXISTS file_links(path TEXT, patient_id INTEGER, how TEXT, PRIMARY KEY(path, patient_id));
CREATE INDEX IF NOT EXISTS file_links_patient ON file_links(patient_id);
"""

# Never linked to patients: staff files and sync/recycle debris.
SKIP_PARTS = ("\\hr\\", "\\applicants\\", "\\payroll\\", "\\.synologyworkingdirectory\\", "\\#recycle\\", "\\@eadir\\",
              "\\$recycle.bin\\")
_WORD = re.compile(r"[A-Za-z][A-Za-z'\-.]*")
_LAST_INITIAL = re.compile(r"^[A-Z][A-Za-z'\-]*[a-z][A-Z]$")        # "GarvidaR", "DeLeonR"
_FILE_DATE = re.compile(r"(?<!\d)(\d{1,2})[ ._-](\d{1,2})[ ._-](\d{2}|\d{4})(?!\d)")
_DIGITS = re.compile(r"(?<!\d)\d{4,7}(?!\d)")
_building = threading.Lock()


@dataclass
class Patient:
    id: int
    last: str
    first: str
    dob: date | None
    acct: str
    first_visit: date | None
    last_visit: date | None
    bookings: int
    files: int

    @property
    def name(self) -> str:
        return f"{self.last}, {self.first}".strip(", ")



def _base(path: str) -> str:
    """Last part of a path, for Windows paths too: the clinic data comes from the Windows PC, so on a Mac
    os.path.basename would return the whole "V:\\folder\\file" string."""
    return re.split(r"[\\/]", path.rstrip("\\/"))[-1]


def ensure_schema(store) -> None:
    with store._lock:
        store.db.executescript(SCHEMA)
        store.db.commit()


# --------------------------------------------------------------------------- building

def _patients_from_bookings(rows) -> tuple[list[tuple], dict, dict]:
    """Group bookings into patients by (last name, first 3 letters of the first name, DOB)."""
    groups: dict[tuple, dict] = {}
    for last_key, first_key, nick_key, dob, acct, start, subject in rows:
        if not last_key or not dob:
            continue  # rep lunches, blocks, placeholders
        g = groups.setdefault((last_key, first_key[:3], dob), {"firsts": Counter(), "nicks": set(), "accts": Counter(),
                                                               "lo": start, "hi": start, "n": 0, "subject": {},
                                                               "days": set()})
        g["firsts"][first_key] += 1
        g["subject"].setdefault(first_key, subject)
        if nick_key:
            g["nicks"].add(nick_key)
        if acct:
            g["accts"][acct.lstrip("0")] += 1
        g["lo"], g["hi"], g["n"] = min(g["lo"], start), max(g["hi"], start), g["n"] + 1
        g["days"].add(start[:10])
    patients, extra_keys, days = [], {}, {}
    for pid, ((last_key, _f3, dob), g) in enumerate(groups.items(), start=1):
        first_key = g["firsts"].most_common(1)[0][0]
        info = parse_subject(g["subject"][first_key])
        last = info["last"] or last_key.title()
        first = info["first"] or first_key.title()
        acct = g["accts"].most_common(1)[0][0] if g["accts"] else ""
        patients.append((pid, last, first, last_key, first_key, dob, acct, g["lo"][:10], g["hi"][:10], g["n"], 0))
        extra_keys[pid] = set(g["firsts"]) | g["nicks"]
        days[pid] = g["days"]
    return patients, extra_keys, days


def _name_keys(patients, extra_keys):
    """Lookup tables: folded "LASTFIRST" -> patient ids, and "LASTF" (first initial) -> patient ids."""
    full, initial = defaultdict(set), defaultdict(set)
    for p in patients:
        pid, last_key = p[0], p[3]
        if len(last_key) < 2:
            continue
        for fk in extra_keys.get(pid, {p[4]}):
            if len(fk) < 2:
                continue
            full[last_key + fk].add(pid)
            full[_fold(last_key + fk)].add(pid)     # "Kerr"+"Nancy" folds differently when read as one word
        if p[4]:
            initial[last_key + p[4][0]].add(pid)
    return full, initial


def candidates(stem: str) -> list[tuple[str, str]]:
    """(folded key, style) pairs that could name a patient, read from a file or folder name."""
    head = re.split(r"\d", stem, maxsplit=1)[0]
    words = _WORD.findall(head)[:7]
    folded = [_fold(w) for w in words]
    out = []
    for i in range(min(3, len(words))):
        for n in (4, 3, 2, 1):  # longest first: "Smith John" before a patient whose whole name reads "Smith"
            if i + n > len(words):
                continue
            key = "".join(folded[i:i + n])
            if n == 1 and len(key) < 6:
                continue  # a single short word ("Notes", "Refill") is never a whole name
            out.append((key, "name"))
            if n > 1:
                out.append((_fold("".join(words[i:i + n])), "name"))
    if words and _LAST_INITIAL.match(words[0]):
        out.append((folded[0][:-1], "name"))       # "SmithJohnA": middle initial on the end
        out.append((folded[0], "initial"))         # "SmithJ": last name + first initial
    if len(words) >= 2 and len(words[1]) == 1 and words[1].isupper():
        out.append((folded[0] + folded[1], "initial"))  # "Smith J 09 24 26"
    return out


# "LastFirst 09 24 26" / "Last, First 09 24 26": a person's name followed by a visit date
_PERSON_DATED = re.compile(r"^\s*([A-Z][A-Za-z'\-]*(?:[ ,]+[A-Za-z'\-]+){0,3}?)\s+\d{1,2}[ ._-]\d{1,2}[ ._-]\d{2,4}\b")


def _split_name(who: str) -> tuple[str, str]:
    """Display last/first for a name that only exists in file names: "Smith, John", "SmithJohn", "DeLeonJohn"."""
    if "," in who:
        last, first = who.split(",", 1)
        return last.strip(), first.strip().split(" ")[0]
    token = who.replace(" ", "")
    m = re.match(r"^(.*[a-z'])([A-Z][a-z]+)$", token)  # the last capitalised word is the first name
    return (m.group(1), m.group(2)) if m else (who.strip(), "")


def _file_day(name: str) -> str:
    """The date written in a file name ("SmithJohn 09 24 26.pdf" -> "2026-09-24"), or ""."""
    m = _FILE_DATE.search(name)
    if not m:
        return ""
    y = int(m.group(3))
    try:
        return date(y + 2000 if y < 100 else y, int(m.group(1)), int(m.group(2))).isoformat()
    except ValueError:
        return ""


def _pick(pids: set, path: str, info: dict) -> tuple[list[int], str]:
    """Narrow a same-name match with a DOB, account # or visit date in the path."""
    if len(pids) == 1:
        return list(pids), ""
    low = path.lower()
    by_dob = [p for p in pids if any(v in low for v in info[p]["dob_forms"])]
    if len(by_dob) == 1:
        return by_dob, " (DOB)"
    nums = set(_DIGITS.findall(path))
    by_acct = [p for p in pids if info[p]["acct"] and info[p]["acct"] in nums]
    if len(by_acct) == 1:
        return by_acct, " (acct#)"
    d = _file_day(_base(path))
    if d:
        same_day = [p for p in pids if d in info[p]["days"]]
        if len(same_day) == 1:
            return same_day, " (visit day)"
        in_range = [p for p in pids if info[p]["lo"] <= d <= info[p]["hi"]]
        if len(in_range) == 1:
            return in_range, " (visit dates)"
    return sorted(pids), " - same name"


def build(store, progress=None, cancel: threading.Event | None = None) -> tuple[int, int]:
    """Rebuild the directory and the file links. Returns (patients, links); (-1, -1) when cancelled."""
    if not _building.acquire(blocking=False):
        return -1, -1
    try:
        return _build(store, progress, cancel)
    finally:
        _building.release()


def _build(store, progress, cancel):
    from . import outlook_cache
    outlook_cache.ensure_schema(store)
    ensure_schema(store)
    db = store.reader()
    report = (lambda msg: progress(msg)) if progress else (lambda msg: None)
    report("Mapping patients: reading Outlook bookings…")
    rows = db.execute("SELECT last_key, first_key, nick_key, dob, acct, start, subject FROM appts").fetchall()
    patients, extra_keys, days = _patients_from_bookings(rows)
    full, initial = _name_keys(patients, extra_keys)
    info = {}
    for p in patients:
        dob = date.fromisoformat(p[5])
        info[p[0]] = {"dob_forms": [f.lower() for f in date_variants(dob)], "acct": p[6], "days": days[p[0]],
                      "lo": (date.fromisoformat(p[7]) - timedelta(days=45)).isoformat(),
                      "hi": (date.fromisoformat(p[8]) + timedelta(days=45)).isoformat()}

    links, counts, folder_cache, n, last = [], Counter(), {}, 0, time.monotonic()
    unlinked: list[tuple[str, str]] = []
    file_dates: dict[int, list[str]] = defaultdict(list)

    def match(stem: str, path: str) -> tuple[list[int], str]:
        for key, style in candidates(stem):
            pids = (full if style == "name" else initial).get(key)
            if pids:
                chosen, note = _pick(pids, path, info)
                return chosen, ("initial" if style == "initial" else "name") + note
        return [], ""

    for path, name, folder in db.execute("SELECT path, name, folder FROM files"):
        n += 1
        if cancel is not None and n % 5000 == 0 and cancel.is_set():
            return -1, -1
        if progress and time.monotonic() - last > 0.5:
            last = time.monotonic()
            report(f"Mapping patients: {n:,} files read, {len(links):,} linked…")
        low = path.lower()
        if any(s in low for s in SKIP_PARTS):
            continue
        pids, how = match(os.path.splitext(name)[0], path)
        if not pids:  # per-patient folders: "…\Smith, John\visit 1.pdf"
            if folder not in folder_cache:
                folder_cache[folder] = match(_base(folder), folder)
            pids, how = folder_cache[folder]
            how = "folder " + how if pids else how
        if not pids and _PERSON_DATED.match(os.path.splitext(name)[0]):
            unlinked.append((path, name))
        for pid in pids:
            links.append((path, pid, how))
            counts[pid] += 1

    # Second pass over dated notes that named nobody: a first name spelled differently from Outlook ("Jon"/"John",
    # "Kathy"/"Katherine") when exactly one patient with that last name fits, else a directory entry made from the
    # file names themselves (patients seen before the Outlook history starts, or never booked in Outlook).
    report("Mapping patients: notes with names not in Outlook…")
    lasts = defaultdict(list)
    for p in patients:
        lasts[p[3]].append(p)
    file_only: dict[str, int] = {}
    next_id = max((p[0] for p in patients), default=0) + 1
    for path, name in unlinked:
        who = _PERSON_DATED.match(os.path.splitext(name)[0]).group(1)
        token = _fold(who)
        day = _file_day(name)
        close = set()
        for k in range(2, len(token) - 1):
            first2 = token[k:k + 2]
            for p in lasts.get(token[:k], ()):
                if len(first2) == 2 and p[4].startswith(first2) and day and day in info[p[0]]["days"]:
                    close.add(p[0])
        if len(close) == 1:  # one patient with that last name, a similar first name, and a visit that very day
            pid = close.pop()
            links.append((path, pid, "close name (visit day)"))
            counts[pid] += 1
            continue
        if len(token) < 5:
            continue
        pid = file_only.get(token)
        if pid is None:
            last_name, first_name = _split_name(who)
            pid = file_only[token] = next_id
            next_id += 1
            patients.append((pid, last_name, first_name, _fold(last_name), _fold(first_name), "", "", "", "", 0, 0))
        links.append((path, pid, "name (files only)"))
        counts[pid] += 1
    # visit range for file-only patients: the dates in their file names
    for path, pid, how in links:
        if how == "name (files only)":
            d = _file_day(_base(path))
            if d:
                file_dates[pid].append(d)
    patients = [p if p[5] or not file_dates.get(p[0]) else p[:7] + (min(file_dates[p[0]]), max(file_dates[p[0]])) + p[9:]
                for p in patients]

    report("Mapping patients: saving…")
    patients = [p[:10] + (counts.get(p[0], 0),) for p in patients]
    # Its own connection, in chunks: the app's shared connection (and its lock) stays free the whole time, so
    # the window never waits on this. Only the final swap is a single short transaction.
    w = sqlite3.connect(store.path, timeout=60)
    try:
        w.executescript("""
            DROP TABLE IF EXISTS patients_new; DROP TABLE IF EXISTS file_links_new;
            CREATE TABLE patients_new(
                id INTEGER PRIMARY KEY, last TEXT, first TEXT, last_key TEXT, first_key TEXT, dob TEXT, acct TEXT,
                first_visit TEXT, last_visit TEXT, bookings INTEGER, files INTEGER);
            CREATE TABLE file_links_new(path TEXT, patient_id INTEGER, how TEXT, PRIMARY KEY(path, patient_id));
        """)
        w.executemany("INSERT INTO patients_new VALUES(?,?,?,?,?,?,?,?,?,?,?)", patients)
        w.commit()
        for i in range(0, len(links), 20000):
            w.executemany("INSERT OR IGNORE INTO file_links_new VALUES(?,?,?)", links[i:i + 20000])
            w.commit()
        w.executescript("""
            BEGIN IMMEDIATE;
            DROP TABLE IF EXISTS patients; DROP TABLE IF EXISTS file_links;
            ALTER TABLE patients_new RENAME TO patients; ALTER TABLE file_links_new RENAME TO file_links;
        """ + SCHEMA + "COMMIT;")
        w.execute("INSERT OR REPLACE INTO meta VALUES('patients_mapped_at', ?)", (datetime.now().isoformat(timespec="seconds"),))
        w.commit()
    finally:
        w.close()
    return len(patients), len(links)


# --------------------------------------------------------------------------- reading

COLS = "id, last, first, dob, acct, first_visit, last_visit, bookings, files"


def _patient(r) -> Patient:
    d = lambda s: date.fromisoformat(s) if s else None  # noqa: E731
    return Patient(r[0], r[1], r[2], d(r[3]), r[4], d(r[5]), d(r[6]), r[7], r[8])


def _prefix(col: str, key: str) -> tuple[str, list]:
    """Index-friendly "starts with" for the upper-case key columns."""
    return f"({col} >= ? AND {col} < ?)", [key, key[:-1] + chr(ord(key[-1]) + 1)]


def _dobs(s: str) -> list[date]:
    """Birth dates typed as 1/2/1950, 01-02-50 or 01.02.1950 (a 2-digit year is 19xx unless that's in the future)."""
    out = []
    for m in re.finditer(r"(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})", s):
        y = int(m.group(3))
        if y < 100:
            y += 2000 if 2000 + y <= date.today().year else 1900
        try:
            out.append(date(y, int(m.group(1)), int(m.group(2))))
        except ValueError:
            pass
    return out


def count(store) -> int:
    try:
        return store.reader().execute("SELECT COUNT(*) FROM patients").fetchone()[0]
    except Exception:
        return 0


def search(store, q: str, limit: int = 400) -> list[Patient]:
    """Patients matching what someone typed: a name ("smi", "smith john", "Smith, J"), a DOB or an account #."""
    ensure_schema(store)
    db = store.reader()
    q = q.strip()
    if not q:
        rows = db.execute(f"SELECT {COLS} FROM patients ORDER BY last_visit DESC LIMIT ?", (limit,)).fetchall()
        return [_patient(r) for r in rows]
    digits = re.sub(r"\D", "", q)
    dates = _dobs(q)
    where, args = [], []
    if dates:
        where.append("dob IN (" + ",".join("?" * len(dates)) + ")")
        args += [d.isoformat() for d in dates]
    elif digits and len(digits) >= 3 and not re.search(r"[A-Za-z]", q):
        where.append("ltrim(acct, '0') = ?")
        args.append(digits.lstrip("0"))
    else:
        parts = [p for p in re.split(r"[,\s]+", q) if p]
        keys = [_fold(p) for p in parts if _fold(p)]
        if "," in q and len(keys) >= 2:  # "Smith, Jo"
            a, b = _prefix("last_key", _fold("".join(re.split(r"\s+", q.split(",")[0].strip())))), _prefix("first_key", keys[-1])
            where.append(f"({a[0]} AND {b[0]})")
            args += a[1] + b[1]
        elif len(keys) >= 2:  # "smith john", "john smith" or a two-word last name
            a1, b1 = _prefix("last_key", keys[0]), _prefix("first_key", keys[1])
            a2, b2 = _prefix("last_key", keys[1]), _prefix("first_key", keys[0])
            c = _prefix("last_key", "".join(keys))
            where.append(f"(({a1[0]} AND {b1[0]}) OR ({a2[0]} AND {b2[0]}) OR {c[0]})")
            args += a1[1] + b1[1] + a2[1] + b2[1] + c[1]
        elif keys:
            a, b = _prefix("last_key", keys[0]), _prefix("first_key", keys[0])
            where.append(f"({a[0]} OR {b[0]})")
            args += a[1] + b[1]
    if not where:
        return []
    rows = db.execute(f"SELECT {COLS} FROM patients WHERE {' OR '.join(where)} "
                      f"ORDER BY last_key, first_key, last_visit DESC LIMIT ?", args + [limit]).fetchall()
    return [_patient(r) for r in rows]


def lookup(store, last: str, first: str = "", dob: date | None = None, account: str = "") -> list[Patient]:
    """Directory entries for a patient typed into the search boxes (account #, or name + optional DOB)."""
    ensure_schema(store)
    db = store.reader()
    if account and account.lstrip("0"):
        rows = db.execute(f"SELECT {COLS} FROM patients WHERE ltrim(acct,'0') = ?", (account.lstrip("0"),)).fetchall()
        if rows:
            return [_patient(r) for r in rows]
    lk = _fold(last.replace(" ", ""))
    if not lk:
        return []
    sql, args = f"SELECT {COLS} FROM patients WHERE last_key = ?", [lk]
    if first:
        sql += " AND first_key LIKE ?"
        args.append(_fold(first)[:3] + "%")
    if dob:
        sql += " AND dob = ?"
        args.append(dob.isoformat())
    return [_patient(r) for r in db.execute(sql, args).fetchall()]


_KINDS = [("progress notes", "Progress note"), ("prepped", "Prepped note"), ("emg", "EMG/NCV"), ("chair", "Chair report"),
          ("vemp", "VEMP"), ("vng", "VNG"), ("zeto", "EEG"), ("eeg", "EEG"), ("superbill", "Superbill"), ("new patient referrals", "Referral"),
          ("outgoing referrals", "Outgoing referral"), ("authoriz", "Auth"), ("referral coordinator", "Auth letter"),
          ("med recs request", "Records request"), ("other mrr", "Records request"), ("all records", "Records"),
          ("\\faxes\\", "Fax"), ("\\1 mps\\", "Billing (MPS)")]


def kind_of(path: str) -> str:
    """What a linked file is, from the folder it sits in."""
    low = path.lower()
    return next((label for key, label in _KINDS if key in low), "File")


def files_for(store, patient_id: int) -> list[tuple]:
    """(path, name, folder, size, mtime, how) of every file linked to the patient, newest first."""
    db = store.reader()
    return db.execute("""SELECT f.path, f.name, f.folder, f.size, f.mtime, l.how FROM file_links l
                         JOIN files f ON f.path = l.path WHERE l.patient_id = ? ORDER BY f.mtime DESC""",
                      (patient_id,)).fetchall()
