"""One set of visit types for the whole app: the schedule, the note header, Build and coding.

The progress-note form has four boxes (Consult, Evaluation, Progress note, Urgent add-on). The schedule
and the clinic's own language have more kinds than that (a procedure visit, a results visit, a telehealth
visit), each of which prints as one of those boxes. A visit type is one of VISIT_TYPES; it carries a colour
that is the same wherever it shows, the form box it ticks and the E/M family it bills in (new or
established). The kind itself and where it came from are kept on the note as `visit-kind` and
`visit-source` (the PDF form has no field for them; a filed note reads back by its boxes).
"""
from __future__ import annotations

import re

KIND_FIELD = "visit-kind"
SOURCE_FIELD = "visit-source"
BOXES = ("neuro-consult", "neuro-eval", "neuro-progress", "neuro-urgent")
SOURCES = {"schedule": "from the schedule", "scope": "from Scope", "note": "from the note", "override": "chosen here"}

VISIT_TYPES = [
    {"id": "consult", "label": "New patient / Consult", "short": "Consult", "color": "#0071e3", "box": "neuro-consult", "family": "new",
     "hint": "A new patient referred by another clinician."},
    {"id": "eval", "label": "Evaluation", "short": "Evaluation", "color": "#7c3aed", "box": "neuro-eval", "family": "new",
     "hint": "A new patient seen on their own request."},
    {"id": "followup", "label": "Follow-up", "short": "Follow-up", "color": "#248a3d", "box": "neuro-progress", "family": "established",
     "hint": "An established patient's return visit."},
    {"id": "procedure", "label": "Procedure", "short": "Procedure", "color": "#c2410c", "box": "neuro-progress", "family": "established",
     "hint": "Epley chair, injection or another procedure visit."},
    {"id": "results", "label": "Testing / Results", "short": "Results", "color": "#0f766e", "box": "neuro-progress", "family": "established",
     "hint": "A visit to go over VNG, CDP, EMG or imaging results."},
    {"id": "telehealth", "label": "Telehealth", "short": "Telehealth", "color": "#b4235a", "box": "neuro-progress", "family": "established",
     "hint": "Seen by video or phone."},
    {"id": "urgent", "label": "Urgent add-on", "short": "Urgent", "color": "#d70015", "box": "neuro-urgent", "family": "established",
     "hint": "Worked in the same day."},
    {"id": "other", "label": "Other", "short": "Other", "color": "#6b7280", "box": "", "family": "",
     "hint": "Anything else; pick the form box on Patient."},
]
BY_ID = {t["id"]: t for t in VISIT_TYPES}
# The kind a form box alone says (a filed note, a note typed without the schedule).
KIND_OF_BOX = {"neuro-consult": "consult", "neuro-eval": "eval", "neuro-progress": "followup", "neuro-urgent": "urgent"}

# What the schedule calls a visit (Outlook categories, the subject) -> a kind. More specific words win.
PATTERNS = [
    ("procedure", re.compile(r"\b(?:epley|chair|crp|crm|reposition\w*|inject\w*|nerve block|block|botox|cerumen|procedure)\b", re.I)),
    ("results", re.compile(r"\b(?:results?|vng|vemp|cdp|emg|ncv|eeg|audio\w*|hearing test|review (?:of )?(?:mri|ct|imaging)|test(?:ing)?)\b", re.I)),
    ("telehealth", re.compile(r"\b(?:tele\w*|video|virtual|phone|zoom|doxy)\b", re.I)),
    ("urgent", re.compile(r"\b(?:urgent|add[- ]?on|same[- ]?day|work[- ]?in|acute)\b", re.I)),
    ("eval", re.compile(r"\b(?:eval\w*)\b", re.I)),
    ("consult", re.compile(r"\b(?:new|consult\w*|np|n/p)\b", re.I)),
    ("followup", re.compile(r"\b(?:f/u|fu|follow[- ]?up|progress|return|recheck|rv|established|est)\b", re.I)),
]
TESTING_CALENDARS = re.compile(r"vng|vemp|eeg|emg|ncv|cdp", re.I)


def classify(text: str, calendar: str = "") -> str:
    """The kind a schedule row says: "Follow-Up · VNG results" is a results visit, "New Patient" a consult; "" when
    nothing in the words says."""
    words = " ".join((text or "").split())
    for kind, rx in PATTERNS:
        if rx.search(words):
            return kind
    if calendar and TESTING_CALENDARS.search(calendar):
        return "results"
    return ""


def kind_of(values: dict) -> tuple[str, str]:
    """(kind, source) for a note: the kind kept on it when it is a real one, else what its form boxes say."""
    v = values or {}
    kind = str(v.get(KIND_FIELD) or "").strip()
    if kind in BY_ID:
        box = BY_ID[kind]["box"]
        if not box or v.get(box) in (True, "Yes", "On", "true", 1, "1"):
            return kind, str(v.get(SOURCE_FIELD) or "note")
    for box, k in KIND_OF_BOX.items():
        if v.get(box) in (True, "Yes", "On", "true", 1, "1"):
            return k, "note"
    return "", ""


def changes_for(kind: str, source: str = "override") -> dict:
    """What a note changes to be this kind: the form box it ticks (the others off), the kind and its source."""
    if kind not in BY_ID:
        raise ValueError(f"unknown visit type {kind!r}")
    box = BY_ID[kind]["box"]
    out = {name: name == box for name in BOXES} if box else {}
    out[KIND_FIELD] = kind
    out[SOURCE_FIELD] = source if source in SOURCES else "override"
    return out


def describe(kind: str) -> dict:
    """The type's label and colour for a screen; the grey "Other" for an unknown one."""
    return dict(BY_ID.get(kind) or BY_ID["other"])


def family(values: dict) -> str:
    """"new" / "established" / "" for coding, from the kind on the note (its boxes agree with it)."""
    kind, _ = kind_of(values)
    return BY_ID[kind]["family"] if kind else ""
