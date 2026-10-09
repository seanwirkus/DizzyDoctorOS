"""Read a day's appointments from the provider calendars in Outlook (classic desktop Outlook).

Each provider has a calendar, and the front desk types appointment subjects from a template:
    "#EC Copay $10 Last, First 'Nick' DOB: 01/02/2003 Ph: ... Ins: PAYER ID: ... Sec: ... CC: ... Ref: ..."
This module only reads calendars; it never creates or changes anything in Outlook.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from .superbills import PatientQuery, _fold, name_in

# --------------------------------------------------------------------------- subject parsing

_NAME = re.compile(
    r"(?P<last>[A-Za-z][A-Za-z'.\-]*(?:\s+[A-Za-z][A-Za-z'.\-]*){0,3})\s*,\s*"
    r"(?P<first>[A-Z][A-Za-z'\-]+)(?:\s+(?P<mi>[A-Z])\.?(?=\s|$))?"
    r"(?:\s+[\"'(“‘](?P<nick>[^\"')”’]{2,20})[\"')”’])?")
_SUFFIX = re.compile(r"^(?:jr|sr|ii|iii|iv|md|do|d\.o|m\.d)\.?$", re.I)
# Front-desk markers typed before the name: provider/staff initials ("EC", "SS", "MET"), "Copay", "$10".
_MARKER = re.compile(r"^(?:[A-Z]{2,3}\d?|[Cc][Oo]-?[Pp][Aa][Yy]|\$\d+(?:\.\d\d)?|[Pp][Tt]|NP|FU)$")
_DOB = re.compile(r"\bDO[BE]\s*[:;#]?\s*(\d{1,2})/(\d{1,2})/(\d{2,4})", re.I)
_BARE_DATE = re.compile(r"(?<![\d/])(\d{1,2})/(\d{1,2})/(\d{4}|\d{2})(?![\d/])")
_ACCT = re.compile(r"\bAcc(?:t|ount)?\.?\s*#?\s*:?\s*(\d{4,7})\b|(?<=[A-Za-z)'\"])\s+(\d{5,7})\s+(?=DO[BE]\b)", re.I)
_COPAY = re.compile(r"co-?pay\s*\$\s*(\d+(?:\.\d\d)?)", re.I)
_INS = re.compile(r"\bIns(?:urance)?\b\s*[:;#]?\s*(.+?)(?=\s+(?:Sec(?:ondary)?\b|Cc\b|CC\b|Ref\b|Email\b|Fax\b)|\s+[\w.+\-]+@|$)")
_SEC = re.compile(r"\bSec(?:ondary)?\s*[:;#]?\s*(.+?)(?=\s+(?:Cc\b|CC\b|Ref\b|Email\b|Fax\b)|\s+[\w.+\-]+@|$)")
_CC = re.compile(r"\bCc\s*[:;]\s*(.+?)(?=\s+Ref\b|\s*\*|$)", re.I)
_REF = re.compile(r"\bRef\s*[:;]\s*(.+?)(?=\s+Fax\b|\s*\*|$)", re.I)


def _payer(raw: str) -> str:
    """'SCMG ID: 000111222' -> 'SCMG'; 'MCR: 1AA1AA1AA11' -> 'MCR'. Drops member IDs."""
    words = [w.strip(":;#,") for w in raw.split()]
    keep = []
    for w in words:
        if not w or w.upper() in ("ID", "ID#", "MEMBER") or re.search(r"\d", w) or w in ("??", "?"):
            if keep:
                break
            continue
        keep.append(w)
    return " ".join(keep)


def parse_subject(subject: str) -> dict:
    s = " ".join(subject.split())
    out: dict = {"last": "", "first": "", "nickname": "", "suffix": "", "dob": None, "account": "",
                 "insurance": "", "secondary": "", "copay": "", "complaint": "", "referral": ""}
    head = s[:s.find(" DOB")] if " DOB" in s else s[:120]
    name_end = 0
    for m in _NAME.finditer(head):
        name_end = m.end()
        tokens = m.group("last").split()
        # "EC King Jr." -> "King Jr.", "SS Anthony D.O." -> "Anthony D.O.", "INFUSION Hymes" -> "Hymes",
        # "ONLY KD Wang" -> "Wang": all-caps words in front of a normally written name are front-desk notes
        while len(tokens) > 1 and (_MARKER.match(tokens[0]) or
                                   (tokens[0].isupper() and any(not t.isupper() for t in tokens[1:]))):
            tokens.pop(0)
        suffix = []
        while len(tokens) > 1 and _SUFFIX.match(tokens[-1]):
            suffix.insert(0, tokens.pop())
        # keep the last "Last, First" before DOB: that's the patient
        out.update(last=" ".join(tokens), first=m.group("first"), nickname=m.group("nick") or "", suffix=" ".join(suffix))
    # "DOB: 01/02/2003", or a bare date typed right after the name/account ("Last, First Acc # 12345 01/02/1937 Ph:")
    m = _DOB.search(s) or (_BARE_DATE.search(s[name_end:name_end + 40]) if name_end else None)
    if m:
        y = int(m.group(3)) + (1900 if len(m.group(3)) == 2 and int(m.group(3)) > 30 else 2000 if len(m.group(3)) == 2 else 0)
        try:
            out["dob"] = date(y, int(m.group(1)), int(m.group(2)))
        except ValueError:
            pass
    if m := _ACCT.search(s):
        out["account"] = m.group(1) or m.group(2)
    if m := _COPAY.search(s):
        out["copay"] = m.group(1)
    if m := _INS.search(s):
        out["insurance"] = _payer(m.group(1))
    if not out["insurance"] and re.search(r"\bMedicare\b|\bMCR\b", s, re.I):
        out["insurance"] = "Medicare"
    if m := _SEC.search(s):
        out["secondary"] = _payer(m.group(1))
    if m := _CC.search(s):
        out["complaint"] = m.group(1).strip(" .")
    if m := _REF.search(s):
        out["referral"] = m.group(1).strip(" .")
    return out


# --------------------------------------------------------------------------- appointments

@dataclass
class Appointment:
    calendar: str
    start: datetime
    end: datetime
    subject: str
    categories: list[str] = field(default_factory=list)
    all_day: bool = False
    info: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.info:
            self.info = parse_subject(self.subject)

    @property
    def is_patient(self) -> bool:
        # Every patient booking carries a DOB; rep lunches and staff notes ("Lunch w/ Rep, Name") don't.
        return self.info.get("dob") is not None

    @property
    def seen(self) -> bool:
        return not any(re.search(r"no.?show|cancel", c, re.I) for c in self.categories)

    @property
    def medicare(self) -> bool:
        # Look at the whole subject: some bookings put "Medicare" before "Ins:" and the MBI after it.
        s = self.subject
        return bool(re.search(r"\bMCR\b|\bMedicare\b", s, re.I)) and not re.search(r"Medicare\s+Advantage", s, re.I)

    @property
    def when(self) -> str:
        return f"{self.start:%m/%d} {self.start:%I:%M %p}".replace(" 0", " ")

    def patient_name(self) -> str:
        i = self.info
        return f"{i.get('first', '')} {i.get('last', '')}".strip()


def score_appointment(a: Appointment, q: PatientQuery) -> tuple[int, list[str]]:
    i, score, why = a.info, 0, []
    if not a.is_patient:
        return 0, why
    last = i.get("last", "")
    if q.last and last and (_fold(q.last) == _fold(last) or _fold(q.last) in {_fold(t) for t in last.split()}
                            or name_in(last, q.last)):
        score += 50; why.append("last name")
        firsts = {_fold(i.get("first", "")), _fold(i.get("nickname", ""))} - {""}
        if q.first and _fold(q.first) in firsts:
            score += 20; why.append("first name")
    if q.dob and i.get("dob") == q.dob:
        score += 40; why.append("DOB")
    if q.account and i.get("account") and i["account"].lstrip("0") == q.account.lstrip("0"):
        score += 30; why.append("account #")
    return score, why


def find_patient(appts: list[Appointment], q: PatientQuery) -> list[tuple[int, Appointment, list[str]]]:
    hits = []
    for a in appts:
        s, why = score_appointment(a, q)
        if s >= 50:
            hits.append((s, a, why))
    return sorted(hits, key=lambda h: (-h[0], h[1].start))


def schedule_order(appts: list[Appointment], calendar: str) -> list[Appointment]:
    """Patients actually seen on one calendar in the order their superbills are scanned:
    by start time, and at the same start time the longer (new-patient) visit first."""
    return sorted((a for a in appts if a.calendar == calendar and a.is_patient and a.seen),
                  key=lambda a: (a.start, -(a.end - a.start).total_seconds()))


@dataclass
class DaySummary:
    calendar: str
    patients: int
    notes: list[str]


def summarize(appts: list[Appointment], calendar: str) -> DaySummary:
    mine = [a for a in appts if a.calendar == calendar]
    return DaySummary(calendar, sum(1 for a in mine if a.is_patient and a.seen),
                      [a.subject.strip() for a in mine if not a.is_patient and a.subject.strip()])


# --------------------------------------------------------------------------- Outlook (Windows only)

def _outlook():
    import pythoncom  # pywin32
    import win32com.client
    # pywin32 imports win32timezone lazily to turn Outlook's dates into Python datetimes. PyInstaller can't see
    # that import, so without this line the .exe fails on every date and the Outlook copy never fills.
    import win32timezone  # noqa: F401
    pythoncom.CoInitialize()  # every thread that talks to Outlook needs this
    try:
        return win32com.client.GetActiveObject("Outlook.Application")
    except Exception:
        return win32com.client.Dispatch("Outlook.Application")


def calendar_folders(ol) -> dict:
    """Calendars as listed in Outlook's Calendar pane (includes shared calendars)."""
    found: dict = {}
    try:
        ex = ol.ActiveExplorer()
        if ex is None and ol.Explorers.Count:
            ex = ol.Explorers.Item(1)
        if ex is not None:
            module = ex.NavigationPane.Modules.GetNavigationModule(1)  # olModuleCalendar
            for group in module.NavigationGroups:
                for nf in group.NavigationFolders:
                    try:
                        found.setdefault(nf.DisplayName, nf.Folder)
                    except Exception:
                        continue  # a shared calendar we can't open
    except Exception:
        pass
    if not found:  # Outlook is running without a window: fall back to the mailbox's own calendars
        cal = ol.GetNamespace("MAPI").GetDefaultFolder(9)  # olFolderCalendar
        found[cal.Name] = cal
        for sub in cal.Folders:
            found[sub.Name] = sub
    return found


def _naive(d) -> datetime:
    # pywin32 labels Outlook's local wall-clock times with a UTC tzinfo; keep the wall clock.
    return datetime(d.year, d.month, d.day, d.hour, d.minute, d.second)


def read_day(day: date, calendars: list[str] | None = None) -> list[Appointment]:
    ol = _outlook()
    start = datetime.combine(day, time())
    end = start + timedelta(days=1)
    flt = f"[Start] < '{end:%m/%d/%Y %I:%M %p}' AND [End] > '{start:%m/%d/%Y %I:%M %p}'"
    out: list[Appointment] = []
    for name, folder in calendar_folders(ol).items():
        if calendars and name not in calendars:
            continue
        try:
            items = folder.Items
            items.IncludeRecurrences = True
            items.Sort("[Start]")
            found = items.Restrict(flt)
            a = found.GetFirst()
            while a is not None:
                cats = [c.strip() for c in (a.Categories or "").split(",") if c.strip()]
                out.append(Appointment(name, _naive(a.Start), _naive(a.End), a.Subject or "", cats, bool(a.AllDayEvent)))
                a = found.GetNext()
        except Exception:
            continue  # unreadable calendar; the others still count
    return out


def outlook_unavailable_reason() -> str:
    try:
        import win32com.client  # noqa: F401
    except ImportError:
        return "pywin32 is not installed"
    return ""
