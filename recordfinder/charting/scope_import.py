"""Import a Scope-generated note as reviewable progress-note proposals.

Scope is deliberately treated as an untrusted draft source.  This module accepts a stable,
human-readable template (``DIZZYDOCTOR SCOPE TRANSFER V1``), validates its headings, and turns
only recognizable facts into the same Proposal objects used by Build.  It never writes to a note.

Clinical fields with a material risk if misplaced (medications, allergies, exam, studies,
assessment/plan, orders, time and attestation) are marked ``requires_confirmation`` so the Build
screen starts them unchecked.  Unrecognized or ambiguous lines are returned under ``unclear``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import dictation, story, visits
from .compose import Proposal, _text, boxes_proposal
from .note import FIELDS

VERSION = "DIZZYDOCTOR SCOPE TRANSFER V1"


def _key(text: str) -> str:
    text = re.sub(r"^[#>*\-\s]+|[*_`]+", "", text or "")
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


ALIASES = {
    "visit type": "visit_type",
    "chief complaint": "chief_complaint",
    "history of present illness": "hpi",
    "hpi": "hpi",
    "dizziness vertigo": "dizziness",
    "dizziness": "dizziness",
    "vertigo": "dizziness",
    "hearing": "hearing",
    "hearing loss": "hearing",
    "tinnitus": "tinnitus",
    "gait falls": "gait",
    "gait and falls": "gait",
    "gait": "gait",
    "headache": "headache",
    "past medical history": "pmh",
    "medical history": "pmh",
    "family social history": "family_social",
    "family and social history": "family_social",
    "family history": "family",
    "social history": "social",
    "medications allergies": "meds_allergies",
    "medications and allergies": "meds_allergies",
    "medications": "medications",
    "allergies": "allergies",
    "review of systems": "ros",
    "labs": "labs",
    "vitals": "vitals",
    "neurologic exam": "exam",
    "neurological exam": "exam",
    "exam": "exam",
    "positional oculography findings": "goggles",
    "positional and oculography findings": "goggles",
    "oculography": "goggles",
    "tests studies": "studies",
    "tests and studies": "studies",
    "studies": "studies",
    "assessment": "assessment",
    "impression": "assessment",
    "plan orders referrals": "plan_orders_referrals",
    "plan orders and referrals": "plan_orders_referrals",
    "plan": "plan",
    "orders": "orders",
    "referrals": "referrals",
    "follow up": "followup",
    "time attestation": "time_attestation",
    "time and attestation": "time_attestation",
    "time": "time",
    "attestation": "attestation",
    "needs review": "needs_review",
}

EMPTY = re.compile(r"^\s*(?:\[?\s*)?(?:not documented|not discussed|unknown|none stated|n/?a|leave blank)(?:\s*\]?)?[.\s]*$", re.I)
VISIT_TYPES = {
    "new consultation": "neuro-consult",
    "consultation": "neuro-consult",
    "consult": "neuro-consult",
    "neurologic evaluation": "neuro-eval",
    "neurological evaluation": "neuro-eval",
    "evaluation": "neuro-eval",
    "progress follow up": "neuro-progress",
    "progress": "neuro-progress",
    "follow up": "neuro-progress",
    "urgent visit": "neuro-urgent",
    "urgent": "neuro-urgent",
}


@dataclass
class ScopeDocument:
    sections: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    version: str = VERSION


def _heading(line: str) -> tuple[str, str] | None:
    """Return (canonical heading, inline value) for a Scope template heading."""
    raw = line.strip()
    if not raw:
        return None
    markdown = bool(re.match(r"^#{1,6}\s+", raw) or re.match(r"^\*\*.+\*\*\s*:?", raw))
    raw = re.sub(r"^#{1,6}\s*", "", raw)
    raw = raw.strip().strip("*_`").strip()
    before, sep, after = raw.partition(":")
    letters = re.sub(r"[^A-Za-z]+", "", before if sep else raw)
    # A plain body line such as "Dizziness" or "Tinnitus" must not become a new section.
    # Scope's template headings are uppercase, Markdown headings, bold headings, or end in a colon.
    if not (markdown or sep or (letters and letters == letters.upper())):
        return None
    canonical = ALIASES.get(_key(before))
    if canonical:
        return canonical, after.strip() if sep else ""
    canonical = ALIASES.get(_key(raw))
    return (canonical, "") if canonical else None


def parse(text: str) -> ScopeDocument:
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        raise ValueError("Paste the note copied from Scope.")
    current = ""
    collected: dict[str, list[str]] = {}
    preamble: list[str] = []
    for line in text.split("\n"):
        if _key(line) == _key(VERSION):
            continue
        head = _heading(line)
        if head:
            current, inline = head
            collected.setdefault(current, [])
            if inline:
                collected[current].append(inline)
            continue
        if current:
            collected[current].append(line.rstrip())
        elif line.strip():
            preamble.append(line.strip())
    sections = {k: "\n".join(v).strip() for k, v in collected.items() if "\n".join(v).strip()}
    if len(sections) < 2:
        raise ValueError("This does not look like a DizzyDoctor Scope note. Keep the template's section headings when copying it.")
    warnings = []
    if preamble and _key(" ".join(preamble)) != _key(VERSION):
        warnings.append("Text before the first recognized heading was not imported: " + " ".join(preamble)[:180])
    return ScopeDocument(sections=sections, warnings=warnings)


def _content(doc: ScopeDocument, *names: str) -> str:
    parts = [doc.sections.get(n, "").strip() for n in names]
    return "\n".join(p for p in parts if p and not EMPTY.match(p)).strip()


def _text_proposal(pid: str, title: str, field_name: str, text: str, values: dict,
                   *, confirm: bool = False, reason: str = "") -> Proposal | None:
    text = text.strip()
    current = _text(values.get(field_name)).strip()
    if not text or text == current:
        return None
    return Proposal(pid, title, "From the matching section of the Scope note.", {field_name: text},
                    field=field_name, text=text, current=current, requires_confirmation=confirm,
                    confirmation_reason=reason)


def _line_items(text: str) -> list[str]:
    return [re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", line).strip()
            for line in text.splitlines() if line.strip()]


def _split_columns(line: str) -> list[str]:
    if "|" in line:
        return [p.strip() for p in line.split("|")]
    return [p.strip() for p in re.split(r"\s+[—–]\s+", line)]


def _studies(text: str, unclear: list[str]) -> dict:
    changes = {}
    for i, line in enumerate(_line_items(text), 1):
        if i > 5:
            unclear.append(f"Tests/studies has more than five entries; not placed: “{line}”")
            continue
        parts = _split_columns(line)
        if len(parts) != 3 or not all(parts):
            unclear.append(f"Study needs ‘Study | Date | Result’ before it can be placed: “{line}”")
            continue
        changes[f"x-study{i}"], changes[f"x-study{i}-date"], changes[f"x-study{i}-result"] = parts
    return changes


def _referrals(text: str, unclear: list[str]) -> dict:
    changes = {}
    for i, line in enumerate(_line_items(text), 1):
        if i > 2:
            unclear.append(f"Referrals has more than two entries; not placed: “{line}”")
            continue
        parts = _split_columns(line)
        if len(parts) != 3 or not all(parts):
            unclear.append(f"Referral needs ‘Clinician | Specialty | Diagnosis’ before it can be placed: “{line}”")
            continue
        changes[f"referral{i}-doc"], changes[f"referral{i}-spec"], changes[f"referral{i}-diag"] = parts
    return changes


def _orders(text: str, unclear: list[str]) -> dict:
    changes, slot = {}, 1
    options = FIELDS["Imaging"]["options"]
    normalized = {_key(x): x for x in options}
    for line in _line_items(text):
        raw = re.sub(r"^(?:imaging|order)\s*:\s*", "", line, flags=re.I).strip()
        if re.match(r"^pt\s*:\s*", raw, re.I):
            changes["PTcheck"] = True
            changes["referral-for"] = re.sub(r"^pt\s*:\s*", "", raw, flags=re.I).strip()
            continue
        match = normalized.get(_key(raw))
        if match and slot <= 2:
            changes["Imaging" if slot == 1 else "Imaging-2"] = match
            slot += 1
        elif raw:
            unclear.append(f"Order does not exactly match a progress-note choice: “{line}”")
    return changes


def _attestation(text: str, unclear: list[str]) -> dict:
    if not text or EMPTY.match(text):
        return {}
    wanted = _key(text)
    for option in FIELDS["Imp/MP"].get("options", []):
        if _key(option) == wanted or _key(option) in wanted:
            return {"imp-mp": True, "Imp/MP": option}
    unclear.append("Attestation did not exactly match an approved statement and was not placed: “" + " ".join(text.split())[:180] + "”")
    return {}


def _changes_proposal(pid: str, title: str, changes: dict, values: dict, reason: str) -> Proposal | None:
    changes = {k: v for k, v in changes.items() if k in FIELDS and v not in (None, "") and values.get(k) != v}
    if not changes:
        return None
    lines = [f"{FIELDS[k].get('label') or k}: {v}" for k, v in changes.items()]
    return Proposal(pid, title, "Extracted from the matching Scope section.", changes, lines=lines,
                    requires_confirmation=True, confirmation_reason=reason)


def import_note(text: str, values: dict | None = None) -> dict:
    values = values or {}
    doc = parse(text)
    proposals: list[Proposal] = []
    unclear = list(doc.warnings)
    notes = ["Scope transfer V1", f"Recognized {len(doc.sections)} sections"]

    visit = _content(doc, "visit_type")
    if visit:
        target = VISIT_TYPES.get(_key(visit))
        # the template's own words first ("Progress / Follow-Up"); then the clinic's visit kinds (a telehealth or
        # procedure visit prints as a progress note but is kept as what it is)
        kind = visits.KIND_OF_BOX[target] if target else visits.classify(visit)
        if kind:
            changes = visits.changes_for(kind, "scope")
            p = Proposal("scope-visit", "Visit type", "From the Scope template selected for this visit.", changes,
                         lines=[visits.describe(kind)["label"]])
            proposals.append(p)
        else:
            unclear.append(f"Visit type was not recognized: “{' '.join(visit.split())}”")

    for p in (
        _text_proposal("scope-cc", "Chief complaint from Scope", "ChiefComplaint", _content(doc, "chief_complaint"), values),
        _text_proposal("scope-hpi", "History of present illness from Scope", "ChiefComplaint-descr", _content(doc, "hpi"), values),
        _text_proposal("scope-labs", "Labs from Scope", "labs", _content(doc, "labs"), values, confirm=True,
                       reason="Verify every result and date against the source record."),
    ):
        if p:
            proposals.append(p)

    readings = []
    hpi = _content(doc, "hpi")
    if hpi:
        readings.append(story.read_notes(hpi, topics=story.topics_of(values)))
    for name, topic in (("dizziness", "dizziness"), ("hearing", "hearing"), ("tinnitus", "tinnitus"),
                        ("gait", "gait"), ("headache", "headache")):
        body = _content(doc, name)
        if body:
            readings.append(story.read_notes(body, topics=[topic]))
    history = _content(doc, "pmh", "family_social", "family", "social")
    if history:
        readings.append(story.read_notes(history))
    if readings:
        wanted, whys = {}, {}
        reading_unclear, unfinished = [], []
        for reading in readings:
            for k, v in reading.set.items():
                if k == "HPI-age":
                    continue
                twin = story._twin(k)
                if v is True and twin in FIELDS and wanted.get(twin) is True:
                    wanted.pop(twin, None)
                    wanted.pop(k, None)
                    reading_unclear.append(f"Scope sections conflict about {FIELDS[k].get('label') or k}; neither box was selected.")
                    continue
                wanted[k] = v
                whys.setdefault(k, reading.why.get(k, ""))
            reading_unclear += reading.unclear
            if reading.unfinished:
                unfinished.append(reading.unfinished)
        # Reject mutually exclusive choices if the source/parser produced more than one.
        exclusive: dict[object, list[str]] = {}
        for k, v in wanted.items():
            group = FIELDS.get(k, {}).get("exclusive")
            if v is True and group is not None:
                exclusive.setdefault(group, []).append(k)
        for names in exclusive.values():
            if len(names) > 1:
                for k in names:
                    wanted.pop(k, None)
                reading_unclear.append("Scope produced mutually exclusive choices; none were selected: " +
                                       ", ".join(FIELDS[k].get("label") or k for k in names) + ".")
        box = boxes_proposal("scope-boxes", "Clinical boxes from Scope",
                             "Only statements the Scope note makes plainly. Confirm each imported box.",
                             wanted, values, whys, unclear)
        if box:
            box.requires_confirmation = True
            box.confirmation_reason = "Scope generated these facts; verify them against the encounter before applying."
            proposals.append(box)
        unclear += reading_unclear
        unclear += [f"Unfinished Scope text: “{x}”" for x in unfinished]

    medications = _content(doc, "medications")
    allergies = _content(doc, "allergies")
    combined = _content(doc, "meds_allergies")
    if combined and not (medications or allergies):
        unclear.append("Medications and allergies were combined in one section. Separate them in Scope before importing: “" +
                       " ".join(combined.split())[:180] + "”")
    for p in (
        _text_proposal("scope-meds", "Medication list from Scope", "meds-descr", medications, values, confirm=True,
                       reason="Verify drug, dose, route, frequency and status with the patient."),
        _text_proposal("scope-allergies", "Allergies from Scope", "Allergies", allergies, values, confirm=True,
                       reason="Verify allergy and reaction with the patient."),
    ):
        if p:
            proposals.append(p)

    # Deterministic readers for fields that already have clinic-specific spoken wording.
    interpreted: dict[str, object] = {}
    for sec_name, mode in (("vitals", "vitals"), ("exam", "exam"), ("goggles", "goggles"),
                           ("plan", "plan"), ("followup", "plan")):
        body = _content(doc, sec_name)
        if not body:
            continue
        it = dictation.interpret(body, mode)
        for k, v in it.set.items():
            if k != "HPI-age":
                interpreted[k] = v
        for n in it.notes:
            if n.startswith("Not placed:"):
                unclear.append(f"{sec_name.title()} was not mapped: {n[len('Not placed:'):].strip()}")
    p = _changes_proposal("scope-structured", "Vitals, exam and structured findings from Scope", interpreted, values,
                          "Verify values, laterality and every abnormal finding.")
    if p:
        proposals.append(p)

    assessment = _content(doc, "assessment")
    plan = _content(doc, "plan", "plan_orders_referrals")
    impression = "\n".join(x for x in (assessment, plan) if x).strip()
    p = _text_proposal("scope-impression", "Assessment and plan from Scope", "impression-plan-descr", impression, values,
                       confirm=True, reason="Verify every diagnosis, treatment, order and follow-up instruction.")
    if p:
        proposals.append(p)

    studies = _studies(_content(doc, "studies"), unclear)
    p = _changes_proposal("scope-studies", "Tests and studies from Scope", studies, values,
                          "Verify study name, date and result against the source report.")
    if p:
        proposals.append(p)
    refs = _referrals(_content(doc, "referrals"), unclear)
    p = _changes_proposal("scope-referrals", "Referrals from Scope", refs, values,
                          "Verify clinician, specialty and referral diagnosis.")
    if p:
        proposals.append(p)
    orders = _orders(_content(doc, "orders"), unclear)
    p = _changes_proposal("scope-orders", "Orders from Scope", orders, values,
                          "Verify each order before applying it.")
    if p:
        proposals.append(p)

    time_text = _content(doc, "time", "time_attestation")
    if time_text:
        time_it = dictation.interpret(time_text, "time")
        p = _changes_proposal("scope-time", "Encounter time from Scope", time_it.set, values,
                              "Confirm the documented time from the encounter record.")
        if p:
            proposals.append(p)
    att = _attestation(_content(doc, "attestation"), unclear)
    p = _changes_proposal("scope-attestation", "Attestation from Scope", att, values,
                          "Confirm the attending and approved attestation statement.")
    if p:
        proposals.append(p)

    review = _content(doc, "needs_review")
    unclear += [f"Scope marked for review: {line}" for line in _line_items(review)]
    # Keep review output compact and deterministic.
    unclear = list(dict.fromkeys(x for x in unclear if x))
    meds = story.find_meds("\n".join((medications, _content(doc, "hpi"))))
    return {"kind": "scope", "version": doc.version, "proposals": [p.as_dict() for p in proposals],
            "unclear": unclear, "notes": notes, "meds": meds, "med_notes": [], "todo": []}
