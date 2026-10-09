"""Find a visit's superbill page in the scanned batches and pull it out as its own PDF.

Used wherever a patient + date of service is known (a billing request, a patient record),
so the superbill is "attached" without anyone opening the batch:
  Outlook (cached) -> the provider's calendar that day -> batch code + position in the day
  -> the batch PDF(s) -> OCR (cached) -> the patient's packet -> page saved as
  "<Last> <First> <CODE> <mm.dd.yy> SB.pdf" under %LOCALAPPDATA%\\DizzyDoctor\\attachments.
"""
from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass, field
from datetime import date

from . import amend, ocr, outlook_cache, rules, schedule, superbills
from .superbills import PatientQuery

MIN_SCORE = 30  # below this the match rests on schedule position alone - show it, but as "check me"


@dataclass
class Attached:
    path: str = ""                 # the extracted single-page PDF ("" when nothing was found)
    batch: str = ""
    page: int = 0                  # 1-based page in the batch
    score: int = 0
    reasons: list[str] = field(default_factory=list)
    code: str = ""
    notes: list[str] = field(default_factory=list)   # what was checked, for the status line
    candidates: list = field(default_factory=list)   # other possible pages (superbills.Match)

    @property
    def confident(self) -> bool:
        return bool(self.path) and self.score >= MIN_SCORE


def attachments_dir() -> str:
    from .config import DATA_DIR
    d = os.path.join(DATA_DIR, "attachments")
    os.makedirs(d, exist_ok=True)
    return d


def extract_page(batch_path: str, page_index: int, out_path: str) -> str:
    import pymupdf
    with pymupdf.open(batch_path) as src:
        out = pymupdf.open()
        out.insert_pdf(src, from_page=page_index, to_page=page_index)
        if out[0].rotation:
            out[0].remove_rotation()
        out.save(out_path, garbage=3, deflate=True)
        out.close()
    return out_path


def _visit_calendar(store, q: PatientQuery, dos: date, roster):
    """(batch code, the patient's appointment, that provider's seen patients in scan order, notes)."""
    notes: list[str] = []
    day = outlook_cache.day(store, dos)
    if not day and not schedule.outlook_unavailable_reason():
        try:
            day = schedule.read_day(dos)  # not cached yet (first run): ask Outlook directly
        except Exception:
            day = []
    if not day:
        notes.append("No Outlook bookings found for that day.")
        return "", None, [], notes
    hits = schedule.find_patient(day, q)
    if not hits:
        notes.append(f"Not on any Outlook calendar on {dos:%m/%d/%Y}.")
        return "", None, [], notes
    # the visit that happened: a seen booking on a provider calendar beats a no-show or a test slot
    hits.sort(key=lambda h: (not h[1].seen, rules.by_calendar(roster, h[1].calendar) is None, -h[0]))
    _s, appt, _why = hits[0]
    prov = rules.by_calendar(roster, appt.calendar)
    order = schedule.schedule_order(day, appt.calendar)
    notes.append(f"Outlook: {appt.calendar} {appt.when}")
    q.dob = q.dob or appt.info.get("dob")
    if appt.info.get("nickname"):
        q.nicknames.append(appt.info["nickname"])
    return (prov.code if prov else ""), appt, order, notes


def _day_patients(store, cfg, order, dos) -> tuple[list[PatientQuery], list[float]]:
    """The provider's seen patients as queries, weighted down when no progress note was filed (likely no-show)."""
    from .notes import matches_patient, notes_on
    filed = notes_on(store, cfg, dos)
    pats, weights = [], []
    for a in order:
        i = a.info
        ins_part = re.split(r"\b(?:Ref|Cc|CC|Email)\s*[:;]", a.subject)[0]  # not the referring doctor's group
        pats.append(PatientQuery(i.get("first", ""), i.get("last", ""), i.get("account", ""), i.get("dob"),
                                 [i["nickname"]] if i.get("nickname") else [],
                                 [x for x in (i.get("insurance", ""), i.get("secondary", ""), ins_part) if x]))
        has_note = any(matches_patient(who, i.get("last", ""), i.get("first", "")) for who, _p in filed)
        remote = any(re.search(r"telephone|phone consult|tele-?health|video|virtual", c, re.I) for c in a.categories)
        weights.append(0.25 if remote or (filed and not has_note) else 1.0)  # phone visits rarely get a paper sheet
    return pats, weights


def find_superbill(store, cfg: dict, last: str, first: str = "", dos: date | None = None, dob: date | None = None,
                   account: str = "", code: str = "", cancel: threading.Event | None = None, progress=None) -> Attached:
    res = Attached()
    root = cfg.get("superbill_root", "")
    if not (root and dos and (last or account)):
        res.notes.append("Need a patient and a date of service (and the superbill folder in Settings).")
        return res
    why = ocr.unavailable_reason()
    if why:
        res.notes.append(f"Can't read scanned batches: {why}.")
        return res
    roster = rules.roster_from(cfg)
    q = PatientQuery(first, last, account, dob)
    cal_code, appt, order, notes = _visit_calendar(store, q, dos, roster)
    res.notes += notes
    code = code or cal_code
    res.code = code
    idx = order.index(appt) if appt is not None and appt in order else None
    day_pats, weights = _day_patients(store, cfg, order, dos) if idx is not None else ([], [])
    batches = [b for b in superbills.find_batches(root, dos, code) if not amend.is_our_amendment(b.path)]
    if not batches and code:
        batches = [b for b in superbills.find_batches(root, dos) if not amend.is_our_amendment(b.path)]
    # an office-amended copy of the batch holds the corrected pages: prefer it for the same code
    batches.sort(key=lambda b: (superbills.norm_code(b.code) != superbills.norm_code(code), not b.amended))
    if not batches:
        res.notes.append(f"No scanned batch for {dos:%m/%d/%y}{' ' + code if code else ''} yet.")
        return res
    matches = []
    for b in batches[:4]:
        if cancel is not None and cancel.is_set():
            return res
        if progress:
            progress(f"Reading {b.name}…")
        try:
            pages = ocr.ocr_pdf(b.path, store, cancel=cancel)
        except InterruptedError:
            return res
        except Exception as ex:
            res.notes.append(f"Couldn't read {b.name}: {ex}")
            continue
        texts = [ocr.text_of(p) for p in pages]
        same = idx is not None and superbills.norm_code(b.code) == superbills.norm_code(code)
        if same:
            me = day_pats[idx]  # merge what the caller knows (DOB, account) into the booking's details
            me.dob, me.account = me.dob or q.dob, me.account or q.account
            me.nicknames = list(dict.fromkeys(me.nicknames + q.nicknames))
            q.payers = q.payers or me.payers
            placed = superbills.align_day(texts, day_pats, weights, groups=[a.start.isoformat() for a in order])
            pl = placed[idx]
            if pl.packet is not None:
                _cover, packets = superbills.split_packets(texts)
                named = lambda k: f"{day_pats[k].last}" if k is not None else ""
                prev_a = next((placed[k] for k in range(idx - 1, -1, -1) if placed[k].how == "printed"), None)
                next_a = next((placed[k] for k in range(idx + 1, len(placed)) if placed[k].how == "printed"), None)
                if pl.how == "printed":
                    score, why = 60 + pl.evidence, superbills.score_packet(texts, pl.packet, q)[1]
                elif pl.how == "tie":
                    score = 15
                    mates = [day_pats[k].last for k, a in enumerate(order) if a.start == order[idx].start and k != idx]
                    why = [f"booked at the same time as {', '.join(mates)} and nothing printed tells them apart - check the name"]
                else:
                    anchored = prev_a is not None and next_a is not None
                    if anchored:
                        # only pinned if the sheets between the two anchors match the patients between them
                        between_pats = next_a.patient - prev_a.patient - 1
                        between_sheets = sum(1 for p in packets if prev_a.packet.superbill < p.superbill < next_a.packet.superbill)
                        anchored = between_pats == between_sheets
                    score = 45 if anchored else 15
                    why = [f"placed in schedule order between {named(prev_a.patient) if prev_a else 'start'} "
                           f"(p{prev_a.packet.superbill + 1 if prev_a else '-'}) and {named(next_a.patient) if next_a else 'end'} "
                           f"(p{next_a.packet.superbill + 1 if next_a else '-'})"]
                    skipped = [day_pats[k].last for k, p in enumerate(placed) if p.packet is None]
                    if skipped:
                        why.append("no superbill for: " + ", ".join(skipped))
                matches.append(superbills.Match(b, pl.packet, score, why, len(packets)))
            else:
                res.notes.append(f"{b.name}: no superbill lines up with this patient (missing sheet?).")
        else:
            matches += superbills.best_matches(b, texts, q, None)
        if matches and matches[0].score >= MIN_SCORE + 40:
            break  # printed name + DOB matched: no need to read more batches
    matches.sort(key=lambda m: (-m.score, m.batch.amended is False))
    if not matches:
        res.notes.append("Patient's superbill not found in the batch.")
        return res
    best = matches[0]
    res.batch, res.page, res.score, res.reasons = best.batch.path, best.page, best.score, best.reasons
    res.candidates = matches[1:6]
    name = amend.billing_name(best.batch.code, dos, last or "Patient", first)
    res.path = extract_page(best.batch.path, best.packet.superbill, os.path.join(attachments_dir(), name))
    res.notes.append(f"Superbill: {best.batch.name} p{best.page} ({'; '.join(best.reasons)})")
    return res
