"""Progress notes on the clinic drive: find a patient's notes by date and read their form fields.

Notes are saved as fillable PDFs named "<LastFirst> mm dd yy.pdf" (final notes in the Progress
Notes folder, drafts in each provider's "Prepped Notes" folders). The fields that matter for
billing are read straight from the form: visit type, total time, the add-on statement
(G2211 "longitudinal" wording or a procedure-time line), procedure boxes, the attending
physician's attestation and the numbered impressions. Results are cached in index.db by path +
modified time.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import date

from .superbills import _fold

_NOTE_NAME = re.compile(r"^(?P<who>[A-Za-z][A-Za-z'.\-]+?)\s*(?P<m>\d{1,2})[ ._-](?P<d>\d{1,2})[ ._-](?P<y>\d{2,4})\b", re.I)
PROC_BOX = re.compile(r"proc|inject|block|trigger|cerumen|epley|crp|canal|botox|inspire|vng|emg|ncv", re.I)


@dataclass
class Note:
    path: str
    dos: date | None
    kind: str          # "final" or "prepped"

    @property
    def name(self) -> str:
        return os.path.basename(self.path)


@dataclass
class NoteFields:
    name: str = ""
    dob: str = ""
    date: str = ""
    visit: list[str] = field(default_factory=list)     # consult / eval / progress / urgent
    minutes: str = ""
    addl: str = ""
    attest: str = ""
    impressions: list[str] = field(default_factory=list)
    proc_boxes: list[str] = field(default_factory=list)
    plan: str = ""

    @property
    def new_patient(self) -> bool:
        return any(v in ("consult", "eval") for v in self.visit)

    @property
    def longitudinal(self) -> bool:
        return bool(re.search(r"longitudinal", self.addl, re.I))

    def attending(self, roster) -> str:
        """Roster code of the physician named in the attestation ("I, <name>, was present...")."""
        for p in roster:
            if p.is_physician and p.surname and re.search(rf"\b{re.escape(p.surname)}\b", self.attest, re.I):
                return p.code
        return ""


def parse_note_name(filename: str) -> tuple[str, date | None]:
    stem = os.path.splitext(os.path.basename(filename))[0]
    m = _NOTE_NAME.match(stem)
    if not m:
        return "", None
    y = int(m.group("y"))
    y = y + 2000 if y < 100 else y
    try:
        return m.group("who"), date(y, int(m.group("m")), int(m.group("d")))
    except ValueError:
        return m.group("who"), None


def matches_patient(who: str, last: str, first: str) -> bool:
    w, lk, fk = _fold(who), _fold(last.replace(" ", "").replace("-", "")), _fold(first)
    if not lk or not w.startswith(lk[:max(4, len(lk) - 1)]):
        return False
    rest = w[len(lk):] if w.startswith(lk) else w
    return not fk or rest.startswith(fk[:3]) or fk[:3] in w


def find_notes(store, cfg: dict, last: str, first: str = "") -> list[Note]:
    """All notes for a patient from the drive index, newest first (final notes before drafts)."""
    final = [os.path.normcase(p) for p in cfg.get("notes_folders") or [r"V:\1111 Progress Notes"]]
    prepped_words = ("prepped", "prep notes")
    rows = store.find_files([last.split()[-1] if last.split() else last], limit=20000) if last else []
    out = []
    for path, name, folder, _size, _mtime in rows:
        if not name.lower().endswith(".pdf"):
            continue
        who, dos = parse_note_name(name)
        if not who or not matches_patient(who, last, first):
            continue
        nf = os.path.normcase(folder)
        if any(nf.startswith(f) for f in final):
            out.append(Note(path, dos, "final"))
        elif any(w in nf for w in prepped_words):
            out.append(Note(path, dos, "prepped"))
    # Reconcile the configured note folders live as well as using the drive index.  This catches older year folders
    # added after the last index, and today's notes before the next scan.  Only directory entries and matching PDF
    # names are read here; form contents remain lazy until a note is opened.
    known = {os.path.normcase(n.path) for n in out}
    lk = _fold(last.replace(" ", "").replace("-", ""))[:4]
    for folder in _all_note_folders(cfg):
        try:
            names = os.listdir(folder)
        except OSError:
            continue
        for name in names:
            if not name.lower().endswith(".pdf") or not _fold(name).startswith(lk):
                continue
            who, dos = parse_note_name(name)
            path = os.path.join(folder, name)
            if who and matches_patient(who, last, first) and os.path.normcase(path) not in known:
                out.append(Note(path, dos, "final"))
    out.sort(key=lambda n: (n.dos or date.min, n.kind == "final"), reverse=True)
    return out


def _all_note_folders(cfg: dict) -> list[str]:
    """Configured progress-note roots plus every four-digit year folder below them."""
    out = []
    for root in cfg.get("notes_folders") or [r"V:\1111 Progress Notes"]:
        if not os.path.isdir(root):
            continue
        out.append(root)
        try:
            with os.scandir(root) as entries:
                out.extend(e.path for e in entries if e.is_dir(follow_symlinks=False) and re.fullmatch(r"20\d{2}", e.name))
        except OSError:
            continue
    return out


def _live_year_folders(cfg: dict, years) -> list[str]:
    """The year folders of the notes roots ("...\\Progress Notes\\2026"): listed live, since today's
    notes are filed after the last drive scan."""
    out = []
    for root in cfg.get("notes_folders") or [r"V:\1111 Progress Notes"]:
        for y in years:
            p = os.path.join(root, str(y))
            if os.path.isdir(p):
                out.append(p)
    return out


def notes_on(store, cfg: dict, dos: date) -> list[tuple[str, str]]:
    """(name part, path) of every final note filed for a date of service, e.g. ("SampleAlice", path)."""
    final = [os.path.normcase(p) for p in cfg.get("notes_folders") or [r"V:\1111 Progress Notes"]]
    found: dict[str, str] = {}
    for path, name, folder, _s, _m in store.find_files([f"{dos:%m %d %y}"], limit=5000):
        who, d = parse_note_name(name)
        if d == dos and any(os.path.normcase(folder).startswith(f) for f in final):
            found[os.path.normcase(path)] = who
    stamp = f"{dos:%m %d %y}"
    for folder in _live_year_folders(cfg, {dos.year}):
        try:
            names = [n for n in os.listdir(folder) if stamp in n and n.lower().endswith(".pdf")]
        except OSError:
            continue
        for n in names:
            who, d = parse_note_name(n)
            if d == dos:
                found.setdefault(os.path.normcase(os.path.join(folder, n)), who)
    return [(who, path) for path, who in found.items()]


def note_for_visit(notes: list[Note], dos: date) -> Note | None:
    same = [n for n in notes if n.dos == dos]
    return next((n for n in same if n.kind == "final"), same[0] if same else None)


def ensure_schema(store) -> None:
    with store._lock:
        store.db.execute("CREATE TABLE IF NOT EXISTS note_cache(path TEXT PRIMARY KEY, mtime REAL, data TEXT)")
        store.db.commit()


def read_fields(path: str) -> NoteFields:
    import pymupdf
    checked, fields = [], {}
    with pymupdf.open(path) as doc:
        for page in doc:
            for w in page.widgets():
                name, val = w.field_name or "", w.field_value
                if w.field_type_string in ("CheckBox", "RadioButton"):
                    if val in (True, "Yes", "On") or (isinstance(val, str) and val not in ("Off", "", "No")):
                        checked.append(name)
                elif val not in (None, "", " "):
                    fields.setdefault(name, str(val))
    impressions = re.findall(r"(?m)^\s*(\d{1,2})\)\s*([^\r\n]{2,90})", fields.get("impression-plan-descr", "").replace("\r", "\n"))
    attest = next((v for v in fields.values() if re.search(r"was present|established the (?:care )?plan", v, re.I)), "")
    return NoteFields(
        name=fields.get("Name", ""), dob=fields.get("DOB", ""), date=fields.get("Date", ""),
        visit=[c.replace("neuro-", "") for c in checked if c.startswith("neuro-")],
        minutes=fields.get("appt-time", ""), addl=fields.get("addnl-time", ""), attest=attest,
        impressions=[f"{n}) {t.split(' - ')[0].strip()}" for n, t in impressions],
        proc_boxes=[c for c in checked if PROC_BOX.search(c)], plan=fields.get("plan-descr", "").replace("\r", " | "))


def fields(store, path: str) -> NoteFields | None:
    """Form fields of a note, cached by modified time. None when the file isn't a fillable note."""
    ensure_schema(store)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    with store._lock:
        row = store.db.execute("SELECT mtime, data FROM note_cache WHERE path=?", (path,)).fetchone()
    if row and abs(row[0] - mtime) < 1:
        return NoteFields(**json.loads(row[1]))
    try:
        nf = read_fields(path)
    except Exception:
        return None
    with store._lock:
        store.db.execute("INSERT OR REPLACE INTO note_cache VALUES(?,?,?)", (path, mtime, json.dumps(nf.__dict__)))
        store.db.commit()
    return nf
