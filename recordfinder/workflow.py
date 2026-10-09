"""The superbill workflow: from a billing request to a proposed amendment, with the evidence.

    request (acct / patient / DOS / batch code)
      -> Outlook: the patient's appointment that day (provider, time, insurance, tags)
      -> the day's scanned batch -> OCR -> the patient's packet -> the superbill page
      -> rules: rendering provider + attending physician (+ Medicare?)
      -> a person reviews it, then amend.py marks the page and files the copies

Every step records what it checked in `notes`, so a missing piece (Outlook closed, batch not
scanned yet, OCR unavailable) shows up as an explanation instead of a silent failure.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import date

from . import amend, ocr, rules, schedule, superbills
from .superbills import PatientQuery


@dataclass
class Investigation:
    query: PatientQuery
    dos: date | None
    code: str
    notes: list[str] = field(default_factory=list)
    appointment: schedule.Appointment | None = None
    appointments: list = field(default_factory=list)
    batches: list = field(default_factory=list)
    matches: list = field(default_factory=list)          # superbills.Match, best first
    texts: dict = field(default_factory=dict)            # batch path -> page texts
    rendering: rules.Provider | None = None
    decision: rules.Decision | None = None
    previous: list = field(default_factory=list)         # our earlier amendments for this visit

    @property
    def best(self):
        return self.matches[0] if self.matches else None


def query_from_item(item: dict) -> tuple[PatientQuery, date | None, str]:
    dob = date.fromisoformat(item["dob"]) if item.get("dob") else None
    dos = date.fromisoformat(item["dos"][0]) if item.get("dos") else None
    return PatientQuery(item.get("first", ""), item.get("last", ""), item.get("account", ""), dob), dos, item.get("visit_code", "")


def investigate(item: dict, cfg: dict, store=None, progress=lambda msg: None,
                cancel: threading.Event | None = None) -> Investigation:
    q, dos, code = query_from_item(item)
    inv = Investigation(q, dos, code)
    roster = rules.roster_from(cfg)
    if dos is None:
        inv.notes.append("No date of service in the request - enter one and try again.")
        return inv
    if store is not None and q.account:
        inv.previous = store.amendments_for(q.account, dos.isoformat())

    # 1. Outlook schedule
    why = schedule.outlook_unavailable_reason()
    if why:
        inv.notes.append(f"Schedule not checked: {why}.")
    else:
        progress("Reading the Outlook schedule…")
        try:
            inv.appointments = schedule.read_day(dos)
        except Exception as ex:  # Outlook closed, profile prompt, etc.
            inv.notes.append(f"Couldn't read Outlook: {ex}")
    if inv.appointments:
        hits = schedule.find_patient(inv.appointments, q)
        if hits:
            _s, appt, why_list = hits[0]
            inv.appointment = appt
            inv.notes.append(f"Schedule: {appt.calendar} {appt.when} ({', '.join(why_list)} matched)")
            info = appt.info
            q.dob = q.dob or info.get("dob")
            q.first = q.first or info.get("first", "")
            if info.get("nickname"):
                q.nicknames.append(info["nickname"])
            prov = rules.by_calendar(roster, appt.calendar)
            if prov and not code:
                code = inv.code = prov.code
        else:
            inv.notes.append(f"Patient not found on any Outlook calendar for {dos:%m/%d/%Y}.")

    # 2. Scanned batch -> superbill page
    root = cfg.get("superbill_root", "")
    if not root:
        inv.notes.append("Superbill folder isn't set (Settings).")
        return _decide(inv, roster, cfg)
    why = ocr.unavailable_reason()
    if why:
        inv.notes.append(f"Can't read scanned batches: {why}.")
        return _decide(inv, roster, cfg)
    batches = [b for b in superbills.find_batches(root, dos, code) if not amend.is_our_amendment(b.path)]
    if not batches and code:
        batches = [b for b in superbills.find_batches(root, dos) if not amend.is_our_amendment(b.path)]
        if batches:
            inv.notes.append(f"No {code} batch for {dos:%m/%d/%y}; searching every batch that day.")
    inv.batches = batches
    if not batches:
        inv.notes.append(f"No scanned batch found for {dos:%m/%d/%y} under {root}.")
        return _decide(inv, roster, cfg)

    order_index = None
    if inv.appointment is not None:
        seen = schedule.schedule_order(inv.appointments, inv.appointment.calendar)
        if inv.appointment in seen:
            order_index = seen.index(inv.appointment)
    for b in batches:
        if cancel is not None and cancel.is_set():
            break
        progress(f"Reading {b.name}…")
        try:
            pages = ocr.ocr_pdf(b.path, store, progress=lambda i, n, name=b.name: progress(f"Reading {name} (page {i} of {n})…"),
                                cancel=cancel)
        except InterruptedError:
            break
        except Exception as ex:
            inv.notes.append(f"Couldn't read {b.name}: {ex}")
            continue
        texts = [ocr.text_of(p) for p in pages]
        inv.texts[b.path] = texts
        same_provider = order_index is not None and superbills.norm_code(b.code) == superbills.norm_code(code)
        inv.matches += superbills.best_matches(b, texts, q, order_index if same_provider else None)
    inv.matches.sort(key=lambda m: -m.score)
    if inv.best:
        m = inv.best
        inv.notes.append(f"Superbill: {m.batch.name}, page {m.page} ({'; '.join(m.reasons)})")
    else:
        inv.notes.append("Couldn't find the patient's superbill in the batch(es).")
    return _decide(inv, roster, cfg)


def _decide(inv: Investigation, roster: list, cfg: dict) -> Investigation:
    code = inv.code or (inv.best.batch.code if inv.best else "")
    inv.rendering = rules.by_code(roster, code) if code else None
    if inv.rendering is None and inv.appointment is not None:
        inv.rendering = rules.by_calendar(roster, inv.appointment.calendar)
    appt = inv.appointment
    notes, busy = [], {}
    for a in inv.appointments:
        if not a.is_patient and a.subject.strip():
            notes.append(a.subject.strip())
        elif a.is_patient and a.seen:
            busy[a.calendar] = busy.get(a.calendar, 0) + 1
    referral = []
    if inv.best:
        referral = superbills.referral_physicians(inv.texts.get(inv.best.batch.path, []), inv.best.packet)
    inv.decision = rules.decide(
        inv.rendering, roster, cfg.get("attending_rules") or {},
        payer=appt.info.get("insurance", "") if appt else "", medicare=appt.medicare if appt else False,
        categories=appt.categories if appt else [], day_notes=notes, physician_patients=busy, referral_names=referral)
    return inv
