"""Rank indexed files against a request's patient (account #, name, DOS, DOB)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime


@dataclass
class Hit:
    score: int
    path: str
    name: str
    folder: str
    size: int
    mtime: float
    why: str


def date_variants(d: date) -> list[str]:
    m, dd, y, yy = d.month, d.day, d.year, d.year % 100
    out = {
        f"{m:02}{dd:02}{y}", f"{m:02}{dd:02}{yy:02}", f"{y}{m:02}{dd:02}",
        f"{y}-{m:02}-{dd:02}", f"{y}.{m:02}.{dd:02}", f"{y}_{m:02}_{dd:02}",
    }
    for sep in "-._ ":
        out |= {f"{m:02}{sep}{dd:02}{sep}{y}", f"{m:02}{sep}{dd:02}{sep}{yy:02}",
                f"{m}{sep}{dd}{sep}{y}", f"{m}{sep}{dd}{sep}{yy:02}"}
    return sorted(out, key=len, reverse=True)


def _bounded(term: str, text: str, digits: bool) -> bool:
    if digits:
        return re.search(rf"(?<!\d){re.escape(term)}(?!\d)", text) is not None
    return re.search(rf"(?<![a-z]){re.escape(term)}(?![a-z])", text) is not None


def search_terms(first: str, last: str, account: str) -> list[str]:
    terms = []
    if account:
        terms += [account, account.lstrip("0")] if len(account.lstrip("0")) >= 4 else [account]
    if last and len(last) >= 3:
        terms.append(last)
    elif last and first:
        terms.append(f"{last}, {first}")
        terms.append(f"{first} {last}")
    return terms


def rank(rows: list[tuple], first: str, last: str, account: str, dos: list[date], dob: date | None,
         hints: list[str], limit: int = 200) -> list[Hit]:
    first, last = first.lower(), last.lower()
    acct_forms = [a for a in {account, account.lstrip("0")} if a and len(a) >= 4] if account else []
    dos_forms = [(d, date_variants(d)) for d in dos]
    dob_forms = date_variants(dob) if dob else []
    last_c, first_c = re.sub(r"[^a-z]", "", last), re.sub(r"[^a-z]", "", first)
    hits = []
    for path, name, folder, size, mtime in rows:
        lname, lpath = name.lower(), path.lower()
        score, why = 0, []
        compact = re.sub(r"[^a-z]", "", lname.split(".")[0])

        if any(_bounded(a, lname, True) for a in acct_forms):
            score += 50; why.append("acct# in name")
        elif any(_bounded(a, lpath, True) for a in acct_forms):
            score += 35; why.append("acct# in folder")

        if last and _bounded(last, lname, False):
            score += 25; why.append("last name")
            if first and _bounded(first, lname, False):
                score += 12; why.append("first name")
        elif last_c and first_c and compact.startswith(last_c + first_c):
            score += 37; why.append("LastFirst name")        # "SmithJohn 09 24 26.pdf"
        elif last and _bounded(last, lpath, False):
            score += 15; why.append("last name in folder")
            if first and _bounded(first, lpath, False):
                score += 8

        if score < 25:
            continue  # neither the account nor the patient's name matched

        for d, forms in dos_forms:
            if any(_bounded(f, lname, True) for f in forms):
                score += 22; why.append(f"DOS {d:%m/%d}")
                break
            if any(_bounded(f, lpath, True) for f in forms):
                score += 14; why.append(f"DOS {d:%m/%d} in folder")
                break
            if abs((datetime.fromtimestamp(mtime).date() - d).days) <= 3:
                score += 5; why.append("saved near DOS")
                break
        if dob_forms and any(_bounded(f, lpath, True) for f in dob_forms):
            score += 8; why.append("DOB")

        stem = re.sub(r"[^a-z0-9]+", " ", lname)
        if any(re.search(rf"\b{re.escape(h)}", stem) for h in hints):
            score += 8; why.append("doc type")

        hits.append(Hit(score, path, name, folder, size, mtime, ", ".join(why)))
    hits.sort(key=lambda h: (-h.score, -h.mtime))
    return hits[:limit]
