"""Patient balances from Veradigm PM, kept in the app database so every chart can show them.

Source today: Veradigm's Financial Inquiry, as captured by the open-balance runs. Each run writes one JSON line per
patient: self and insurance balance, the aging buckets, and every open item (date of service, voucher, provider,
charge, payments, balance, payer, billed date, age). Each run becomes a dated snapshot per account (Pt #), and the
chart shows the latest one with its date, so it's always clear how fresh a balance is.

Nothing here talks to Veradigm; it only reads the files the runs leave behind.
"""
from __future__ import annotations

import glob
import json
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime

SCHEMA = """
CREATE TABLE IF NOT EXISTS balances(
    acct TEXT, as_of TEXT, self REAL, insur REAL, total REAL, unassigned REAL,
    self_aging TEXT, insur_aging TEXT, items TEXT, source TEXT, PRIMARY KEY(acct, as_of));
"""
AGING = ["0-30", "31-60", "61-90", "Over 90", "Total"]
DEFAULT_FOLDER = r"C:\Users\admin\Documents\ClaudeRoutines\referrals\vd"


@dataclass
class Balance:
    acct: str
    as_of: date
    self: float
    insur: float
    total: float
    unassigned: float = 0.0
    self_aging: list = field(default_factory=list)
    insur_aging: list = field(default_factory=list)
    items: list = field(default_factory=list)     # dicts: svc voucher provider chg pmts balance payor coverage billed age
    source: str = ""

    @property
    def credit(self) -> bool:
        return self.self < 0  # a credit is only a negative self balance (Veradigm's "Unassigned" isn't one)

    @property
    def self_over_90(self) -> float:
        return self.self_aging[3] if len(self.self_aging) > 3 else 0.0

    @property
    def insur_over_90(self) -> float:
        return self.insur_aging[3] if len(self.insur_aging) > 3 else 0.0


def ensure_schema(store) -> None:
    with store._lock:
        store.db.executescript(SCHEMA)
        store.db.commit()


def _acct(v) -> str:
    return re.sub(r"\D", "", str(v or "")).lstrip("0")


def _money(v) -> float:
    try:
        return float(str(v).replace("$", "").replace(",", "").replace("(", "-").replace(")", "") or 0)
    except ValueError:
        return 0.0


def import_folder(store, folder: str = DEFAULT_FOLDER) -> int:
    """Read every results_*.jsonl the balance runs left in `folder`. Files already imported (same size and time)
    are skipped. Returns the number of patient snapshots stored."""
    ensure_schema(store)
    if not folder or not os.path.isdir(folder):
        return 0
    try:
        seen = json.loads(store.meta_get("balances_imported") or "{}")
    except ValueError:
        seen = {}
    rows = []
    for path in sorted(glob.glob(os.path.join(folder, "results_*.jsonl")), key=os.path.getmtime):
        st = os.stat(path)
        stamp = f"{st.st_size}:{int(st.st_mtime)}"
        if seen.get(os.path.basename(path)) == stamp:
            continue
        as_of = datetime.fromtimestamp(st.st_mtime).date().isoformat()  # the day the run looked it up
        with open(path, encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                acct = _acct(d.get("pt") or d.get("acct"))
                if not d.get("ok") or not acct:
                    continue  # the run couldn't find this patient in Veradigm
                items = [{k: r.get(k, "") for k in ("svc", "voucher", "provider", "chg", "pmts", "balance", "payor",
                                                     "coverage", "billed", "age")} for r in d.get("rows") or []]
                rows.append((acct, as_of, _money(d.get("self")), _money(d.get("insur")), _money(d.get("total")),
                             _money(d.get("unassigned")), json.dumps(d.get("self_aging") or []),
                             json.dumps(d.get("insur_aging") or []), json.dumps(items), os.path.basename(path)))
        seen[os.path.basename(path)] = stamp
    if rows:
        with store._lock:
            store.db.executemany("INSERT OR REPLACE INTO balances VALUES(?,?,?,?,?,?,?,?,?,?)", rows)
            store.db.commit()
    store.meta_set("balances_imported", json.dumps(seen))
    return len(rows)


def _balance(r) -> Balance:
    return Balance(r[0], date.fromisoformat(r[1]), r[2], r[3], r[4], r[5], json.loads(r[6] or "[]"),
                   json.loads(r[7] or "[]"), json.loads(r[8] or "[]"), r[9])


def latest(store, acct: str) -> Balance | None:
    """The most recent Veradigm snapshot for a Pt #, or None if Veradigm hasn't been checked for this patient yet."""
    acct = _acct(acct)
    if not acct:
        return None
    ensure_schema(store)
    r = store.reader().execute("SELECT * FROM balances WHERE acct = ? ORDER BY as_of DESC LIMIT 1", (acct,)).fetchone()
    return _balance(r) if r else None


def history(store, acct: str) -> list[Balance]:
    acct = _acct(acct)
    ensure_schema(store)
    rows = store.reader().execute("SELECT * FROM balances WHERE acct = ? ORDER BY as_of DESC", (acct,)).fetchall()
    return [_balance(r) for r in rows]


def latest_totals(store) -> dict[str, tuple[float, float, str]]:
    """{Pt #: (self, insurance, as_of)} from each account's latest snapshot - for the directory list."""
    ensure_schema(store)
    rows = store.reader().execute("""SELECT b.acct, b.self, b.insur, b.as_of FROM balances b
        JOIN (SELECT acct, MAX(as_of) m FROM balances GROUP BY acct) x ON x.acct = b.acct AND x.m = b.as_of""").fetchall()
    return {r[0]: (r[1], r[2], r[3]) for r in rows}
