"""Map every scanned superbill batch to the patients on it, ahead of time.

Each batch PDF ("EC 09.24.26.pdf") holds one provider's superbills for a day, each followed by that patient's own
pages. For every batch: the provider's calendar that day (Outlook cache) gives the patients in scan order, the pages
are read with Windows OCR, and superbills.align_day() places each patient on their superbill page. The result is
stored per page, so a chart shows every visit's superbill at once and the dashboard can show what's been mapped.

Runs as its own process (DizzyDoctor.exe --map-superbills): several worker processes read pages at the same time
and only this process writes the database. It can be stopped any time and picks up where it left off; a batch that
changes on the drive is mapped again. Only the placements are kept, not the OCR text (that would be over a GB).
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import date, datetime

from . import superbills

SCHEMA = """
CREATE TABLE IF NOT EXISTS sb_batches(
    path TEXT PRIMARY KEY, code TEXT, day TEXT, size INTEGER, mtime REAL, status TEXT, pages INTEGER, sheets INTEGER,
    placed INTEGER, note TEXT, mapped_at TEXT);
CREATE INDEX IF NOT EXISTS sb_batches_day ON sb_batches(day);
CREATE TABLE IF NOT EXISTS sb_pages(
    batch TEXT, page INTEGER, day TEXT, code TEXT, last TEXT, first TEXT, dob TEXT, acct TEXT, last_key TEXT,
    how TEXT, evidence INTEGER, PRIMARY KEY(batch, page));
CREATE INDEX IF NOT EXISTS sb_pages_patient ON sb_pages(last_key, dob);
CREATE INDEX IF NOT EXISTS sb_pages_day ON sb_pages(day);
"""
# Batch codes that aren't in the provider roster: the calendar their patients are booked on.
CODE_CALENDARS = {"EJ": "Emily Jing", "HB": "Haleigh Boe", "MG": "Melanie Glover", "EEG": "EEG",
                  "VNGVEMP": "VNG_VEMP", "VNGTYMP": "VNG_VEMP", "VNG": "VNG_VEMP", "TYEMGNCV": "TY EMG/NCV",
                  "TYEMG": "TY EMG/NCV", "MGEMGNCV": "MG EMG/NCV", "MP2": "Dr. Patel", "IMPMP": "IMP"}
DONE = ("mapped", "no calendar", "no schedule", "no superbills")


def ensure_schema(store) -> None:
    with store._lock:
        store.db.executescript(SCHEMA)
        store.db.commit()


def calendar_for(code: str, roster) -> str | None:
    from . import rules
    n = superbills.norm_code(code)
    if n in CODE_CALENDARS:
        return CODE_CALENDARS[n]
    p = rules.by_code(roster, code)
    if p and p.calendar:
        return p.calendar
    for p in sorted(roster, key=lambda p: -len(p.code)):   # "TY 2", "EC AM" -> the provider's calendar
        if p.calendar and n.startswith(superbills.norm_code(p.code)):
            return p.calendar
    return None


def discover(store, root: str) -> int:
    """Find every batch PDF under the superbill folder; new or changed ones are queued. Returns batches found.
    Only names and sizes are read here (opening 5,000 PDFs over the network takes an hour); the workers, which
    open each PDF anyway, skip the app's own amended copies."""
    ensure_schema(store)
    known = {r[0]: (r[1], r[2]) for r in store.reader().execute("SELECT path, size, mtime FROM sb_batches")}
    new, total = [], 0
    for dp, _dn, fn in os.walk(root):
        for n in fn:
            path = os.path.join(dp, n)
            b = superbills.parse_batch_name(path)
            if b is None:
                continue
            total += 1
            try:
                st = os.stat(path)
            except OSError:
                continue
            old = known.get(path)
            if old is None or old[0] != st.st_size or abs(old[1] - st.st_mtime) > 1:
                new.append((path, b.code, b.day.isoformat(), st.st_size, st.st_mtime))
    if new:
        with store._lock:
            store.db.executemany("""INSERT INTO sb_batches(path, code, day, size, mtime, status) VALUES(?,?,?,?,?,NULL)
                ON CONFLICT(path) DO UPDATE SET size=excluded.size, mtime=excluded.mtime, status=NULL""", new)
            store.db.commit()
    return total


def pending(store, years: list[int] | None = None) -> list[tuple]:
    """(path, code, day) of batches still to map, newest first."""
    ensure_schema(store)
    rows = store.reader().execute("SELECT path, code, day FROM sb_batches WHERE status IS NULL OR status = 'error' "
                                  "ORDER BY day DESC").fetchall()
    return [r for r in rows if not years or int(r[2][:4]) in years]


def read_pages(path: str) -> tuple[str, list[str] | None]:
    """Worker process: OCR every page of one batch. Returns (path, page texts), or (path, None) for an amended
    copy the app made itself (not a scanned batch)."""
    import pymupdf
    from . import amend, ocr
    texts = []
    with pymupdf.open(path) as doc:
        if amend.amended_keywords((doc.metadata or {}).get("keywords") or ""):
            return path, None
        for page in doc:
            texts.append(ocr.text_of(ocr.ocr_page(page)))
    return path, texts


@dataclass
class Result:
    status: str
    pages: int = 0
    sheets: int = 0
    placed: int = 0
    note: str = ""
    rows: list = None


def place(store, cfg: dict, roster, path: str, texts: list[str]) -> Result:
    """Place the day's patients on the batch's superbill pages."""
    from . import attach, outlook_cache, schedule
    from .superbills import _fold
    b = superbills.parse_batch_name(path)
    cal = calendar_for(b.code, roster)
    if not cal:
        return Result("no calendar", len(texts), note=f"no calendar for batch code '{b.code}'", rows=[])
    order = schedule.schedule_order(outlook_cache.day(store, b.day), cal)
    _cover, packets = superbills.split_packets(texts)
    if not packets:
        return Result("no superbills", len(texts), note="no superbill page found in the batch", rows=[])
    if not order:
        return Result("no schedule", len(texts), len(packets), note=f"nobody seen on {cal} that day", rows=[])
    pats, weights = attach._day_patients(store, cfg, order, b.day)
    placed = superbills.align_day(texts, pats, weights, groups=[a.start.isoformat() for a in order])
    rows = []
    for pl in placed:
        if pl.packet is None:
            continue
        q = pats[pl.patient]
        rows.append((path, pl.packet.superbill + 1, b.day.isoformat(), b.code, q.last, q.first,
                     q.dob.isoformat() if q.dob else "", (q.account or "").lstrip("0"), _fold(q.last.replace(" ", "")),
                     pl.how, pl.evidence))
    return Result("mapped", len(texts), len(packets), len(rows), rows=rows)


def save(store, path: str, res: Result) -> None:
    with store._lock:
        store.db.execute("DELETE FROM sb_pages WHERE batch = ?", (path,))
        if res.rows:
            store.db.executemany("INSERT OR REPLACE INTO sb_pages VALUES(?,?,?,?,?,?,?,?,?,?,?)", res.rows)
        store.db.execute("UPDATE sb_batches SET status=?, pages=?, sheets=?, placed=?, note=?, mapped_at=? WHERE path=?",
                         (res.status, res.pages, res.sheets, res.placed, res.note,
                          datetime.now().isoformat(timespec="seconds"), path))
        store.db.commit()


def _low_priority() -> None:
    """Worker processes run below normal priority so the PC stays responsive for whoever is using it."""
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        k32.SetPriorityClass(k32.GetCurrentProcess(), 0x4000)   # BELOW_NORMAL_PRIORITY_CLASS
    except Exception:
        pass


def run(db_path: str, cfg: dict, workers: int = 6, years: list[int] | None = None, log=print,
        limit: int | None = None) -> int:
    """Map every pending batch (newest first; at most `limit`). Stops early when the app sets meta sb_stop=1.
    Returns batches mapped."""
    from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
    from . import rules
    from .index import Store
    store = Store(db_path)
    # hours of work: wait out the app's own writes (hourly refresh, publishing) instead of failing after 5 s
    store.db.execute("PRAGMA busy_timeout = 60000")
    ensure_schema(store)
    store.meta_set("sb_stop", "0")
    root = cfg.get("superbill_root", "")
    if not root or not os.path.isdir(root):
        store.meta_set("sb_progress", json.dumps({"state": "error", "text": "The superbill folder isn't set or reachable."}))
        return 0
    store.meta_set("sb_progress", json.dumps({"state": "running", "text": "Looking for batches…", "pid": os.getpid()}))
    total = discover(store, root)
    todo = pending(store, years)[:limit] if limit else pending(store, years)
    roster = rules.roster_from(cfg)
    done, t0, n_todo = 0, time.monotonic(), len(todo)

    def report(state="running"):
        rate = done / max(1.0, time.monotonic() - t0) * 3600
        text = (f"Mapping superbills: {done:,} of {n_todo:,} batches this run"
                + (f" · about {int((n_todo - done) / rate * 60):,} min left" if done and rate and state == "running" else ""))
        store.meta_set("sb_progress", json.dumps({"state": state, "text": text, "pid": os.getpid(), "done": done,
                                                  "todo": n_todo, "total": total, "at": datetime.now().isoformat(timespec="seconds")}))

    report()
    queue = list(todo)
    with ProcessPoolExecutor(max_workers=max(1, workers), initializer=_low_priority) as ex:
        running = {}
        while queue or running:
            stop = store.meta_get("sb_stop") == "1"
            while queue and len(running) < workers * 2 and not stop:
                path, _code, _day = queue.pop(0)
                running[ex.submit(read_pages, path)] = path
            if not running:
                break
            finished, _ = wait(running, timeout=5, return_when=FIRST_COMPLETED)
            for fut in finished:
                path = running.pop(fut)
                try:
                    _p, texts = fut.result()
                    res = (place(store, cfg, roster, path, texts) if texts is not None
                           else Result("skipped", note="an amended copy made by DizzyDoctor", rows=[]))
                except Exception as exc:   # an unreadable PDF or a busy share: retried next run
                    res = Result("error", note=f"{type(exc).__name__}: {exc}"[:300], rows=[])
                save(store, path, res)
                done += 1
                if done % 5 == 0:
                    report()
            if stop and not finished:
                for fut in running:
                    fut.cancel()
                break
    report("stopped" if store.meta_get("sb_stop") == "1" else "done")
    log(f"mapped {done} batches")
    store.close()
    return done


# --------------------------------------------------------------------------- reading

@dataclass
class Sheet:
    batch: str
    page: int
    day: date
    code: str
    how: str
    evidence: int

    @property
    def sure(self) -> bool:
        return self.how == "printed" or self.evidence >= 30


def for_patient(store, last_key: str, dob: str) -> list[Sheet]:
    ensure_schema(store)
    rows = store.reader().execute("SELECT batch, page, day, code, how, evidence FROM sb_pages WHERE last_key = ? AND dob = ? "
                                  "ORDER BY day DESC", (last_key, dob)).fetchall()
    return [Sheet(r[0], r[1], date.fromisoformat(r[2]), r[3], r[4], r[5]) for r in rows]


def status(store) -> dict:
    """Counts for the dashboard: batches per status, superbills placed, and the running job's progress."""
    ensure_schema(store)
    db = store.reader()
    by = dict(db.execute("SELECT coalesce(status, 'waiting'), COUNT(*) FROM sb_batches GROUP BY 1").fetchall())
    placed = db.execute("SELECT COUNT(*) FROM sb_pages").fetchone()[0]
    years = db.execute("""SELECT substr(day, 1, 4), COUNT(*), SUM(status = 'mapped'), SUM(coalesce(placed, 0))
                          FROM sb_batches GROUP BY 1 ORDER BY 1 DESC""").fetchall()
    try:
        prog = json.loads(store.meta_get("sb_progress") or "{}")
    except ValueError:
        prog = {}
    return {"by_status": by, "batches": sum(by.values()), "placed": placed, "years": years, "progress": prog}
