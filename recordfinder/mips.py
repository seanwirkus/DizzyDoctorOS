r"""MIPS 2026: the practice's quality measures, what meeting each one takes, and where the office stands.

Reads the office's own measure workbooks (config "mips_folder", default V:\MIPS 2024\MIPS\MIPS 2026 - the files
uploaded to MD Interactive), and matches their patients to the Outlook copy so everyone still missing something
shows up with their next visit, and as a reminder on that day's appointment. Nothing here writes to the workbooks.

The rules below come from the office's instructions (MIPS 2025\MIPS Instructions: "MIPS Information and
Instructions" and the "MIPS PA Instructions" per measure) and the office decisions made while completing 2026.
"""
from __future__ import annotations

import glob
import os
import re
from dataclasses import dataclass
from datetime import date, datetime

from .superbills import _fold

DEFAULT_FOLDER = r"V:\MIPS 2024\MIPS\MIPS 2026"
FOLDER = DEFAULT_FOLDER          # the app sets this from its settings at start
YEAR = 2026
DATA_COMPLETENESS = 0.75
CASE_MINIMUM = 20
EM_CODES = "99204, 99205, 99214 or 99215"

PROGRAM = [
    f"Performance year {YEAR}: only {YEAR} visits and {YEAR} documentation count. The one exception is #277, whose "
    "sleep study (AHI/RDI/REI) can be older.",
    f"Data completeness: report at least {DATA_COMPLETENESS:.0%} of each measure's eligible patients or visits, as Done "
    "or with a documented reason. Blank rows count against it.",
    f"A measure needs at least {CASE_MINIMUM} eligible cases to be scored. MD Interactive asks for 20 sleep-apnea "
    "patients for #277.",
    f"Eligible visits: E/M {EM_CODES}, with the measure's diagnosis in the top 4 on the superbill.",
    "Submitted in MD Interactive (Quality + Improvement Activities): one Excel upload per measure covers both "
    "physicians. Patel and Patel2 are both Dr. Patel.",
]


@dataclass(frozen=True)
class Measure:
    number: str
    title: str
    denominator: str
    met: str
    how: str                 # how the office documents and fills it in
    options: tuple
    action: str              # what to do at the patient's next visit
    chip: str                # the short tag on that appointment in the Today list
    every_visit: bool = False
    once_a_year: bool = True  # a later visit this year can still turn a "Not Done" into "Done"


MEASURES = (
    Measure("126", "Diabetes: foot & ankle care, peripheral neuropathy - neurological evaluation",
            "Age 18+ on the visit date, diabetes with neuropathy (E11.40 or E11.42 in the top 4 on the superbill). "
            "Not eligible: patients who can't have a lower-extremity neuro exam (e.g. bilateral amputees, dementia).",
            "A lower-extremity neurological exam (motor and sensory) documented at the visit; the note must say "
            "\"monofilament\".",
            f"First visit per patient and provider. Office rule: the exam must be at a {YEAR} visit (no {YEAR - 1} "
            "look-back); if it was at a later visit this year, use that visit's date. Only a "
            f"{YEAR - 1} exam = leave blank, \"needs a {YEAR} exam\".",
            ("Done", "Not Done - Medical Reason", "Not Done"),
            "Monofilament exam (lower-extremity motor + sensory) - write \"monofilament\"", "MIPS 126 monofilament"),
    Measure("268", "Epilepsy: counseling for women of childbearing potential",
            "Females 12-59 with epilepsy (G40.109, G40.209, G40.309, G40.909, G40.B19... in the top 4 on the superbill). "
            "Not eligible: unable to bear children (M1016).",
            "Counseled on how epilepsy and its treatment affect contraception and pregnancy: drug interactions with "
            "contraception, and anti-seizure medication effects on the fetus / pregnancy.",
            "EVERY visit is a row. Done = BOTH plan boxes checked: \"two forms of birth control\" AND "
            "\"indications / contraindications / adverse effects\". One box missing = Not Done.",
            ("Done", "Not Done"),
            "Check BOTH counseling boxes (birth control + adverse effects)", "MIPS 268 both boxes",
            every_visit=True, once_a_year=False),
    Measure("277", "Sleep apnea: severity assessment at initial diagnosis",
            "Age 18+ with an initial sleep-apnea diagnosis or first contact (G47.30 or G47.33 in the top 4 on the "
            "superbill). Not eligible: patients already billed with sleep apnea 2000-2025 (the history report in the "
            "MIPS 2026 folder).",
            "AHI, RDI or REI documented within 2 months of the initial evaluation. Note the study date: \"DE - m/d/yyyy\".",
            "First contact. An older study counts. HST only ordered = \"Not Done - Documented Reason\". Keep the study "
            "in the chart.",
            ("Done", "Not Done - Documented Reason", "Not Done"),
            "Document AHI / RDI / REI with the sleep-study date (within 2 months of the first visit)", "MIPS 277 AHI",
            once_a_year=False),
    Measure("291", "Parkinson's disease: cognitive impairment or dysfunction assessment",
            "All patients with Parkinson's disease (G20.A1, G20.A2, G20.B1, G20.B2, G20.C in the top 4 on the superbill).",
            f"Assessed once in {YEAR}: MoCA, SLUMS or MMSE (or a discussion with the patient / care partner, or a "
            "neuropsychology referral) dated this year.",
            "First visit per patient and provider. If the assessment was at a later visit this year, use that visit's date.",
            ("Done", "Patient or care partner decline assessment",
             "Patient is not able to participate in assessment and no knowledgeable informant available", "Not Done"),
            "MoCA / SLUMS / MMSE this year (or neuropsych referral)", "MIPS 291 MoCA"),
    Measure("293", "Parkinson's disease: rehabilitative therapy referral",
            "All patients with Parkinson's disease (G20.A1, G20.A2, G20.B1, G20.B2, G20.C in the top 4 on the superbill).",
            f"Referral to physical, occupational, speech or recreational therapy (incl. LSVT) once in {YEAR}.",
            "First visit per patient and provider. Done = the PT box or \"refer ... PT/OT\" in the note (PT/OT in "
            "capitals - \"Pt\" means patient). Also allowed: already in therapy, declined, not needed.",
            ("Done", "Patient or care partner decline referral", "Clinician determines patient does not require referral",
             "Patient already receiving physical, occupational, speech or recreational therapy", "Not Done"),
            "Refer to PT / OT / speech / LSVT (or note: already in therapy / declined)", "MIPS 293 therapy referral"),
)
BY_NUMBER = {m.number: m for m in MEASURES}
_REASONS = ("not done - medical reason", "not done - documented reason", "patient or care partner decline",
            "patient is not able", "clinician determines", "patient already receiving")


def classify(result) -> str:
    """met / reason (reported with a documented reason) / not met / pending (blank) / check (e.g. "Done?")."""
    r = " ".join(str(result or "").split()).lower()
    if not r:
        return "pending"
    if r == "done":
        return "met"
    if any(r.startswith(p) for p in _REASONS):
        return "reason"
    if r.startswith("not done"):
        return "not met"
    return "check"


def _day(v) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v or "").strip()
    m = re.match(r"(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})", s)
    if m:
        y = int(m.group(3))
        try:
            return date(y + 2000 if y < 100 else y, int(m.group(1)), int(m.group(2)))
        except ValueError:
            return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


@dataclass
class Entry:
    measure: str
    row: int
    provider: str
    pa: str
    note: str
    last: str
    first: str
    dob: date
    gender: str
    visit: date | None
    result: str
    state: str

    @property
    def key(self) -> tuple:
        return _fold(self.last.replace(" ", "")), self.dob


def workbook_for(folder: str, number: str) -> str | None:
    hits = [p for p in glob.glob(os.path.join(folder, f"*{number}_*.xlsx")) if not os.path.basename(p).startswith("~$")]
    return sorted(hits)[-1] if hits else None


_cache: dict = {}


def read(folder: str | None = None) -> dict[str, list[Entry]]:
    """Every patient row of each measure's workbook (re-read only when a workbook changed)."""
    import openpyxl
    folder = folder or FOLDER
    out: dict[str, list[Entry]] = {}
    for m in MEASURES:
        path = workbook_for(folder, m.number)
        if not path:
            out[m.number] = []
            continue
        stamp = (path, os.path.getmtime(path))
        if _cache.get(m.number, (None,))[0] == stamp:
            out[m.number] = _cache[m.number][1]
            continue
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        try:
            rows = list(wb[wb.sheetnames[0]].iter_rows(values_only=True))
        finally:
            wb.close()
        entries = []
        for i, r in enumerate(rows[1:], start=2):
            r = list(r) + [None] * 15
            last, first, dob = str(r[6] or "").strip(), str(r[7] or "").strip(), _day(r[9])
            if not last or not dob:
                continue                     # the office's note lines and blank lines
            result = " ".join(str(r[13] or "").split())
            entries.append(Entry(m.number, i, str(r[0] or "").strip(), str(r[4] or "").strip(), str(r[5] or "").strip(),
                                 last, first, dob, str(r[10] or "").strip(), _day(r[12]), result, classify(result)))
        _cache[m.number] = (stamp, entries)
        out[m.number] = entries
    return out


@dataclass
class Status:
    measure: Measure
    path: str | None
    rows: int = 0
    met: int = 0
    reason: int = 0
    not_met: int = 0
    pending: int = 0
    check: int = 0

    @property
    def reported(self) -> int:
        return self.met + self.reason + self.not_met

    @property
    def completeness(self) -> float | None:
        return self.reported / self.rows if self.rows else None

    @property
    def rate(self) -> float | None:
        """Performance rate: met / (met + not met); rows reported with a documented reason are left out."""
        d = self.met + self.not_met
        return self.met / d if d else None

    @property
    def problems(self) -> list[str]:
        out = []
        if not self.path:
            out.append("workbook not found")
        if self.path and self.rows < CASE_MINIMUM:
            out.append(f"only {self.rows} cases (needs {CASE_MINIMUM})")
        if self.completeness is not None and self.completeness < DATA_COMPLETENESS:
            out.append(f"completeness {self.completeness:.0%} (needs {DATA_COMPLETENESS:.0%})")
        if self.pending:
            out.append(f"{self.pending} blank")
        if self.check:
            out.append(f"{self.check} to check")
        return out


def status(folder: str | None = None) -> list[Status]:
    folder = folder or FOLDER
    data = read(folder)
    out = []
    for m in MEASURES:
        s = Status(m, workbook_for(folder, m.number))
        for e in data.get(m.number, []):
            s.rows += 1
            if e.state == "met":
                s.met += 1
            elif e.state == "reason":
                s.reason += 1
            elif e.state == "not met":
                s.not_met += 1
            elif e.state == "pending":
                s.pending += 1
            else:
                s.check += 1
        out.append(s)
    return out


def actionable(e: Entry) -> bool:
    """Still something to do this year: blank or unsure rows, and a "Not Done" on a once-a-year measure (a later
    visit this year that documents it can still make it Done)."""
    m = BY_NUMBER[e.measure]
    return e.state in ("pending", "check") or (e.state == "not met" and m.once_a_year)


@dataclass
class Todo:
    entry: Entry
    measure: Measure
    next_visit: datetime | None
    next_cal: str


def next_visits(store, keys: set) -> dict:
    """{(last_key, dob): (start, calendar)} - each patient's next booking from today on (no cancellations)."""
    out: dict = {}
    if not keys:
        return out
    now = datetime.now().isoformat(timespec="minutes")
    try:
        rows = store.reader().execute("""SELECT last_key, dob, start, cal, cats FROM appts WHERE start >= ?
                                         AND cal NOT IN ('Cancellations_No Show''s', 'Birthdays') ORDER BY start""",
                                      (now,)).fetchall()
    except Exception:
        return out
    wanted = {(lk, d.isoformat()) for lk, d in keys}
    for lk, dob, start, cal, cats in rows:
        k = (lk, dob)
        if k in wanted and k not in out and not re.search(r"no.?show|cancel", cats or "", re.I):
            out[k] = (datetime.fromisoformat(start), cal)
    return {(lk, date.fromisoformat(d)): v for (lk, d), v in out.items()}


def todo(store, folder: str | None = None) -> list[Todo]:
    """Everyone still missing something this year, with their next visit (soonest first; no visit booked last)."""
    data = read(folder)
    items = [e for m in MEASURES for e in data.get(m.number, []) if actionable(e)]
    nxt = next_visits(store, {e.key for e in items})
    out = [Todo(e, BY_NUMBER[e.measure], *(nxt.get(e.key) or (None, ""))) for e in items]
    return sorted(out, key=lambda t: (t.next_visit is None, t.next_visit or datetime.max, t.measure.number, t.entry.last))


def reminders(folder: str | None = None) -> dict:
    """{(last_key, dob): [Measure, ...]} for appointments: every open item, plus #268's counseling at every visit."""
    out: dict = {}
    try:
        data = read(folder)
    except Exception:
        return out                           # no workbooks on this PC / V: not reachable: no reminders
    for m in MEASURES:
        for e in data.get(m.number, []):
            if m.every_visit or actionable(e):
                lst = out.setdefault(e.key, [])
                if m not in lst:
                    lst.append(m)
    return out
