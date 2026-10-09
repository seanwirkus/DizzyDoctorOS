"""What the chart screen shows, from wherever it comes from.

AppProvider reads the clinic's data through the rest of the app (patient directory, Outlook cache,
drive index, notes on the drive). DemoProvider is a made-up clinic for trying the screens with no
data at all (no real patients). Both answer the same questions, so the screens don't care which
one is behind them.
"""
from __future__ import annotations

import os
import re
import tempfile
import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from . import note, visits
from .note import NoteFile

TONES = ("red", "amber", "green", "blue", "grey")
PROVIDER_COLORS = ("#0071e3", "#7c3aed", "#00875a", "#c2410c", "#b4235a", "#0369a1", "#6b7280", "#8a5a00")


def _provider_color(calendar: str) -> str:
    """Stable across date ranges and computers; unlike hash(), SHA does not change between launches."""
    return PROVIDER_COLORS[hashlib.sha256(calendar.casefold().encode("utf-8")).digest()[0] % len(PROVIDER_COLORS)]


def _iso(d) -> str:
    return d.isoformat() if isinstance(d, (date, datetime)) else (d or "")


def visit_kind(row: dict) -> dict:
    """The schedule row with its visit type: the kind the booking's words say, its label and colour (blank when the
    booking says nothing, so the note's own boxes decide)."""
    kind = visits.classify(str(row.get("visit", "")), str(row.get("cal", ""))) if row.get("patient") else ""
    if kind:
        t = visits.describe(kind)
        row.update({"kind": kind, "kind_label": t["label"], "kind_short": t["short"], "kind_color": t["color"]})
    else:
        row.update({"kind": "", "kind_label": "", "kind_short": "", "kind_color": ""})
    return row


def _mdY(d) -> str:
    if isinstance(d, datetime):
        d = d.date()
    return d.strftime("%m/%d/%Y") if isinstance(d, date) else (d or "")


# --------------------------------------------------------------------------- the snapshot

def snapshot_from_note(path: str) -> dict:
    """The patient summary Epic calls SnapShot, read off the latest note (with the marks kept beside it)."""
    from . import distributions
    v = note.read(path)
    v.update(distributions.read_sidecar(path))
    return snapshot_from_values(v, path)


def _exam_findings(v: dict) -> list[dict]:
    """The exam rows marked abnormal, as "Sensory: Decreased vibration in a stocking distribution"."""
    out = []
    for row in note.SCHEMA.get("exam_rows", []):
        text = str(v.get(row.get("findings") or "", "") or "").replace("\r", " ").strip()
        abnormal = bool(str(v.get(row.get("abnormal") or "", "") or "").strip())
        if abnormal or (text and row.get("normal") and not v.get(row["normal"])):
            out.append({"group": row.get("group", ""), "label": row.get("label", ""), "text": text})
    return out


def _oculography(v: dict) -> list[str]:
    """What IR oculography concluded (the impression boxes), in the form's own words."""
    section = next((s for s in note.SCHEMA.get("sections", []) if s.get("id") == "goggles"), {})
    return [note.FIELDS[k]["label"] for k in section.get("fields", []) if k.startswith("imp-") and k != "imp-mp" and v.get(k)]


def snapshot_from_values(v: dict, path: str = "") -> dict:
    """The SnapShot from a note's values: the filed note, or the one open on screen."""
    from . import distributions
    imps = [{"n": i.number, "dx": i.diagnosis, "plan": i.plan} for i in note.impressions(str(v.get("impression-plan-descr", "")))]
    meds = [m.strip(" -*") for m in str(v.get("meds-descr", "")).replace("\r", "\n").split("\n")
            if m.strip() and not re.match(r"^[-*=_\s]*(PO Medications|Medication list|I, |\*)", m.strip(), re.I) and "reconcil" not in m.lower()]
    pmh = [note.FIELDS[k]["label"] for k in ("hist-hypt", "hist-dyslip", "hist-diab", "hist-stroke", "hist-migraine", "hist-cad",
                                             "hist-arrh", "hist-cervical", "hist-trauma", "hist-ear", "hist-ototox") if v.get(k)]
    studies = [{"name": v.get(f"x-study{i}", ""), "date": v.get(f"x-study{i}-date", ""), "result": v.get(f"x-study{i}-result", "")}
               for i in range(1, 6) if str(v.get(f"x-study{i}", "")).strip()]
    symptoms = {s: ("present" if v.get(k) else ("denies" if v.get(k + "-denies") else ""))
                for s, k in (("Dizziness", "dizziness"), ("Hearing loss", "hearingloss"), ("Tinnitus", "tinnitus"),
                             ("Gait ataxia", "gaitataxia"), ("Headache", "headache"))}
    attest = re.search(r"I, ([^,]+),", str(v.get("Imp/MP", "")))
    return {
        "note": path, "date": v.get("Date", ""), "name": v.get("Name", ""), "dob": v.get("DOB", ""), "age": v.get("HPI-age", ""),
        "sex": "F" if v.get("hpi-female") else ("M" if v.get("hpi-male") else ""),
        "visit": [note.FIELDS[k]["label"] for k in note.VISIT_TYPES if v.get(k)],
        "visit_kind": visits.kind_of(v)[0], "visit_color": visits.describe(visits.kind_of(v)[0])["color"] if visits.kind_of(v)[0] else "",
        "chief_complaint": v.get("ChiefComplaint", ""), "onset": v.get("OnsetDate", ""),
        "allergies": "NKDA" if v.get("nkda") else str(v.get("Allergies", "")).strip(),
        "impressions": imps, "meds": meds, "pmh": pmh, "studies": studies, "symptoms": symptoms,
        "falls": "yes" if v.get("ga-falls-yes") else ("no" if v.get("ga-falls-no") else ""),
        "vitals": {k: v.get(f, "") for k, f in (("bp", "const-bp"), ("hr", "const-hr"), ("wt", "WEIGHT"), ("bmi", "BMI"))},
        "time": v.get("appt-time", ""), "attending": attest.group(1) if attest and v.get("imp-mp") else "",
        "next": (str(v.get("next-appt", "")) + " " + " ".join(u for u in ("days", "weeks", "months") if v.get(f"next-appt-{u}"))).strip(),
        "pmd": v.get("NeuroConsultPMD", ""), "hpi": str(v.get("ChiefComplaint-descr", "")).replace("\r", "\n"),
        "labs": str(v.get("labs", "")).replace("\r", "\n"),
        "exam": _exam_findings(v), "oculography": _oculography(v), "goggle_date": v.get("goggle-date", ""),
        "plan": str(v.get("plan-descr", "")).replace("\r", "\n"), "next_note": str(v.get("nextappt-descr", "")).strip(),
        "distributions": distributions.from_values(v),
    }


def prep_reference(prev: dict, path: str = "") -> dict:
    """Grayed-out reference for a new visit: what the last note said, plus explicit follow-ups.

    This is reference only — nothing here is copied into the new note's exam, time or
    attestation. Follow-ups are suggestions the provider checks off or pulls into the plan.
    """
    snap = snapshot_from_note(path) if path and os.path.isfile(path) else None
    if snap is None:
        v = prev or {}
        imps = [{"n": i.number, "dx": i.diagnosis, "plan": i.plan}
                for i in note.impressions(str(v.get("impression-plan-descr", "")))]
        meds = [m.strip(" -*") for m in str(v.get("meds-descr", "")).replace("\r", "\n").split("\n")
                if m.strip() and not re.match(r"^[-*=_\s]*(PO Medications|Medication list|I, |\*)", m.strip(), re.I)
                and "reconcil" not in m.lower()][:12]
        studies = [{"name": v.get(f"x-study{i}", ""), "date": v.get(f"x-study{i}-date", ""),
                    "result": v.get(f"x-study{i}-result", "")}
                   for i in range(1, 6) if str(v.get(f"x-study{i}", "")).strip()]
        snap = {"date": v.get("Date", ""), "name": v.get("Name", ""), "chief_complaint": v.get("ChiefComplaint", ""),
                "onset": v.get("OnsetDate", ""), "impressions": imps, "meds": meds, "studies": studies,
                "next": (str(v.get("next-appt", "")) + " " + " ".join(u for u in ("days", "weeks", "months")
                        if v.get(f"next-appt-{u}"))).strip(),
                "next_note": str(v.get("nextappt-descr", "")).strip(), "hpi": str(v.get("ChiefComplaint-descr", "")),
                "plan": str(v.get("plan-descr", "")), "note": path}
        referrals = []
        for i in (1, 2):
            doc, spec = str(v.get(f"referral{i}-doc", "")).strip(), str(v.get(f"referral{i}-spec", "")).strip()
            diag = str(v.get(f"referral{i}-diag", "")).strip()
            if doc or spec:
                referrals.append({"doc": doc, "spec": spec, "diag": diag})
        snap["referrals"] = referrals
    else:
        v = prev or {}
        referrals = []
        for i in (1, 2):
            doc, spec = str(v.get(f"referral{i}-doc", "")).strip(), str(v.get(f"referral{i}-spec", "")).strip()
            diag = str(v.get(f"referral{i}-diag", "")).strip()
            if doc or spec:
                referrals.append({"doc": doc, "spec": spec, "diag": diag})
        snap["referrals"] = referrals
        snap["next_note"] = str(v.get("nextappt-descr", "")).strip()
        snap["plan"] = str(v.get("plan-descr", "")).replace("\r", "\n")
    followups: list[dict] = []
    for imp in snap.get("impressions", []) or []:
        text = imp["dx"] + (f" — {imp['plan']}" if imp.get("plan") else "")
        followups.append({"id": f"dx{imp['n']}", "text": text, "source": "Last assessment"})
    for s in snap.get("studies", []) or []:
        result = str(s.get("result", "")).strip()
        pending = (not result) or bool(re.search(
            r"order|pend|sched|await|to be|will |review|follow|RTC|repeat|recheck|pending", result, re.I))
        if s.get("name") and pending:
            followups.append({"id": f"study-{s['name'][:24]}",
                              "text": f"{s['name']} ({s.get('date', '')}): {result or 'no result on file — chase it'}",
                              "source": "Study"})
    nxt = str(snap.get("next", "") or "").strip()
    nxt_note = str(snap.get("next_note", "") or "").strip()
    if nxt or nxt_note:
        joined = " · ".join(x for x in (nxt, nxt_note) if x)
        followups.append({"id": "next", "text": f"Next visit was: {joined}", "source": "Follow-up"})
    for r in snap.get("referrals", []) or []:
        followups.append({"id": f"ref-{r.get('doc', '')[:20]}",
                          "text": "Referral: " + " · ".join(x for x in
                                [r.get("doc", ""), r.get("spec", ""), r.get("diag", "")] if x),
                          "source": "Referral"})
    for line in (str(snap.get("plan", "") or "").replace("\r", "\n").split("\n")):
        if re.search(r"\b(RTC|return|follow|repeat|recheck| sooner|PRN|call|phone)\b", line, re.I) and line.strip():
            followups.append({"id": f"plan-{len(followups)}", "text": line.strip()[:160], "source": "Last plan"})
            if len(followups) >= 12:
                break
    seen, unique = set(), []
    for f in followups:
        if f["text"] and f["text"] not in seen:
            seen.add(f["text"])
            unique.append(f)
    snap["followups"] = unique[:12]
    return snap


# --------------------------------------------------------------------------- a report's bottom line

_IMPRESSION_HEAD = re.compile(r"^\s*(?:impressions?|interpretation|conclusions?|summary|clinical correlation)\s*[:\-–]\s*(?P<rest>.*)$", re.I)
_REPORT_STOP = re.compile(r"^\s*(?:recommendation|comment|signed|electronically|technologist|reviewed by|interpreted by|cc:|note:|disclaimer)", re.I)


def report_result(path: str, max_pages: int = 3) -> str:
    """The result line of a report on the drive (a Zeto EEG, a VNG, an EMG): what follows "Impression:" /
    "Interpretation:" / "Conclusion:", up to a few lines, so chart prep can put it in the study row. Empty when the
    report has no such heading (the row then says "see report")."""
    if not path or not os.path.isfile(path) or not path.lower().endswith(".pdf"):
        return ""
    try:
        import pymupdf
        with pymupdf.open(path) as doc:
            text = "\n".join(page.get_text() for page in list(doc)[:max_pages])
    except Exception:
        return ""
    lines = [" ".join(ln.split()) for ln in text.splitlines()]
    for i, ln in enumerate(lines):
        m = _IMPRESSION_HEAD.match(ln)
        if not m:
            continue
        out = [m.group("rest").strip()] if m.group("rest").strip() else []
        for nxt in lines[i + 1:i + 6]:
            if not nxt.strip() or _REPORT_STOP.match(nxt) or _IMPRESSION_HEAD.match(nxt):
                if out:
                    break
                continue
            out.append(nxt.strip())
            if len(" ".join(out)) > 240:
                break
        result = " ".join(out).strip()
        if result:
            return result[:300]
    return ""


# --------------------------------------------------------------------------- the clinic's data

class AppProvider:
    def __init__(self, store, cfg: dict):
        self.store, self.cfg = store, cfg

    def search_patients(self, q: str) -> list[dict]:
        from .. import people
        out = []
        for p in people.search(self.store, q)[:40]:
            out.append({"id": p.id, "last": p.last, "first": p.first, "acct": p.acct, "dob": _mdY(p.dob),
                        "last_visit": _mdY(p.last_visit), "first_visit": _mdY(p.first_visit), "bookings": p.bookings, "files": p.files})
        return out

    def patient_notes(self, q: dict) -> list[NoteFile]:
        from .. import notes, people
        last, first = q.get("last", ""), q.get("first", "")
        found = {os.path.normcase(n.path): NoteFile(n.path, n.dos, "final" if n.kind == "final" else "draft")
                 for n in notes.find_notes(self.store, self.cfg, last, first)}
        # The patient reconciler can make a stronger link than a filename alone (DOB, account number or a unique
        # visit date). Pull those linked progress notes too, which also handles older Last+initial filenames.
        pid = q.get("id")
        try:
            pid = int(pid) if pid else None
        except (TypeError, ValueError):
            pid = None
        if pid is None and last:
            dob = note.parse_date(q.get("dob", ""))
            matches = people.lookup(self.store, last, first, dob, q.get("acct", ""))
            pid = matches[0].id if len(matches) == 1 else None
        roots = [os.path.normcase(os.path.realpath(p)) for p in self.cfg.get("notes_folders") or [] if p]
        for path, _name, folder, _size, _mtime, _how in (people.files_for(self.store, pid) if pid else []):
            if not path.lower().endswith(".pdf"):
                continue
            who, dos = notes.parse_note_name(path)
            low = os.path.normcase(os.path.realpath(folder))
            is_final = any(low.startswith(root) for root in roots)
            is_draft = "prepped" in low or "prep notes" in low
            if (is_final or is_draft) and (dos or who):
                found.setdefault(os.path.normcase(path), NoteFile(path, dos, "final" if is_final else "draft"))
        return sorted(found.values(), key=lambda n: (n.dos or date.min, n.kind == "final", n.path), reverse=True)

    def schedule(self, start: date, end: date) -> dict:
        from .. import rules, worklists
        rows = worklists._rows(self.store, start, end)
        calendars = sorted({r.cal for r in rows}, key=str.casefold)
        colors = {cal: _provider_color(cal) for cal in calendars}
        roster = rules.roster_from(self.cfg)
        providers = []
        for cal in calendars:
            p = rules.by_calendar(roster, cal)
            providers.append({"id": cal, "calendar": cal, "name": p.name if p else cal, "color": colors[cal]})

        groups: dict[tuple, list] = {}
        for r in rows:
            identity = (r.acct or (r.dob.isoformat() if r.dob else "") or (r.last_key + r.first_key),
                        r.start.isoformat(timespec="minutes"), (r.end or r.start).isoformat(timespec="minutes"))
            if r.patient and identity[0]:
                groups.setdefault(identity, []).append(r)
        duplicate = {id(r): sorted({x.cal for x in group}) for group in groups.values() if len({x.cal for x in group}) > 1 for r in group}

        out = []
        for r in rows:
            dup = duplicate.get(id(r), [])
            out.append(visit_kind({"id": r.entry_id, "date": r.start.date().isoformat(), "start": r.start.isoformat(timespec="minutes"),
                                   "end": (r.end or (r.start + timedelta(minutes=30))).isoformat(timespec="minutes"),
                                   "time": r.start.strftime("%H:%M"), "cal": r.cal, "provider": next((p["name"] for p in providers if p["id"] == r.cal), r.cal),
                                   "color": colors[r.cal], "patient": r.patient, "name": r.name, "last": r.last, "first": r.first,
                                   "dob": _mdY(r.dob), "acct": r.acct, "plan": r.plan, "visit": r.visit,
                                   "alerts": [{"text": t, "tone": tone} for t, tone in (r.alerts or [])], "tone": r.tone,
                                   "duplicate": bool(dup), "duplicate_count": len(dup), "duplicate_calendars": dup}))
        return {"start": start.isoformat(), "end": end.isoformat(), "providers": providers, "rows": out, "visit_types": visits.VISIT_TYPES}

    def today(self, day: date) -> list[dict]:
        from .. import worklists
        try:
            rows = worklists.day(self.store, day)
        except Exception:
            rows = worklists._rows(self.store, day, day + timedelta(days=1))
        out = []
        for r in rows:
            out.append(visit_kind({"id": r.entry_id, "time": r.start.strftime("%H:%M"), "cal": r.cal, "patient": r.patient, "name": r.name,
                                   "last": r.last, "first": r.first, "dob": _mdY(r.dob), "acct": r.acct, "plan": r.plan, "visit": r.visit,
                                   "alerts": [{"text": t, "tone": tone} for t, tone in (r.alerts or [])], "tone": r.tone,
                                   "auth": r.auth_status, "elig": r.elig.label() if r.elig else "", "mips": len(r.mips or [])}))
        return out

    def chart(self, q: dict) -> dict:
        from .. import outlook_cache, people
        from ..record_ui import build_visits, group_of, header_facts
        last, first, acct = q.get("last", ""), q.get("first", ""), q.get("acct", "")
        dob = note.parse_date(q.get("dob", ""))
        bookings = outlook_cache.find_patient(self.store, last, first, dob, acct)
        note_files = self.patient_notes(q)
        # patient_notes combines direct filename matches with the reconciler's DOB/account/visit-date file links.
        raw_notes = note_files
        visits = []
        for v in build_visits(bookings, raw_notes):
            n = next((x for x in v.notes if x.kind == "final"), v.notes[0] if v.notes else None)
            visits.append({"day": _mdY(v.day), "iso": _iso(v.day), "where": v.where(), "tags": v.tags(), "seen": v.seen,
                           "note": n.path if n else "", "note_kind": (n.kind if n else "")})
        pid = q.get("id")
        try:
            pid = int(pid) if pid else None
        except ValueError:
            pid = None
        if pid is None:
            found = people.lookup(self.store, last, first, dob, acct)
            pid = found[0].id if len(found) == 1 else None
        docs = []
        for path, name, folder, size, mtime, how in (people.files_for(self.store, pid) if pid else []):
            kind = people.kind_of(path)
            docs.append({"path": path, "name": name, "folder": folder, "size": size, "kind": kind, "group": group_of(kind),
                         "modified": datetime.fromtimestamp(mtime).strftime("%m/%d/%Y") if mtime else "", "how": how})
        facts = header_facts(bookings)
        summary = outlook_cache.summary(bookings)
        balance = None
        try:
            from .. import balances
            b = balances.latest(self.store, acct or facts.get("account", "")) if (acct or facts.get("account")) else None
            if b:
                balance = {k: getattr(b, k, "") for k in ("total", "patient", "insurance", "as_of") if hasattr(b, k)}
        except Exception:
            balance = None
        return {"patient": {"id": pid, "last": last, "first": first, "dob": q.get("dob", "") or (max(summary["dobs"], key=summary["dobs"].get) if summary["dobs"] else ""),
                            "acct": acct or facts.get("account", "")},
                "facts": facts, "insurance": summary.get("insurance", ""), "bookings": summary.get("bookings", 0), "seen": summary.get("seen", 0),
                "first_seen": summary.get("first_seen", ""), "last_seen": _iso(summary.get("last_seen")) if summary.get("last_seen") else "",
                "visits": visits, "documents": docs, "notes": [{"path": n.path, "dos": _iso(n.dos), "kind": n.kind, "name": os.path.basename(n.path)} for n in note_files],
                "balance": balance}

    def snapshot(self, q: dict) -> dict:
        files = [n for n in self.patient_notes(q) if n.kind == "final"] or self.patient_notes(q)
        if not files:
            return {}
        latest = max(files, key=lambda n: (n.dos or date.min, n.path))
        return snapshot_from_note(latest.path)

    def status(self) -> dict:
        from .. import outlook_cache
        try:
            n, synced = outlook_cache.status(self.store)
        except Exception:
            n, synced = 0, ""
        return {"clinic": "Ian Purcell, M.D., APC", "bookings": n, "synced": synced, "demo": False,
                "initials": self.cfg.get("initials") or self.cfg.get("signature", "").strip("*")}


# --------------------------------------------------------------------------- a made-up clinic

DEMO_PATIENTS = [
    {"id": 1, "last": "Sample", "first": "Alice", "acct": "123456", "dob": "02/14/1958", "sex": "F", "ins": "Medicare / AARP supplement", "time": "08:30", "visit": "Follow-up", "cal": "JP"},
    {"id": 2, "last": "Tester", "first": "Bob", "acct": "234567", "dob": "07/01/1961", "sex": "M", "ins": "Aetna PPO", "time": "09:15", "visit": "Follow-up · Epley chair", "cal": "TY"},
    {"id": 3, "last": "Example", "first": "Carl", "acct": "345678", "dob": "11/20/1949", "sex": "M", "ins": "SCMG (HMO)", "time": "10:00", "visit": "New consult", "cal": "IMP"},
    {"id": 4, "last": "Fake", "first": "Dana", "acct": "456789", "dob": "05/09/1972", "sex": "F", "ins": "Blue Shield PPO", "time": "10:45", "visit": "Follow-up · VNG results", "cal": "CC"},
    {"id": 5, "last": "Placeholder", "first": "Evelyn", "acct": "567890", "dob": "09/30/1944", "sex": "F", "ins": "Medicare", "time": "11:30", "visit": "Follow-up · CDP", "cal": "JP"},
]


class DemoProvider:
    """Made-up patients and notes (generated into a temp folder) - nothing here is a real person."""

    def __init__(self, root: str = ""):
        self.root = root or os.path.join(tempfile.gettempdir(), "ddos-demo-clinic")
        self.notes_dir = os.path.join(self.root, "Progress Notes")
        os.makedirs(self.notes_dir, exist_ok=True)
        self._notes: dict[int, list[NoteFile]] = {}
        self._make_notes()

    def _make_notes(self):
        plans = {
            1: ("Dizziness", "1) Dizziness and lack of coordination - Monitor clinically. Keep balance log. RTC with worsening symptoms.\r2) Gait and balance difficulties - Secondary to #1. Reinforced fall precautions.\r3) Headaches with migrainous features - Continue Nurtec PRN.",
                "--------PO Medications--------\rAmlodipine 5mg QD\rAtorvastatin 20mg QD\rMeclizine 25mg PRN\rNurtec 75mg ODT PRN", ["hist-hypt", "hist-dyslip", "hist-migraine"]),
            2: ("Vertigo", "1) Complex BPPV - Epley chair treatment today. Keep head greater than earth horizontal x 48h. RTC with acute vertigo.\r2) Dizziness and lack of coordination - Secondary to #1.",
                "--------PO Medications--------\rLisinopril 10mg QD\rMetformin 500mg BID", ["hist-hypt", "hist-diab"]),
            3: ("Imbalance", "1) Gait and balance difficulties - CDP and VNG/VEMP ordered. Strict fall precautions.\r2) Peripheral neuropathy - EMG/NCV BLE ordered.",
                "--------PO Medications--------\rGabapentin 300mg TID\rRosuvastatin 10mg QD", ["hist-dyslip", "hist-diab"]),
            4: ("Migraines", "1) Vestibular migraine - Continue migraine cocktail. HA log.\r2) Tinnitus AU - Tinnitus cocktail discussed.",
                "--------PO Medications--------\rTopamax 50mg BID\rMagnesium 500mg QHS\rRiboflavin 400mg QD", ["hist-migraine"]),
            5: ("Dizziness", "1) Presbyastasis - Vestibular PT 2x/week x 6 weeks. Balance log.\r2) SNHL AU - Audiology referral.\r3) Mild cognitive impairment - MoCA 25/30; follow clinically.",
                "--------PO Medications--------\rLevothyroxine 75mcg QD\rMemantine 10mg BID", ["hist-hypt"]),
        }
        today = date.today()
        for i, p in enumerate(DEMO_PATIENTS):
            cc, imp, meds, pmh = plans[p["id"]]
            files = []
            for k, back in enumerate((52, 150)):
                dos = today - timedelta(days=back + i * 3)
                v = note.blank()
                v.update({"Name": f"{p['last']}, {p['first']}", "DOB": p["dob"], "Date": dos.strftime("%m/%d/%Y"),
                          "HPI-age": str(note.age_on(note.parse_date(p["dob"]), dos)), "hpi-female": p["sex"] == "F", "hpi-male": p["sex"] == "M",
                          "neuro-consult": k == 1, "neuro-progress": k == 0, "nkda": True, "ChiefComplaint": cc, "OnsetDate": "2024",
                          "ChiefComplaint-descr": f"{'Ms.' if p['sex'] == 'F' else 'Mr.'} {p['last']} is a {note.age_on(note.parse_date(p['dob']), dos)} y/o RHD "
                                                  f"{'female' if p['sex'] == 'F' else 'male'} presenting to the clinic today for a follow-up on {cc.lower()}.\r\r"
                                                  "No other focal motor, sensory, or CN deficits reported at this time.",
                          "meds-descr": meds, "impression-plan-descr": imp, "appt-time": "40", "imp-mp": True,
                          "Imp/MP": note.FIELDS["Imp/MP"]["options"][0], "next-appt": "3-4", "next-appt-months": True, "nextappt-descr": "or sooner PRN",
                          "const-bp": "126/78", "const-hr": "68", "const-rr": "14", "WEIGHT": "150", "HEIGHT-ft": "5", "HEIGHT-in": "5", "BMI": "25.0", "TEMP": "98.0",
                          "x-study1": "CDP", "x-study1-date": (dos - timedelta(days=30)).strftime("%m/%d/%y"), "x-study1-result": "Vestibular integration deficit, w/ composite score 54. Rev/interp by TY/IMP",
                          "x-study2": "MRI Brain IAC w/o @ IHS", "x-study2-date": (dos - timedelta(days=90)).strftime("%m/%d/%y"), "x-study2-result": "No acute intracranial abnormality.",
                          "labs": "(per PCP)\rCBC w/ diff - WNL\rCMP - WNL\rHemoglobin A1C: 5.6", "dizziness": True, "worse-standing": True, "duration-seconds": True,
                          "course-parox": True, "ga-falls-no": True})
                for h in pmh:
                    v[h] = True
                path = os.path.join(self.notes_dir, note.filename(p["last"], p["first"], dos))
                if not os.path.exists(path):
                    note.fill(v, path)
                files.append(NoteFile(path, dos, "final"))
            self._notes[p["id"]] = files
        self._make_reports()

    def _make_reports(self):
        """A made-up EMG/NCV report for the patient with neuropathy, so past studies can be opened in the demo."""
        import pymupdf
        self.emg_dir = os.path.join(self.root, "EMG")
        os.makedirs(self.emg_dir, exist_ok=True)
        self._reports: dict[int, list[dict]] = {}
        p = next(x for x in DEMO_PATIENTS if x["id"] == 3)
        done = self._notes[3][1].dos - timedelta(days=21)
        path = os.path.join(self.emg_dir, f"{p['last']}, {p['first']} EMG-NCV BLE {done.strftime('%m-%d-%y')}.pdf")
        if not os.path.exists(path):
            lines = ["EMG / NERVE CONDUCTION STUDY - BILATERAL LOWER EXTREMITIES", "Made-up demo report. Not a real patient.", "",
                     f"Patient: {p['last']}, {p['first']}    DOB: {p['dob']}    Date of study: {done.strftime('%m/%d/%Y')}", "",
                     "Sural sensory responses: absent bilaterally.", "Superficial peroneal sensory responses: reduced amplitude bilaterally.",
                     "Peroneal and tibial motor responses: mildly reduced amplitudes, mildly slowed conduction velocities.",
                     "Needle EMG: chronic neurogenic changes in distal leg muscles bilaterally; no active denervation.", "",
                     "IMPRESSION: Length-dependent sensorimotor axonal polyneuropathy, mild to moderate.", "No electrodiagnostic evidence of lumbosacral radiculopathy."]
            with pymupdf.open() as doc:
                page = doc.new_page()
                page.insert_text((54, 72), "\n".join(lines), fontsize=10.5, lineheight=1.5)
                doc.save(path)
        self._reports[3] = [{"path": path, "name": os.path.basename(path), "folder": self.emg_dir, "size": os.path.getsize(path),
                             "kind": "EMG/NCV", "group": "Neuro testing", "modified": _mdY(done), "how": "demo"}]
        # A made-up Zeto EEG report for the migraine patient, filed since her last note: chart prep picks it up.
        self.zeto_dir = os.path.join(self.root, "Zeto Reports")
        os.makedirs(self.zeto_dir, exist_ok=True)
        p = next(x for x in DEMO_PATIENTS if x["id"] == 4)
        done = self._notes[4][0].dos + timedelta(days=12)
        path = os.path.join(self.zeto_dir, f"{p['last']}, {p['first']} Zeto EEG {done.strftime('%m-%d-%y')}.pdf")
        if not os.path.exists(path):
            lines = ["ZETO ROUTINE EEG REPORT", "Made-up demo report. Not a real patient.", "",
                     f"Patient: {p['last']}, {p['first']}    DOB: {p['dob']}    Date of study: {done.strftime('%m/%d/%Y')}", "",
                     "Recording: 24-channel dry-electrode routine EEG, awake and drowsy, 28 minutes, with hyperventilation and photic stimulation.",
                     "Background: 10 Hz posterior dominant rhythm, symmetric and reactive.", "",
                     "IMPRESSION: Normal awake and drowsy EEG. No epileptiform discharges or focal slowing.",
                     "Clinical correlation: A normal EEG does not exclude a clinical diagnosis of epilepsy."]
            with pymupdf.open() as doc:
                page = doc.new_page()
                page.insert_text((54, 72), "\n".join(lines), fontsize=10.5, lineheight=1.5)
                doc.save(path)
        self._reports[4] = [{"path": path, "name": os.path.basename(path), "folder": self.zeto_dir, "size": os.path.getsize(path),
                             "kind": "EEG", "group": "Neuro testing", "modified": _mdY(done), "how": "demo"}]

    def _find(self, q: dict) -> dict | None:
        for p in DEMO_PATIENTS:
            if str(p["id"]) == str(q.get("id", "")) or (q.get("last", "").lower() == p["last"].lower() and (not q.get("first") or q["first"].lower()[:2] == p["first"].lower()[:2])):
                return p
        return None

    def search_patients(self, q: str) -> list[dict]:
        ql = q.lower().strip()
        return [{**{k: p[k] for k in ("id", "last", "first", "acct", "dob")}, "last_visit": _mdY(self._notes[p["id"]][0].dos), "bookings": 9, "files": 12}
                for p in DEMO_PATIENTS if not ql or ql in f"{p['last']} {p['first']} {p['last']}, {p['first']} {p['acct']} {p['dob']}".lower()]

    def patient_notes(self, q: dict) -> list[NoteFile]:
        p = self._find(q)
        return list(self._notes.get(p["id"], [])) if p else []

    def today(self, day: date) -> list[dict]:
        tones = {1: [], 2: [{"text": "Aetna: expect E/M downcode, appeal with note", "tone": "amber"}], 3: [{"text": "SCMG auth needed for 92548 / VNG", "tone": "red"}],
                 4: [], 5: [{"text": "MIPS: falls screening open", "tone": "blue"}]}
        return [visit_kind({"id": f"demo-{p['id']}", "time": p["time"], "cal": p["cal"], "patient": True, "name": f"{p['last']}, {p['first']}", "last": p["last"], "first": p["first"],
                            "dob": p["dob"], "acct": p["acct"], "plan": p["ins"], "visit": p["visit"], "alerts": tones[p["id"]],
                            "tone": tones[p["id"]][0]["tone"] if tones[p["id"]] else "", "auth": "OK" if p["id"] != 3 else "No auth", "elig": "Verified" if p["id"] in (1, 2, 4) else "", "mips": 1 if p["id"] == 5 else 0})
                for p in DEMO_PATIENTS]

    def schedule(self, start: date, end: date) -> dict:
        days = []
        d = start
        while d < end:
            days.append(d)
            d += timedelta(days=1)
        providers = []
        calendars = sorted({p["cal"] for p in DEMO_PATIENTS})
        for cal in calendars:
            providers.append({"id": cal, "calendar": cal, "name": cal, "color": _provider_color(cal)})
        colors = {p["id"]: p["color"] for p in providers}
        rows = []
        # The made-up schedule follows the selected week so day/week/month controls can be tried on any date.
        for i, p in enumerate(DEMO_PATIENTS):
            day = days[i % len(days)] if days else start
            hour, minute = map(int, p["time"].split(":"))
            began = datetime(day.year, day.month, day.day, hour, minute)
            rows.append(visit_kind({"id": f"demo-{p['id']}", "date": day.isoformat(), "start": began.isoformat(timespec="minutes"),
                                    "end": (began + timedelta(minutes=45)).isoformat(timespec="minutes"), "time": p["time"],
                                    "cal": p["cal"], "provider": p["cal"], "color": colors[p["cal"]], "patient": True,
                                    "name": f"{p['last']}, {p['first']}", "last": p["last"], "first": p["first"], "dob": p["dob"],
                                    "acct": p["acct"], "plan": p["ins"], "visit": p["visit"], "alerts": [], "tone": "",
                                    "duplicate": False, "duplicate_count": 0, "duplicate_calendars": []}))
        if len(rows) >= 2:
            clone = dict(rows[0], id="demo-duplicate", cal=rows[1]["cal"], provider=rows[1]["cal"], color=colors[rows[1]["cal"]])
            rows.append(clone)
            for r in (rows[0], rows[-1]):
                r.update(duplicate=True, duplicate_count=2, duplicate_calendars=sorted({rows[0]["cal"], rows[-1]["cal"]}))
        return {"start": start.isoformat(), "end": end.isoformat(), "providers": providers, "rows": rows, "visit_types": visits.VISIT_TYPES}

    def chart(self, q: dict) -> dict:
        p = self._find(q)
        if not p:
            return {"patient": q, "facts": {}, "visits": [], "documents": [], "notes": [], "balance": None}
        files = self._notes[p["id"]]
        visits = [{"day": _mdY(n.dos), "iso": _iso(n.dos), "where": p["cal"], "tags": "Follow-up" if i == 0 else "New consult", "seen": True, "note": n.path, "note_kind": "final"} for i, n in enumerate(files)]
        visits.insert(0, {"day": _mdY(date.today()), "iso": _iso(date.today()), "where": p["cal"], "tags": p["visit"], "seen": False, "note": "", "note_kind": ""})
        docs = [{"path": n.path, "name": os.path.basename(n.path), "folder": self.notes_dir, "size": os.path.getsize(n.path), "kind": "Progress note", "group": "Progress notes",
                 "modified": _mdY(n.dos), "how": "demo"} for n in files]
        docs += [{"path": "", "name": f"{p['last']}, {p['first']} VNG report.pdf", "folder": "V:\\VNG", "size": 0, "kind": "VNG", "group": "Vestibular testing", "modified": _mdY(files[0].dos - timedelta(days=30)), "how": "demo"},
                 {"path": "", "name": f"{p['last']}, {p['first']} insurance card.pdf", "folder": "V:\\Faxes", "size": 0, "kind": "Fax", "group": "Faxes", "modified": _mdY(files[1].dos), "how": "demo"}]
        docs += self._reports.get(p["id"], [])
        return {"patient": {"id": p["id"], "last": p["last"], "first": p["first"], "dob": p["dob"], "acct": p["acct"]},
                "facts": {"phone": "(858) 555-0100", "insurance": p["ins"], "complaint": next(x for x in DEMO_PATIENTS if x is p)["visit"], "referral": "PCP"},
                "insurance": p["ins"], "bookings": 9, "seen": 8, "first_seen": _iso(files[-1].dos), "last_seen": _iso(files[0].dos),
                "visits": visits, "documents": docs, "notes": [{"path": n.path, "dos": _iso(n.dos), "kind": n.kind, "name": os.path.basename(n.path)} for n in files],
                "balance": {"total": "0.00"} if p["id"] != 2 else {"total": "118.40", "patient": "23.68", "insurance": "94.72"}}

    def snapshot(self, q: dict) -> dict:
        p = self._find(q)
        return snapshot_from_note(self._notes[p["id"]][0].path) if p else {}

    def status(self) -> dict:
        return {"clinic": "Demo clinic (made-up patients)", "bookings": 0, "synced": "", "demo": True, "initials": "DEMO"}
