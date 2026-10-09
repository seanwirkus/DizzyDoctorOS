"""What each appointment needs before the patient is seen: the Today screen and the authorization worklist.

Built from the app's local copies - the Outlook cache, the patient map, the Veradigm balance snapshots and the
authorizations written in appointment notes - so it never changes anything in Outlook or Veradigm.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from . import auths, balances, eligibility, people
from .schedule import parse_subject

TESTING = {"VNG_VEMP", "EEG", "TY EMG/NCV", "MG EMG/NCV"}          # calendars for tests, not office visits
SKIP_CALENDARS = {"Cancellations_No Show's", "Birthdays"}
# Categories the office uses for money and forms; everything else in the categories describes the visit.
STATUS_CATEGORIES = {"self open-balance", "patient has credit", "insurance open balance", "abn form"}
TONE_ORDER = {"red": 0, "amber": 1, "blue": 2, "green": 3, "": 4}
AUTH_TONES = {"OK": "green", "Ends soon": "amber", "Codes not listed": "amber", "Test not on auth": "red",
              "No auth": "red", "Expired": "red", "Starts after visit": "red"}


@dataclass
class Row:
    entry_id: str
    start: datetime
    cal: str
    subject: str
    categories: list = field(default_factory=list)
    patient: bool = False              # a patient booking (has a DOB), not a block, rep lunch or note
    last: str = ""
    first: str = ""
    dob: date | None = None
    acct: str = ""
    ins: str = ""
    sec: str = ""
    last_key: str = ""
    first_key: str = ""
    auth_needed: bool = False
    auth: auths.Auth | None = None     # the authorization that covers this visit
    auth_status: str = ""              # OK / Ends soon / Codes not listed / Test not on auth / No auth / Expired / Starts after visit
    auth_detail: str = ""
    notes: list = field(default_factory=list)    # auth notes written on this appointment ("Need Chair Auth")
    balance: balances.Balance | None = None
    portal: str = ""                             # where to verify eligibility
    elig: eligibility.Check | None = None        # the latest verification on file
    mips: list = field(default_factory=list)     # mips.Measure items still open for this patient this year
    alerts: list = field(default_factory=list)   # (text, tone)
    end: datetime | None = None                  # cached Outlook end time (calendar layout)

    @property
    def name(self) -> str:
        return f"{self.last}, {self.first}".strip(", ") if self.patient else self.subject[:70]

    @property
    def visit(self) -> str:
        return ", ".join(c for c in self.categories if c.lower() not in STATUS_CATEGORIES)

    @property
    def plan(self) -> str:
        return self.ins + (f" / {self.sec}" if self.sec else "")

    @property
    def tone(self) -> str:
        return min((t for _x, t in self.alerts), key=lambda t: TONE_ORDER[t], default="")

    @property
    def needs_attention(self) -> bool:
        return self.tone in ("red", "amber")


def _rows(store, d0: date, d1: date, calendars=None) -> list[Row]:
    db = store.reader()
    got = db.execute("""SELECT entry_id, cal, start, end, subject, cats, last_key, first_key, dob, acct, ins, sec FROM appts
                        WHERE start >= ? AND start < ? ORDER BY start, cal""", (d0.isoformat(), d1.isoformat())).fetchall()
    out = []
    for eid, cal, start, end, subject, cats, lk, fk, dob, acct, ins, sec in got:
        if cal in SKIP_CALENDARS or (calendars and cal not in calendars):
            continue
        cat_list = [c.strip() for c in (cats or "").split(",") if c.strip()]
        if any(re.search(r"no.?show|cancel", c, re.I) for c in cat_list):
            continue
        r = Row(eid, datetime.fromisoformat(start), cal, " ".join((subject or "").split()), cat_list, patient=bool(dob),
                acct=(acct or "").lstrip("0"), ins=ins or "", sec=sec or "", last_key=lk or "", first_key=fk or "",
                end=datetime.fromisoformat(end) if end else None)
        if dob:
            info = parse_subject(subject or "")
            r.dob = date.fromisoformat(dob)
            r.last, r.first = info.get("last") or (lk or "").title(), info.get("first") or (fk or "").title()
        out.append(r)
    return out


def _outlook_reader():
    from . import schedule
    ns = schedule._outlook().GetNamespace("MAPI")
    return lambda eid: ns.GetItemFromID(eid).Body or ""


def _check_auth(r: Row, found: list, notes: list) -> None:
    visit = r.start.date()
    r.auth_needed = True
    tag = f"{visit:%m/%d/%y} {r.cal}"
    r.notes = [n for where, n in notes if where == tag]
    cover = [a for a in found if a.covers(visit)]
    if cover:
        a = max(cover, key=lambda x: x.end)
        r.auth = a
        codes = [c for x in cover for c in x.codes]
        if r.cal in TESTING and codes and all(c.startswith("99") for c in codes):
            r.auth_status, r.auth_detail = "Test not on auth", f"#{a.number} lists office-visit codes only"
        elif r.cal in TESTING and not codes:
            r.auth_status, r.auth_detail = "Codes not listed", f"#{a.number} - check the test is authorized"
        elif (a.end - visit).days <= 14:
            r.auth_status, r.auth_detail = "Ends soon", f"#{a.number} ends {a.end:%m/%d/%y} - renew for the next visit"
        else:
            r.auth_status, r.auth_detail = "OK", f"#{a.number} through {a.end:%m/%d/%y}"
    elif not found:
        r.auth_status, r.auth_detail = "No auth", "none written in any of this patient's appointments"
    elif any(a.start > visit for a in found):
        nxt = min((a for a in found if a.start > visit), key=lambda x: x.start)
        r.auth_status, r.auth_detail = "Starts after visit", f"#{nxt.number} starts {nxt.start:%m/%d/%y}"
    else:
        last = max(found, key=lambda x: x.end)
        r.auth_status, r.auth_detail = "Expired", f"#{last.number} ended {last.end:%m/%d/%y}"


def _alerts(r: Row) -> list[tuple[str, str]]:
    out = []
    if r.auth_needed:
        out.append(("Auth OK" if r.auth_status == "OK" else r.auth_status, AUTH_TONES[r.auth_status]))
    for n in r.notes:
        m = re.search(r"need \w+ auth(?:orization)?", n, re.I)
        if m:
            out.append((m.group(0).title(), "amber"))
    if r.elig and r.elig.fresh:
        out.append(("Elig OK" if r.elig.status == "Active" else f"Elig {r.elig.status}",
                     "green" if r.elig.status == "Active" else "red"))
    b = r.balance
    if b:
        if b.self > 0:
            out.append((f"Self ${b.self:,.2f}", "red" if b.self_over_90 > 0 else "amber"))
        elif b.credit:
            out.append((f"Credit ${-b.self:,.2f}", "green"))
        if b.self_over_90 > 0:
            out.append(("Over 90 days", "red"))
        if b.insur_over_90 > 0:
            out.append(("Ins over 90", "amber"))
    if "abn" in " ".join(r.categories).lower():
        out.append(("ABN form", "blue"))
    for m in r.mips:
        out.append((m.chip, "amber"))
    if r.patient and ("??" in r.subject or not r.ins):
        out.append(("Check header", "amber"))
    if re.search(r"\bSTAT\b", r.subject):
        out.append(("STAT", "red"))
    return out


def annotate(store, rows: list[Row], read_body=None, progress=None) -> list[Row]:
    """Fill in authorizations, balances and alerts. Each patient's appointment notes are read once (and cached)."""
    need = [r for r in rows if r.patient and auths.needs_auth(r.ins, r.sec)]
    patients: dict[tuple, tuple] = {}
    for i, r in enumerate(need):
        key = (r.last_key, r.dob)
        if key not in patients:
            if progress:
                progress(f"Checking authorizations… {i + 1} of {len(need)} visits")
            if read_body is None:
                read_body = _outlook_reader()
            since = (r.start.date() - timedelta(days=400)).isoformat()
            patients[key] = auths.for_patient(store, r.last_key, r.first_key, r.dob.isoformat(), r.acct,
                                              read_body=read_body, since=since)
        _check_auth(r, *patients[key])
    from . import mips
    open_mips = mips.reminders()                 # the MIPS workbooks: what each patient still needs this year
    for r in rows:
        if r.patient and r.acct:
            r.balance = balances.latest(store, r.acct)
        if r.patient:
            r.portal = eligibility.portal_for(r.ins, r.sec)
            r.elig = eligibility.latest(store, r.last_key, r.dob.isoformat())
            r.mips = open_mips.get((r.last_key, r.dob), [])
        r.alerts = _alerts(r)
    return rows


def refresh_alerts(r: Row) -> None:
    r.alerts = _alerts(r)


def day(store, when: date, calendars=None, read_body=None, progress=None) -> list[Row]:
    """Every appointment on one day (cancellations left out), with what each one needs."""
    try:
        eligibility.import_folder(store)   # portal runs saved since the last look
    except Exception:
        pass
    return annotate(store, _rows(store, when, when + timedelta(days=1), calendars), read_body, progress)


def auth_worklist(store, start: date, days: int = 14, calendars=None, read_body=None, progress=None) -> list[Row]:
    """Visits in [start, start + days) on plans that need a medical-group / HMO authorization."""
    rows = [r for r in _rows(store, start, start + timedelta(days=max(1, days)), calendars)
            if r.patient and auths.needs_auth(r.ins, r.sec)]
    return annotate(store, rows, read_body, progress)


def calendars(store, around: date | None = None) -> list[str]:
    """Calendars with bookings in the 60 days around a date, for the filter lists."""
    around = around or date.today()
    got = store.reader().execute("SELECT DISTINCT cal FROM appts WHERE start >= ? AND start < ?",
                                 ((around - timedelta(days=30)).isoformat(), (around + timedelta(days=30)).isoformat())).fetchall()
    return sorted(c for (c,) in got if c not in SKIP_CALENDARS)


def patient_id(store, r: Row) -> int | None:
    found = people.lookup(store, r.last, r.first, r.dob, r.acct) if r.patient else []
    return found[0].id if len(found) == 1 else None


def pull_list(rows: list[Row]) -> str:
    """The chart-pull list: every patient on the day, per calendar, in appointment order (for the tech)."""
    by_cal: dict[str, list[Row]] = {}
    for r in rows:
        if r.patient:
            by_cal.setdefault(r.cal, []).append(r)
    if not by_cal:
        return ""
    day_ = next(iter(by_cal.values()))[0].start
    out = [f"Charts to pull - {day_:%A %m/%d/%Y}", ""]
    for cal in sorted(by_cal):
        out.append(f"{cal} ({len(by_cal[cal])})")
        out += [f"  {r.start:%I:%M %p}  {r.name}   DOB {r.dob:%m/%d/%Y}" + (f"   Pt# {r.acct}" if r.acct else "")
                for r in by_cal[cal]]
        out.append("")
    return "\n".join(out).rstrip() + "\n"


_MEMBER_ID = re.compile(r"\bID\s*[:#]\s*([A-Za-z0-9\-]{4,})")


def eligibility_list(rows: list[Row]) -> str:
    """The day's patients grouped by the portal their plan is verified on, with plan and member ID."""
    by_portal: dict[str, list[Row]] = {}
    for r in rows:
        if r.patient:
            by_portal.setdefault(r.portal or "Unknown plan", []).append(r)
    if not by_portal:
        return ""
    day_ = next(iter(by_portal.values()))[0].start
    out = [f"Eligibility to verify - {day_:%A %m/%d/%Y}", ""]
    for portal in sorted(by_portal, key=lambda p: -len(by_portal[p])):
        out.append(f"{portal} ({len(by_portal[portal])})")
        for r in by_portal[portal]:
            m = _MEMBER_ID.search(r.subject)
            done = f"   [{r.elig.label()}]" if r.elig and r.elig.fresh else ""
            out.append(f"  {r.start:%I:%M %p}  {r.name:<28} DOB {r.dob:%m/%d/%Y}   {r.plan:<20}"
                       + (f" ID {m.group(1)}" if m else " ID ??") + done)
        out.append("")
    return "\n".join(out).rstrip() + "\n"
