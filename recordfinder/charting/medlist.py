"""The medication list: the drugs the search knows, and the note's medication box as a list.

The progress note keeps medications as text in one box (meds-descr), the way the practice writes them:

    --------PO Medications--------
    Amlodipine 5mg QD
    Meclizine 25mg PRN
    Gabapentin 300mg TID (new)
    Topamax 50mg BID (D/C)
    ***I, SW, independently reviewed the medication list with patient, 10/06/2026***

The screen edits that text as rows (name, dose, how often, status) and writes it back the same way; a line it
didn't change is written back exactly as it was. The drug list (web/meds.json) is offline: names, brands, class,
common strengths and the two flags this clinic cares about (ototoxic, held before vestibular testing).
"""
from __future__ import annotations

import json
import os
import re
from datetime import date
from functools import lru_cache

from . import phrases
from .note import parse_date

MEDS_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web", "meds.json")

STRENGTH = re.compile(r"(?<![\w.])(\d+(?:[.,]\d+)?(?:\s*[-/]\s*\d+(?:\.\d+)?)*)\s*(mg|mcg|µg|g|ml|units?|iu|%|meq|caps?|capsules?|tabs?|tablets?|"
                      r"puffs?|sprays?|drops?|patch(?:es)?|softgels?|gumm(?:y|ies))"
                      r"(?:\s*/\s*(?:ml|hr|24\s*hrs?|72\s*hrs?|spray|actuation|dose|tab|g|day))?(?![a-z])", re.I)
# Speech recognition often writes the clinic's spoken "D/C" as "did see" or
# "dee cee".  Treat those spellings exactly like a written D/C, but only in a
# trailing status comment so ordinary medication names and instructions cannot
# be misclassified.
STATUS = re.compile(r"\s*[\(\[]\s*(new|not taking|d\s*/?\s*c(?:\s*'?d)?|did see|dee\s+cee|stopped|discontinued|dose changed|changed|held|on hold)\b[^)\]]*[\)\]]\s*$", re.I)
# Only the clinic's own review statements (phrases.MED_RECONCILIATION); a medication line that mentions "med rec" stays one.
REVIEW = re.compile(r"^[-*\s]*(?:I,\s*[^,]{1,24},\s*independently (?:performed medication reconciliation|reviewed the medication list with (?:the )?patient"
                    r"(?:,\s*\d{1,2}/\d{1,2}/\d{2,4})?)|medication list was reviewed with (?:the )?patient\b.*|(?:PA\s+)?[A-Za-z.]{1,12} reviewed and updated "
                    r"(?:the )?medication list with (?:the )?patient\b.*)[.*\s-]*$", re.I)
HEADER = re.compile(r"^[-=*_\s]*(?:PO\s+)?medications?(?:\s+list)?[-=*_\s:]*$|^-{3,}.*-{3,}$", re.I)
DATE = re.compile(r"(\d{1,2}/\d{1,2}/\d{2,4})")


@lru_cache(maxsize=1)
def drugs() -> list[dict]:
    try:
        with open(MEDS_JSON, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


@lru_cache(maxsize=1)
def all_names() -> list[str]:
    """Every generic and brand name, longest first (so "Carbidopa-levodopa" is found before "Levodopa")."""
    names = set()
    for d in drugs():
        names.add(d["name"])
        names.update(d.get("brands") or [])
    return sorted(names, key=lambda n: (-len(n), n))


def search(q: str, limit: int = 12) -> list[dict]:
    """Drugs whose generic or brand name starts with (then contains) what was typed."""
    q = (q or "").strip().lower()
    if not q:
        return []
    first, then = [], []
    for d in drugs():
        names = [d["name"]] + list(d.get("brands") or [])
        low = [n.lower() for n in names]
        if any(n.startswith(q) for n in low):
            first.append(d)
        elif any(q in n for n in low):
            then.append(d)
    return (first + then)[:limit]


def lookup(name: str) -> dict | None:
    key = (name or "").strip().lower()
    for d in drugs():
        if d["name"].lower() == key or key in (b.lower() for b in d.get("brands") or []):
            return d
    for d in drugs():
        if any(b.lower().startswith(key + " ") for b in d.get("brands") or []):     # "Nurtec" is "Nurtec ODT"
            return d
    return None


# ---------------------------------------------------------------------------------------------------- the note's box

def parse(text: str) -> list[dict]:
    """The medication box as lines: header, review statement, medication rows, or other text."""
    rows = []
    for raw in str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.strip()
        if not line:
            continue
        if REVIEW.match(line):
            d = DATE.search(line)
            rows.append({"kind": "review", "raw": raw, "date": d.group(1) if d else ""})
            continue
        if HEADER.match(line):
            rows.append({"kind": "header", "raw": raw})
            continue
        rows.append(dict(kind="med", raw=raw, **split_line(line)))
    return rows


def split_line(line: str) -> dict:
    """"Gabapentin 300mg TID (new)" -> name, dose, sig, status."""
    status = ""
    m = STATUS.search(line)
    if m:
        status = _status(m.group(1))
        line = line[: m.start()].rstrip()
    line = line.strip(" -*•")
    m = STRENGTH.search(line)
    if m:
        name, dose, sig = line[: m.start()].strip(" ,-"), line[m.start(): m.end()].strip(), line[m.end():].strip(" ,-")
    else:
        sig_at = re.search(r"\b(?:QD|QAM|QPM|QHS|HS|BID|TID|QID|PRN|Q\d+H|daily|nightly|weekly|monthly|once|twice|as needed)\b", line, re.I)
        name, dose, sig = (line[: sig_at.start()].strip(" ,-"), "", line[sig_at.start():].strip()) if sig_at else (line, "", "")
    return {"name": name, "dose": dose, "sig": sig, "status": status}


def _status(word: str) -> str:
    w = re.sub(r"\s+", " ", word.lower()).strip()
    compact = re.sub(r"[\s/']", "", w)
    if compact.startswith(("dc", "didsee", "deecee", "stopped", "discontinued")):
        return "stopped"
    if w.startswith("not"):
        return "not taking"
    if "chang" in w:
        return "changed"
    if w.startswith(("held", "on hold")):
        return "held"
    return "new" if w == "new" else ""


def reviewed_on(text: str) -> date | None:
    """The date on the box's medication review statement, if it has one."""
    for row in parse(text):
        if row["kind"] == "review" and row["date"]:
            return parse_date(row["date"])
    return None


def reviewed_today(values: dict) -> bool:
    dos = parse_date(str(values.get("Date") or "")) or date.today()
    return reviewed_on(str(values.get("meds-descr") or "")) == dos


def carry(text: str) -> str:
    """The box for the next visit: stopped medications dropped, today's markers (new, changed) and the review
    statement taken off, so the next visit reviews the list again."""
    out = []
    for row in parse(text):
        if row["kind"] == "review":
            continue
        if row["kind"] == "med":
            if row["status"] == "stopped":
                continue
            if row["status"] in ("new", "changed"):
                out.append(STATUS.sub("", row["raw"]).rstrip())
                continue
        out.append(row["raw"].rstrip())
    return "\n".join(out)


def review_line(initials: str, when: date) -> str:
    return phrases.MED_RECONCILIATION[1].format(initials=initials or "**", date=when.strftime("%m/%d/%Y"))
