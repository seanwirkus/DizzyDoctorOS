"""Authorizations, read from the notes of a patient's Outlook appointments.

The front desk writes each authorization into the appointment's notes, in several styles:
    Authorization #234211 9/2/2026 - 3/1/2027          followed by code lines such as "99215 (1)"
    Authorization Number: 21841 7/17/26-1/13/27
    Auth 40121-09/11/26-03/10/27 *IC
    Authorized # 51234 valid from 7/23/2026 -1/19/27
Each appointment's notes are read from Outlook once and cached (read again only when the appointment changes).
Nothing is written to Outlook.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date

SCHEMA = """
CREATE TABLE IF NOT EXISTS auth_notes(entry_id TEXT PRIMARY KEY, modified TEXT, auths TEXT, notes TEXT);
"""
_DATE = r"(\d{1,2})/(\d{1,2})/(\d{2,4})"
_CODE_LINE = re.compile(r"^(\d{5}|[A-Z]\d{4})\s*\((\d+)\)")
_NUMBER = re.compile(r"(?i)auth\w*[^0-9\n]{0,45}?(\d{5,})")
_NOTE_WORDS = re.compile(r"(?i)denied|pending|runs out|expired|need \w+ auth|resubmit|no auth|auth required")


# Plans whose visits need a medical-group / HMO authorization (office rules: see the clinic playbook).
AUTH_PLANS = re.compile(r"SCMG|Scripp|Sharp|Rees|Graybill|Palomar|Harmony|\bHMO\b|Health ?Net|\bHnet\b|PHMG|Optum Care", re.I)
_GROUPS = re.compile(r"SCMG|Scripp|Sharp|Rees|Graybill|Palomar|Harmony|\bHMO\b|PHMG|Optum Care", re.I)


def needs_auth(primary: str, secondary: str = "") -> bool:
    """Does a visit on this plan need an authorization? Health Net behind Medicare is a secondary (no group auth)."""
    primary, secondary = primary or "", secondary or ""
    if re.search(r"\bCash\b|Self[- ]?Pay", primary, re.I):
        return False
    if AUTH_PLANS.search(primary):
        return bool(_GROUPS.search(primary)) or not re.search(r"\bMCR\b|Medicare", primary, re.I)
    return bool(re.search(r"SCMG|Scripp|Sharp", secondary, re.I))


@dataclass
class Auth:
    number: str
    start: date
    end: date
    codes: list = field(default_factory=list)   # "99215 (1)"
    source: str = ""                             # "09/24/26 Touraj" - the appointment it's written on
    line: str = ""

    def status(self, today: date | None = None) -> str:
        today = today or date.today()
        if today < self.start:
            return "Upcoming"
        if today > self.end:
            return "Expired"
        return "Active"

    def covers(self, day: date) -> bool:
        return self.start <= day <= self.end

    def key(self) -> tuple:
        return self.number, self.start, self.end


def _d(m) -> date:
    y = int(m[2])
    return date(y + 2000 if y < 100 else y, int(m[0]), int(m[1]))


def parse(body: str) -> tuple[list[Auth], list[str]]:
    """(authorizations, other auth notes such as "auth denied, resubmitted") from an appointment's notes."""
    auths, notes, cur = [], [], None
    for raw in (body or "").replace("\r", "\n").split("\n"):
        s = raw.strip()
        if not s:
            continue
        m = _CODE_LINE.match(s)
        if m and cur is not None:
            cur.codes.append(f"{m[1]} ({m[2]})")
            continue
        if re.search(r"auth", s, re.I):
            num = _NUMBER.search(s)
            dates = re.findall(_DATE, s)
            if num and len(dates) >= 2:
                try:
                    cur = Auth(num[1], _d(dates[0]), _d(dates[1]), [], "", s[:140])
                    auths.append(cur)
                    continue
                except ValueError:
                    pass
            if _NOTE_WORDS.search(s):
                notes.append(s[:140])
    return auths, notes


def ensure_schema(store) -> None:
    with store._lock:
        store.db.executescript(SCHEMA)
        store.db.commit()


def _appointments(store, last_key: str, first_key: str, dob: str, acct: str, since: str = "") -> list[tuple]:
    """(entry_id, calendar, start, modified) of the patient's bookings in the Outlook cache (from `since` on)."""
    db = store.reader()
    rows = []
    if last_key and dob:
        rows += db.execute("SELECT entry_id, cal, start, modified FROM appts WHERE last_key = ? AND dob = ?",
                           (last_key, dob)).fetchall()
    elif last_key and first_key:
        rows += db.execute("SELECT entry_id, cal, start, modified FROM appts WHERE last_key = ? AND first_key LIKE ?",
                           (last_key, first_key[:3] + "%")).fetchall()
    if acct:
        rows += db.execute("SELECT entry_id, cal, start, modified FROM appts WHERE ltrim(acct,'0') = ?",
                           (acct.lstrip("0"),)).fetchall()
    return sorted((r for r in set(rows) if r[2] >= since), key=lambda r: r[2])


def for_patient(store, last_key: str, first_key: str, dob: str, acct: str, progress=None,
                read_body=None, since: str = "") -> tuple[list[Auth], list[tuple[str, str]]]:
    """Every authorization on the patient's appointments (newest first) and the other auth notes, as
    [(appointment, note)]. Notes not read yet are fetched from Outlook (read_body is injectable for tests).
    since = "YYYY-MM-DD": only appointments from that day on (auths run about six months, so a worklist only needs
    the last year or so, which keeps a first check of a whole day quick)."""
    ensure_schema(store)
    appts = _appointments(store, last_key, first_key, dob, acct, since)
    db = store.reader()
    cached = {}
    for eid, _cal, _start, modified in appts:
        r = db.execute("SELECT modified, auths, notes FROM auth_notes WHERE entry_id = ?", (eid,)).fetchone()
        if r and r[0] == modified:
            cached[eid] = (json.loads(r[1]), json.loads(r[2]))
    todo = [a for a in appts if a[0] not in cached]
    if todo:
        if read_body is None:
            from . import schedule
            ns = schedule._outlook().GetNamespace("MAPI")
            read_body = lambda eid: ns.GetItemFromID(eid).Body or ""  # noqa: E731
        new = []
        for i, (eid, _cal, _start, modified) in enumerate(todo):
            if progress and i % 10 == 0:
                progress(f"Reading appointment notes for authorizations… {i + 1}/{len(todo)}")
            try:
                auths, notes = parse(read_body(eid))
            except Exception:
                continue  # deleted since the last sync, or Outlook busy: try again next time
            a = [[x.number, x.start.isoformat(), x.end.isoformat(), x.codes, x.line] for x in auths]
            cached[eid] = (a, notes)
            new.append((eid, modified, json.dumps(a), json.dumps(notes)))
        if new:
            with store._lock:
                store.db.executemany("INSERT OR REPLACE INTO auth_notes VALUES(?,?,?,?)", new)
                store.db.commit()
    out: dict[tuple, Auth] = {}
    notes_out = []
    for eid, cal, start, _m in appts:
        a_list, notes = cached.get(eid, ([], []))
        where = f"{start[5:7]}/{start[8:10]}/{start[2:4]} {cal}"
        for number, s, e, codes, line in a_list:
            a = Auth(number, date.fromisoformat(s), date.fromisoformat(e), list(codes), where, line)
            k = a.key()
            if k not in out:
                out[k] = a
            else:  # the same auth copied onto several visits: keep every code listed anywhere
                out[k].codes = list(dict.fromkeys(out[k].codes + a.codes))
        notes_out += [(where, n) for n in notes]
    return sorted(out.values(), key=lambda a: (a.end, a.start), reverse=True), notes_out
