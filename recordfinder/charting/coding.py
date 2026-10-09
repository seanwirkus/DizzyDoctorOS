"""Billing suggestions from a finished note.

The office codes office visits by total time (2021 E/M rules) and documents separate time for
procedures on the note itself, so most of this is arithmetic over what the note says, plus the
checks a coder would make before the superbill goes out. Everything is a suggestion with its
reason; the provider and billing decide. Items marked "verify" follow the clinic playbook and
should be confirmed by the practice's coder.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import phrases, visits
from .note import impressions

# Office/outpatient E/M by total time on the date of service (CPT 2021+). (code, minimum minutes)
NEW_PATIENT = [("99202", 15), ("99203", 30), ("99204", 45), ("99205", 60)]
ESTABLISHED = [("99212", 10), ("99213", 20), ("99214", 30), ("99215", 40)]
PROLONGED_FROM = {"99205": 75, "99215": 55}          # 99417 for each 15 min beyond the top code's range (commercial)
PROLONGED_MEDICARE_FROM = {"99205": 89, "99215": 69}  # G2212 (Medicare counts from the end of the top code's range)

SEPARATE_TIME_CODES = [
    # (pattern in the separate-time statement, codes, note)
    (r"oculography|epley chair|IR/ocul", ["92542"],
     "Positional nystagmus test with recording (IR oculography). Add 95992 when a repositioning maneuver was done. (verify)"),
    (r"injection", ["64405"], "Nerve block / injection: 64405 occipital nerve block (-50 when both sides), 20552/20553 trigger "
                              "points, 64450 other peripheral nerve. Pick by the procedure note. (verify)"),
    (r"cerumen|keratinous", ["69210"], "Removal of impacted cerumen, instrumentation (-50 when both ears). (verify)"),
    (r"inspire", ["95970"], "Neurostimulator interrogation/programming: 95970 analysis without programming, 95976/95977 with "
                            "programming. (verify)"),
    (r"moca", [], "MoCA documented with separate time; code per the practice's policy (not part of the E/M time). (verify)"),
]


@dataclass
class Suggestion:
    code: str
    title: str
    why: str
    modifier: str = ""
    verify: bool = False


@dataclass
class Coding:
    visit: str = ""                       # "new" / "established" / ""
    minutes: int = 0
    em: Suggestion | None = None
    next_level: str = ""                  # "99215 starts at 40 minutes"
    addons: list[Suggestion] = field(default_factory=list)
    procedures: list[Suggestion] = field(default_factory=list)
    icd: list[tuple[str, str]] = field(default_factory=list)   # (impression text, ICD-10 or "")
    warnings: list[str] = field(default_factory=list)
    attending: str = ""
    kind: str = ""                        # the visit type on the note (visits.py), "" when none is chosen

    def as_dict(self) -> dict:
        sug = lambda s: None if s is None else {"code": s.code, "title": s.title, "why": s.why, "modifier": s.modifier, "verify": s.verify}  # noqa: E731
        return {"visit": self.visit, "minutes": self.minutes, "em": sug(self.em), "next_level": self.next_level,
                "addons": [sug(s) for s in self.addons], "procedures": [sug(s) for s in self.procedures],
                "icd": [{"impression": i, "code": c} for i, c in self.icd], "warnings": self.warnings,
                "attending": self.attending, "kind": self.kind}


def _minutes(text: str) -> int:
    m = re.search(r"\d+", text or "")
    return int(m.group()) if m else 0


def em_by_time(new_patient: bool, minutes: int) -> tuple[Suggestion | None, str]:
    table = NEW_PATIENT if new_patient else ESTABLISHED
    chosen = None
    for code, floor in table:
        if minutes >= floor:
            chosen = (code, floor)
    if not chosen:
        floor = table[0][1]
        return None, f"{table[0][0]} starts at {floor} minutes of total time on the date of service."
    code, floor = chosen
    higher = [(c, f) for c, f in table if f > floor]
    nxt = f"{higher[0][0]} starts at {higher[0][1]} minutes." if higher else ""
    kind = "new patient" if new_patient else "established patient"
    return Suggestion(code, f"Office visit, {kind}", f"{minutes} minutes total time documented."), nxt


def icd_for(diagnosis: str) -> str:
    d = diagnosis.lower().strip().rstrip(".")
    for name, code in phrases.DIAGNOSES:
        if name.lower() == d:
            return code
    for name, code in phrases.DIAGNOSES:
        n = name.lower()
        if n in d or d in n:
            return code
    return ""


def code_note(values: dict, payer: str = "") -> Coding:
    c = Coding()
    v = values
    # the visit type chosen on the note (schedule, Scope or the provider) decides the E/M family; its form box agrees
    c.kind, _source = visits.kind_of(v)
    fam = visits.family(v)
    new_patient = fam == "new" or (not fam and bool(v.get("neuro-consult") or v.get("neuro-eval")))
    established = fam == "established" or (not fam and bool(v.get("neuro-progress") or v.get("neuro-urgent")))
    c.visit = "new" if new_patient else ("established" if established else "")
    if not c.visit:
        c.warnings.append("No visit type chosen (New patient / Consult, Evaluation, Follow-up, Procedure, Results, Telehealth or Urgent).")
    c.minutes = _minutes(str(v.get("appt-time", "")))
    medicare = "medicare" in payer.lower()

    # ---- E/M level from total time
    if c.minutes:
        c.em, c.next_level = em_by_time(new_patient, c.minutes)
        top = "99205" if new_patient else "99215"
        if c.em and c.em.code == top:
            prolonged_from = (PROLONGED_MEDICARE_FROM if medicare else PROLONGED_FROM)[top]
            if c.minutes >= prolonged_from:
                units = 1 + (c.minutes - prolonged_from) // 15
                code = "G2212" if medicare else "99417"
                c.addons.append(Suggestion(code, f"Prolonged service x{units}",
                                           f"{c.minutes} minutes documented; {code} applies from {prolonged_from} minutes."))
    else:
        c.warnings.append("Total encounter time is blank; the visit level is based on total time on the date of service.")

    # ---- separate-time statements (procedures outside the E/M time)
    lines = []
    for box, choice in (("time-proc1", "addnl-time"), ("time-proc2", "addnl-time-2")):
        text = str(v.get(choice, "") or "")
        if v.get(box) and text.strip():
            lines.append(text)
        elif text.strip() and not v.get(box) and "longitudinal" not in text.lower():
            c.warnings.append(f"Separate-time statement is filled in but its box isn't checked: \"{text[:60]}\".")
        elif v.get(box) and not text.strip():
            c.warnings.append("A separate-time box is checked with no statement selected.")
    longitudinal = any("longitudinal" in t.lower() for t in lines) or any(
        "longitudinal" in str(v.get(k, "")).lower() for k in ("addnl-time", "addnl-time-2"))
    for text in lines:
        for pattern, codes, note in SEPARATE_TIME_CODES:
            if re.search(pattern, text, re.I):
                for code in codes:
                    c.procedures.append(Suggestion(code, text, note, verify=True))
                if not codes:
                    c.warnings.append(note)
    treatment = str(v.get("goggle-treatment-x", "")).lower()
    bppv = any(v.get(k) for k in ("imp-rightpost", "imp-leftpost", "imp-rightcupu", "imp-leftcupu", "imp-leftlateral",
                                  "imp-rightlateral", "imp-leftanterior", "imp-rightanterior"))
    if re.search(r"epley|maneuver|reposition|semont|bbq|gufoni|crp", treatment) or (bppv and treatment and "no treatment" not in treatment):
        c.procedures.append(Suggestion("95992", "Canalith repositioning procedure",
                                       "A repositioning maneuver is documented in the oculography section.", verify=True))
    if c.procedures and c.em:
        c.em.modifier = "25"
        c.warnings.append("E/M with a same-day procedure: append modifier 25 to the visit code and keep the procedure "
                          "time out of the E/M total (the note's separate-time lines do this).")

    # ---- missed procedures: documented but no separate-time line
    goggles_done = bool(v.get("goggle-dr") or v.get("goggle-date")) or any(
        v.get(k) for k in v if k.startswith(("dixhall-", "supine-", "gaze-", "imp-")) and k != "imp-mp" and v.get(k))
    if goggles_done and not any(re.search(r"oculography|epley chair", t, re.I) for t in lines):
        c.warnings.append("IR oculography / positional testing is documented on page 3 but there is no separate-time "
                          "statement for it on page 4 (15 minutes IR/oculography).")
    if (v.get("debris-kerat") or v.get("debris-cerumen")) and not any(re.search(r"cerumen|keratinous", t, re.I) for t in lines):
        c.warnings.append("Cerumen / keratinous debris is marked on the exam but there is no separate-time statement for "
                          "its removal (8 minutes).")
    plan_text = (str(v.get("impression-plan-descr", "")) + " " + str(v.get("plan-descr", ""))).lower()
    if re.search(r"\b(injection|nerve block|trigger point)\b.*\b(today|performed|done|administered)\b", plan_text) and not any(
            "injection" in t.lower() for t in lines):
        c.warnings.append("The plan describes an injection today but there is no separate-time statement for injection procedures.")

    # ---- what the visit type promises
    if c.kind == "telehealth":
        c.warnings.append("Telehealth visit: bill the E/M with modifier 95 (audio-video) or 93 (audio only) and the telehealth "
                          "place of service the payer wants. (verify)")
    if c.kind == "procedure" and not c.procedures:
        c.warnings.append("Scheduled as a procedure visit, but the note documents no procedure (no separate-time line, no "
                          "repositioning treatment). Document it, or change the visit type.")
    if c.kind == "results" and not any(str(v.get(f"x-study{i}", "")).strip() for i in range(1, 6)):
        c.warnings.append("Scheduled as a results visit, but no study is listed under Imaging / studies.")

    # ---- G2211
    if longitudinal:
        if new_patient:
            c.warnings.append("The longitudinal (G2211) statement is on a new-patient visit; G2211 needs an established, "
                              "ongoing relationship.")
        elif c.procedures:
            c.warnings.append("G2211 is not paid on a visit billed with modifier 25 for a same-day procedure (verify "
                              "current CMS rule). Choose between the add-on and the procedure.")
        else:
            c.addons.append(Suggestion("G2211", "Visit complexity add-on",
                                       "Longitudinal-care statement documented on an established-patient visit."))
    elif established and not c.procedures and medicare:
        c.warnings.append("Established Medicare visit with no procedure: the longitudinal (G2211) statement would add "
                          "the complexity add-on if this is the patient's continuing care.")

    # ---- attestation / who bills
    attest = str(v.get("Imp/MP", "")).strip()
    if v.get("imp-mp") and attest:
        who = "Dr. Purcell" if "purcell" in attest.lower() else ("Dr. Patel" if "patel" in attest.lower() else "attending")
        c.attending = who
        if new_patient:
            c.warnings.append(f"New-patient visit with an incident-to attestation ({who}): incident-to doesn't apply to "
                              "new patients or new problems. Bill under the rendering PA, or the physician performs the visit.")
    elif established and not v.get("imp-mp"):
        c.warnings.append("No attending attestation: a PA visit billed under the physician (incident-to) needs the "
                          "physician present in the office and the care plan established.")
    elif v.get("imp-mp") and not attest:
        c.warnings.append("The attestation box is checked but no attending statement is selected.")

    # ---- diagnoses
    for imp in impressions(str(v.get("impression-plan-descr", ""))):
        c.icd.append((f"{imp.number}) {imp.diagnosis}", icd_for(imp.diagnosis)))
    if not c.icd:
        c.warnings.append("No numbered impressions found (\"1) Diagnosis - plan\").")

    # ---- documentation checks
    if v.get("plan-adverse") and not str(v.get("plan-adverse-descr", "")).strip():
        c.warnings.append("\"We discussed the indications / adverse effects of\" is checked without a medication named.")
    if not str(v.get("Date", "")).strip():
        c.warnings.append("Date of service is blank.")
    if not str(v.get("next-appt", "")).strip():
        c.warnings.append("Next appointment is blank.")
    holes = [label for name, label in (("ChiefComplaint-descr", "HPI"), ("impression-plan-descr", "impression / plan"),
                                       ("plan-descr", "plan notes")) if "***" in str(v.get(name, ""))]
    if holes:
        c.warnings.append(f"Placeholders (***) are still in the {' and '.join(holes)}; fill them in before filing.")
    if c.minutes and c.em and c.next_level:
        c.next_level = c.next_level
    return c
