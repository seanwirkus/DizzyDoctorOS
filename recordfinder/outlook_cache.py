"""Local copy of the Outlook calendars, so any patient's full booking history is instant.

A full sync reads every calendar in a date window once (read-only; about 5 minutes for
~120k appointments) and stores each item with its parsed patient fields in index.db.
Later syncs only fetch items changed since the last one. Nothing is ever written to Outlook.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from .schedule import Appointment, parse_subject
from .superbills import _fold

SCHEMA = """
CREATE TABLE IF NOT EXISTS appts(
    entry_id TEXT PRIMARY KEY, cal TEXT, start TEXT, end TEXT, subject TEXT, cats TEXT,
    last_key TEXT, first_key TEXT, nick_key TEXT, dob TEXT, acct TEXT, ins TEXT, sec TEXT, modified TEXT);
CREATE INDEX IF NOT EXISTS appts_last ON appts(last_key);
CREATE INDEX IF NOT EXISTS appts_start ON appts(start);
CREATE INDEX IF NOT EXISTS appts_dob ON appts(dob);
"""
FMT = "%m/%d/%Y %I:%M %p"
HISTORY_START = date(2000, 1, 1)
log = logging.getLogger("recordfinder")


def ensure_schema(store) -> None:
    with store._lock:
        store.db.executescript(SCHEMA)
        store.db.commit()


def _keys(info: dict) -> tuple[str, str, str]:
    last = " ".join(t for t in info.get("last", "").split())
    return _fold(last.replace(" ", "")), _fold(info.get("first", "")), _fold(info.get("nickname", ""))


def _row(cal: str, item, naive) -> tuple:
    subject = item.Subject or ""
    info = parse_subject(subject)
    lk, fk, nk = _keys(info)
    return (item.EntryID, cal, naive(item.Start).isoformat(timespec="minutes"), naive(item.End).isoformat(timespec="minutes"),
            subject, item.Categories or "", lk, fk, nk, info["dob"].isoformat() if info.get("dob") else "",
            info.get("account", ""), info.get("insurance", ""), info.get("secondary", ""),
            naive(item.LastModificationTime).isoformat(timespec="seconds"))


def sync(store, start: date | None = None, end: date | None = None, full: bool = False, progress=None,
         cancel: threading.Event | None = None) -> int:
    """Copy calendar items into the cache. Incremental unless full=True or never synced. Returns items stored."""
    from . import schedule
    ensure_schema(store)
    # The whole history, so every patient the office has ever booked is in the directory. The window only
    # changes once a year, so day-to-day syncs stay incremental (a window that moved daily forced full reads).
    start = start or HISTORY_START
    end = end or date(date.today().year + 2, 1, 1)
    last = store.meta_get("outlook_synced_at")
    window = f"{start.isoformat()}..{end.isoformat()}"
    incremental = bool(last) and not full and store.meta_get("outlook_window") == window
    ol = schedule._outlook()
    flt = f"[Start] >= '{datetime.combine(start, datetime.min.time()):{FMT}}' AND [Start] < '{datetime.combine(end, datetime.min.time()):{FMT}}'"
    if incremental:
        since = datetime.fromisoformat(last) - timedelta(minutes=5)
        flt += f" AND [LastModificationTime] >= '{since:{FMT}}'"
    began = datetime.now()
    rows, n = [], 0

    # A full read fills a new table and swaps it in at the end, so a read that fails or is stopped halfway (the
    # app closed, Outlook busy) leaves the previous copy in place instead of an empty one.
    table = "appts" if incremental else "appts_new"

    def flush_big():
        nonlocal n
        if len(rows) >= 2000:
            n += _flush(store, rows, table)

    folders = [(k, v) for k, v in schedule.calendar_folders(ol).items() if k != "Birthdays"]
    if not incremental:
        with store._lock:
            store.db.execute("DROP TABLE IF EXISTS appts_new")
            store.db.execute(SCHEMA.split(";")[0].replace("IF NOT EXISTS appts(", "appts_new("))
            store.db.commit()
    log.info("Outlook %s sync: %d calendars, window %s", "incremental" if incremental else "full", len(folders), window)
    for i, (cal, folder) in enumerate(folders):
        if cancel is not None and cancel.is_set():
            log.info("Outlook sync stopped at %s", cal)
            return -1
        if progress:
            progress(f"Outlook: reading {cal} ({i + 1}/{len(folders)}) · {n:,} bookings so far")
        t = time.monotonic()
        try:
            got = _read_table(cal, folder, flt, rows, flush_big, cancel)
        except Exception as ex:
            log.warning("Outlook table read failed for %s: %r - reading item by item", cal, ex)
            got = None
        if cancel is not None and cancel.is_set():  # stopped mid-calendar: never swap in a partial copy
            log.info("Outlook sync stopped in %s", cal)
            return -1
        if got is None:  # no Table support for this folder: walk the items (slower)
            try:
                found = folder.Items.Restrict(flt)
                item = found.GetFirst()
            except Exception as ex:
                log.warning("Outlook: can't read %s: %r", cal, ex)
                continue
            walked = 0
            while item is not None:
                try:
                    rows.append(_row(cal, item, schedule._naive))
                except Exception:
                    pass
                walked += 1
                if len(rows) >= 500:
                    n += _flush(store, rows, table)
                    if progress:
                        progress(f"Outlook: reading {cal} ({i + 1}/{len(folders)}) item by item · {n:,} bookings so far")
                    if cancel is not None and cancel.is_set():
                        return -1
                item = found.GetNext()
            got = walked
        n += _flush(store, rows, table)
        log.info("Outlook: %s - %s rows in %.1fs", cal, got, time.monotonic() - t)
    n += _flush(store, rows, table)
    if not incremental:
        with store._lock:
            store.db.executescript("BEGIN IMMEDIATE; DROP TABLE IF EXISTS appts; ALTER TABLE appts_new RENAME TO appts;"
                                   + SCHEMA + "COMMIT;")
    log.info("Outlook sync done: %d bookings in %.0fs", n, (datetime.now() - began).total_seconds())
    store.meta_set("outlook_synced_at", began.isoformat(timespec="seconds"))
    store.meta_set("outlook_window", window)
    if not incremental:
        store.meta_set("outlook_full_sync_at", began.isoformat(timespec="seconds"))
    return n


TABLE_COLS = ("EntryID", "Subject", "Start", "End", "Categories", "LastModificationTime")


def _table_row(cal: str, vals) -> tuple:
    entry, subject, start, end, cats, modified = vals
    subject = subject or ""
    info = parse_subject(subject)
    lk, fk, nk = _keys(info)
    iso = lambda d, spec="minutes": datetime(d.year, d.month, d.day, d.hour, d.minute, d.second).isoformat(timespec=spec)
    return (entry, cal, iso(start), iso(end), subject, cats or "", lk, fk, nk,
            info["dob"].isoformat() if info.get("dob") else "", info.get("account", ""), info.get("insurance", ""),
            info.get("secondary", ""), iso(modified, "seconds"))


def _read_table(cal: str, folder, flt: str, rows: list, flush, cancel) -> int | None:
    """Bulk-read a calendar with Outlook's Table object (one call per 500 rows). None if unsupported."""
    try:
        table = folder.GetTable(flt)
        table.Columns.RemoveAll()
        for c in TABLE_COLS:
            table.Columns.Add(c)  # built-in names -> dates come back in local time
    except Exception:
        return None
    got = 0
    while not table.EndOfTable:
        if cancel is not None and cancel.is_set():
            break
        block = table.GetArray(500)
        if not block:
            break
        # Normalise to one tuple per appointment: in a row the Start column (index 2) is a date.
        row_major = len(block[0]) == len(TABLE_COLS) and hasattr(block[0][2], "year")
        data = block if row_major else list(zip(*block))
        for vals in data:
            try:
                rows.append(_table_row(cal, vals))
                got += 1
            except Exception:
                continue
        flush()
    return got


def _flush(store, rows: list, table: str = "appts") -> int:
    n = len(rows)
    if n:
        with store._lock:
            store.db.executemany(f"INSERT OR REPLACE INTO {table} VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            store.db.commit()
        rows.clear()
    return n


def status(store) -> tuple[int, str]:
    ensure_schema(store)
    with store._lock:
        n = store.db.execute("SELECT COUNT(*) FROM appts").fetchone()[0]
    return n, store.meta_get("outlook_synced_at") or ""


@dataclass
class Booking:
    cal: str
    start: datetime
    end: datetime
    subject: str
    categories: list[str] = field(default_factory=list)
    dob: str = ""
    account: str = ""
    insurance: str = ""
    secondary: str = ""

    @property
    def seen(self) -> bool:
        return self.cal != "Cancellations_No Show's" and not any(re.search(r"no.?show|cancel", c, re.I) for c in self.categories)

    def appointment(self) -> Appointment:
        return Appointment(self.cal, self.start, self.end, self.subject, self.categories)


def _booking(r) -> Booking:
    cal, start, end, subject, cats, dob, acct, ins, sec = r
    return Booking(cal, datetime.fromisoformat(start), datetime.fromisoformat(end), subject,
                   [c.strip() for c in cats.split(",") if c.strip()], dob, acct, ins, sec)


COLS = "cal, start, end, subject, cats, dob, acct, ins, sec"


def find_patient(store, last: str = "", first: str = "", dob: date | None = None, account: str = "") -> list[Booking]:
    """Every cached booking for a patient (like Outlook's Advanced Find, but instant).

    Matches by last name (narrowed by first name or nickname, and by DOB when the booking has one),
    plus any booking carrying the account number. With only a DOB, returns bookings with that DOB."""
    ensure_schema(store)
    queries = []
    lk = _fold(last.replace(" ", "")) if last else ""
    if lk:
        queries.append(("(last_key = ? OR last_key LIKE ? OR last_key LIKE ?)", [lk, f"%{lk}", f"{lk}%"], True))
    if account:
        queries.append(("ltrim(acct,'0') = ?", [account.lstrip("0")], False))
    if not queries and dob:
        queries.append(("dob = ?", [dob.isoformat()], False))
    fk = _fold(first)[:3] if first else ""
    seen, out = set(), []
    for where, args, by_name in queries:
        with store._lock:
            rows = store.db.execute(f"SELECT {COLS}, first_key, nick_key FROM appts WHERE {where} ORDER BY start", args).fetchall()
        for r in rows:
            first_key, nick_key = r[-2], r[-1]
            if by_name and fk and not (first_key.startswith(fk) or nick_key.startswith(fk)):
                continue
            if by_name and dob and r[5] != dob.isoformat():
                continue  # with a DOB given, only bookings carrying that DOB (skips rep lunches, notes)
            key = (r[0], r[1], r[3])
            if key not in seen:
                seen.add(key)
                out.append(_booking(r[:-2]))
    return sorted(out, key=lambda b: b.start)


def day(store, when: date, calendars: list[str] | None = None) -> list[Appointment]:
    """All cached appointments on a day (same shape schedule.read_day returns)."""
    ensure_schema(store)
    lo, hi = when.isoformat(), (when + timedelta(days=1)).isoformat()
    with store._lock:
        rows = store.db.execute(f"SELECT {COLS} FROM appts WHERE start >= ? AND start < ? ORDER BY start", (lo, hi)).fetchall()
    appts = [_booking(r).appointment() for r in rows]
    return [a for a in appts if not calendars or a.calendar in calendars]


def summary(bookings: list[Booking]) -> dict:
    """Consensus DOB / insurance and visit counts for a patient's bookings."""
    dobs, ins = {}, {}
    for b in bookings:
        if b.dob:
            dobs[b.dob] = dobs.get(b.dob, 0) + 1
        if b.insurance:
            ins[b.insurance] = ins.get(b.insurance, 0) + 1
    seen = [b for b in bookings if b.seen]
    return {"dobs": dobs, "insurance": max(ins, key=ins.get) if ins else "", "bookings": len(bookings),
            "seen": len(seen), "first_seen": seen[0].start.date().isoformat() if seen else "",
            "last_seen": max((b.start for b in seen if b.start <= datetime.now()), default=None)}


def dumps(bookings: list[Booking]) -> str:
    return json.dumps([{"cal": b.cal, "start": b.start.isoformat(timespec="minutes"), "cats": b.categories} for b in bookings])
