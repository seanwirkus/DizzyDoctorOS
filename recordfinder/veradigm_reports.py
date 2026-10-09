"""Veradigm PM report PDFs (saved in the ALL folder) read into the app.

Revenue Detail Report, "Summarize by Voucher": one row per voucher with the date of service, voucher number,
Veradigm patient number and name, units, RVUs, charges, payments, refunds & debits, adjustments and Net - what is
still open on that visit (insurance and patient together; a negative Net is a credit). Imported rows give:
  - every patient's open vouchers and total open, aged by date of service,
  - the name Veradigm has for each patient number, to cross-check against the Outlook headers.
How to make the PDF: Reporting > Comparative Analysis Reports > Revenue Detail Report, Service Dates from/to,
Format Option "Summarize by Voucher", Run > Print to "Microsoft Print to PDF" > save in Desktop\\ALL.
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
CREATE TABLE IF NOT EXISTS vd_vouchers(
    voucher TEXT PRIMARY KEY, acct TEXT, name TEXT, dos TEXT, units REAL, charges REAL, payments REAL,
    refunds REAL, adjusts REAL, net REAL, report TEXT, as_of TEXT);
CREATE INDEX IF NOT EXISTS vd_vouchers_acct ON vd_vouchers(acct);
CREATE INDEX IF NOT EXISTS vd_vouchers_open ON vd_vouchers(net);
"""
DEFAULT_FOLDER = r"C:\Users\admin\Desktop\ALL"
_DATE = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")
_NUM = re.compile(r"^-?[\d,]*\.\d\d$")
_DIGITS = re.compile(r"^\d{3,9}$")
_RANGE = re.compile(r"Service Dates:\s*(\d{2}/\d{2}/\d{4})\s*-\s*(\d{2}/\d{2}/\d{4})")
_RUN = re.compile(r"(\d{1,2}/\d{1,2}/\d{4})\s+\d{1,2}:\d{2}:\d{2}\s*[AP]M")


@dataclass
class Voucher:
    voucher: str
    acct: str
    name: str
    dos: date
    units: float
    charges: float
    payments: float
    refunds: float
    adjusts: float
    net: float


def _money(s: str) -> float:
    return float(s.replace(",", ""))


def _d(s: str) -> date:
    m = _DATE.match(s)
    return date(int(m[3]), int(m[1]), int(m[2]))


def is_revenue_detail(path: str) -> bool:
    import pymupdf
    try:
        with pymupdf.open(path) as doc:
            head = doc[0].get_text()[:2000] if doc.page_count else ""
    except Exception:
        return False
    return "Revenue Detail Report" in head and "Voucher" in head and "Net" in head


def parse_revenue_detail(path: str) -> tuple[list[Voucher], dict]:
    """(vouchers, info) from a Revenue Detail Report PDF summarized by voucher. info: service_from/to, run_date."""
    import pymupdf
    out: list[Voucher] = []
    info: dict = {}
    with pymupdf.open(path) as doc:
        for pno, page in enumerate(doc):
            if pno == 0:
                head = page.get_text()
                if m := _RANGE.search(head):
                    info["service_from"], info["service_to"] = _d(m[1]).isoformat(), _d(m[2]).isoformat()
                if m := _RUN.search(head):
                    mo, dd, yy = m[1].split("/")
                    info["run_date"] = date(int(yy), int(mo), int(dd)).isoformat()
            rows: dict[int, list] = {}
            for w in page.get_text("words"):   # x0, y0, x1, y1, text, ...
                rows.setdefault(round(w[1] / 2.5), []).append(w)
            for key in sorted(rows):
                ws = sorted(rows[key], key=lambda w: w[0])
                toks = [w[4] for w in ws]
                # a voucher row: service date, voucher #, patient #, the name, then seven numbers
                if len(toks) < 11 or not _DATE.match(toks[0]) or not _DIGITS.match(toks[1]) or not _DIGITS.match(toks[2]):
                    continue
                nums = [t for t in toks[3:] if _NUM.match(t)]
                if len(nums) < 7:
                    continue
                units, _rvus, charges, payments, refunds, adjusts, net = (_money(x) for x in nums[-7:])
                first_num = next(i for i, t in enumerate(toks) if i > 2 and _NUM.match(t))
                name = " ".join(toks[3:first_num])
                try:
                    out.append(Voucher(toks[1], toks[2].lstrip("0"), name, _d(toks[0]), units, charges, payments,
                                       refunds, adjusts, net))
                except ValueError:
                    continue
    return out, info


def ensure_schema(store) -> None:
    with store._lock:
        store.db.executescript(SCHEMA)
        store.db.commit()


def import_report(store, path: str) -> int:
    """Store every voucher of a Revenue Detail PDF (a newer report replaces older rows for the same voucher)."""
    ensure_schema(store)
    vouchers, info = parse_revenue_detail(path)
    as_of = info.get("run_date") or datetime.fromtimestamp(os.path.getmtime(path)).date().isoformat()
    rows = [(v.voucher, v.acct, v.name, v.dos.isoformat(), v.units, v.charges, v.payments, v.refunds, v.adjusts, v.net,
             os.path.basename(path), as_of) for v in vouchers]
    with store._lock:
        # an older report must not overwrite what a newer one says about the same voucher
        store.db.executemany("""INSERT INTO vd_vouchers VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(voucher) DO UPDATE SET acct=excluded.acct, name=excluded.name, dos=excluded.dos,
              units=excluded.units, charges=excluded.charges, payments=excluded.payments, refunds=excluded.refunds,
              adjusts=excluded.adjusts, net=excluded.net, report=excluded.report, as_of=excluded.as_of
            WHERE excluded.as_of >= vd_vouchers.as_of""", rows)
        store.db.commit()
    return len(rows)


def import_folder(store, folder: str = DEFAULT_FOLDER, progress=None) -> int:
    """Import Revenue Detail PDFs in the ALL folder that are new or changed since the last import."""
    ensure_schema(store)
    if not folder or not os.path.isdir(folder):
        return 0
    try:
        seen = json.loads(store.meta_get("vd_reports_imported") or "{}")
    except ValueError:
        seen = {}
    n = 0
    for path in sorted(glob.glob(os.path.join(folder, "*Revenue Detail*.pdf")), key=os.path.getmtime):
        st = os.stat(path)
        stamp = f"{st.st_size}:{int(st.st_mtime)}"
        if seen.get(os.path.basename(path)) == stamp or not is_revenue_detail(path):
            continue
        if progress:
            progress(f"Reading Veradigm report {os.path.basename(path)}…")
        vouchers, _info = parse_revenue_detail(path)
        if vouchers:  # a report of injections only is still a Revenue Detail: its vouchers are real too
            n += import_report(store, path)
        seen[os.path.basename(path)] = stamp
    store.meta_set("vd_reports_imported", json.dumps(seen))
    return n


# --------------------------------------------------------------------------- reading

@dataclass
class OpenBalance:
    acct: str
    name: str
    total: float
    vouchers: int
    oldest: date | None
    as_of: date | None
    aging: list            # 0-30, 31-60, 61-90, over 90 (by date of service, as of the report date)

    @property
    def credit(self) -> bool:
        return self.total < 0


def _age_buckets(rows, as_of: date) -> list[float]:
    b = [0.0, 0.0, 0.0, 0.0]
    for dos, net in rows:
        days = (as_of - date.fromisoformat(dos)).days
        b[0 if days <= 30 else 1 if days <= 60 else 2 if days <= 90 else 3] += net
    return [round(x, 2) for x in b]


def open_for(store, acct: str) -> tuple[OpenBalance | None, list[Voucher]]:
    """The patient's open vouchers (Net not zero) from the imported Veradigm reports."""
    ensure_schema(store)
    acct = (acct or "").lstrip("0")
    if not acct:
        return None, []
    rows = store.reader().execute("""SELECT voucher, acct, name, dos, units, charges, payments, refunds, adjusts, net, as_of
        FROM vd_vouchers WHERE acct = ? AND abs(net) >= 0.005 ORDER BY dos""", (acct,)).fetchall()
    if not rows:
        name = store.reader().execute("SELECT name FROM vd_vouchers WHERE acct = ? LIMIT 1", (acct,)).fetchone()
        return (OpenBalance(acct, name[0], 0.0, 0, None, None, [0.0] * 4) if name else None), []
    vs = [Voucher(r[0], r[1], r[2], date.fromisoformat(r[3]), *r[4:10]) for r in rows]
    as_of = max(date.fromisoformat(r[10]) for r in rows)
    ob = OpenBalance(acct, rows[-1][2], round(sum(v.net for v in vs), 2), len(vs), vs[0].dos, as_of,
                     _age_buckets([(r[3], r[9]) for r in rows], as_of))
    return ob, vs


def open_totals(store) -> dict[str, OpenBalance]:
    """{Pt #: open balance} for every patient with anything open in the imported reports."""
    ensure_schema(store)
    got = store.reader().execute("""SELECT acct, name, dos, net, as_of FROM vd_vouchers WHERE abs(net) >= 0.005
                                    ORDER BY acct, dos""").fetchall()
    by: dict[str, list] = {}
    for acct, name, dos, net, as_of in got:
        by.setdefault(acct, []).append((name, dos, net, as_of))
    out = {}
    for acct, rows in by.items():
        as_of = max(date.fromisoformat(r[3]) for r in rows)
        out[acct] = OpenBalance(acct, rows[-1][0], round(sum(r[2] for r in rows), 2), len(rows),
                                date.fromisoformat(rows[0][1]), as_of, _age_buckets([(r[1], r[2]) for r in rows], as_of))
    return out


def status(store) -> tuple[int, int, str]:
    """(vouchers imported, patients with an open balance, newest report date)."""
    ensure_schema(store)
    db = store.reader()
    n = db.execute("SELECT COUNT(*) FROM vd_vouchers").fetchone()[0]
    p = db.execute("SELECT COUNT(DISTINCT acct) FROM vd_vouchers WHERE abs(net) >= 0.005").fetchone()[0]
    r = db.execute("SELECT MAX(as_of) FROM vd_vouchers").fetchone()[0] or ""
    return n, p, r


# --------------------------------------------------------------------------- name cross-check

@dataclass
class NameCheck:
    acct: str
    veradigm: str             # "First M Last" as Veradigm has it
    outlook: str              # "Last, First" from the Outlook headers ("" when no header carries this Pt #)
    dob: str
    issue: str                # "different name" / "Pt # not in any Outlook header" / "several Outlook patients"


def cross_check(store, only_open: bool = True) -> list[NameCheck]:
    """Veradigm's name for each patient number against the patient directory (Outlook headers)."""
    ensure_schema(store)
    db = store.reader()
    where = "WHERE abs(net) >= 0.005" if only_open else ""
    vd = dict(db.execute(f"SELECT acct, name FROM vd_vouchers {where} GROUP BY acct").fetchall())
    try:
        pats = db.execute("SELECT ltrim(acct,'0'), last, first, last_key, first_key, dob FROM patients WHERE acct != ''").fetchall()
    except Exception:
        pats = []
    by_acct: dict[str, list] = {}
    for a, last, first, lk, fk, dob in pats:
        by_acct.setdefault(a, []).append((last, first, lk, fk, dob))
    out = []
    for acct, name in sorted(vd.items(), key=lambda kv: kv[1]):
        words = name.split()
        v_first, v_last = (_fold(words[0]) if words else ""), (_fold("".join(words[1:])) if len(words) > 1 else "")
        hits = by_acct.get(acct, [])
        if not hits:
            out.append(NameCheck(acct, name, "", "", "Pt # not in any Outlook header"))
            continue
        if len({(h[2], h[4]) for h in hits}) > 1:
            out.append(NameCheck(acct, name, "; ".join(f"{h[0]}, {h[1]}" for h in hits), hits[0][4], "several Outlook patients"))
            continue
        last, first, lk, fk, dob = hits[0]
        # Veradigm puts middle initials and suffixes in the name: compare the last name as a suffix of the tail
        same_last = bool(lk) and (v_last.endswith(lk) or lk.endswith(v_last) or lk in v_last)
        same_first = bool(fk) and (v_first[:3] == fk[:3])
        if not (same_last and same_first):
            out.append(NameCheck(acct, name, f"{last}, {first}", dob, "different name"))
    return out
