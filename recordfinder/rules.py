"""Who rendered a visit, and who was the attending (supervising) physician.

Everything office-specific comes from settings: the provider roster and the attending rules.
The result is a suggestion with its reasons; a person confirms it before anything is changed.

Settings shape (see config.example.json):
    "roster": [{"code": "AB", "name": "Alex Brown, PA-C", "kind": "pa", "calendar": "Alex Brown"},
               {"code": "DR", "name": "Dana Reed, MD", "kind": "physician", "calendar": "DR",
                "column": "DR", "aliases": ["Dr. Reed"]}],
    "attending_rules": {"default": "DR",
                        "payer_excludes": {"PAYER": ["DR2"]},          # physician can't see this payer
                        "present_categories": {"DR has to be present": "DR"}}
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .superbills import _fold, norm_code

OUT_WORDS = r"\b(?:out|off|vacation|pto|no clinic|not in|away)\b"


@dataclass
class Provider:
    code: str
    name: str                      # as printed on the superbill, e.g. "Alex Brown, PA-C"
    kind: str = "pa"               # "physician" or "pa" (any non-physician rendering provider)
    calendar: str = ""             # Outlook calendar name
    column: str = ""               # physicians: attending column header on the superbill
    side: str = ""                 # physicians: which side of the PROVIDER list that column is on
    aliases: list[str] = field(default_factory=list)  # how notes refer to them ("Dr. Reed", "DR")
    codes: list[str] = field(default_factory=list)    # extra batch codes ("DR 2")

    @property
    def surname(self) -> str:
        base = self.name.split(",")[0].strip()
        return base.split()[-1] if base else ""

    @property
    def is_physician(self) -> bool:
        return self.kind.lower() in ("physician", "md", "do")

    def all_codes(self) -> list[str]:
        return [c for c in [self.code, *self.codes] if c]

    def all_aliases(self) -> list[str]:
        return [a for a in {self.code, self.column, self.surname, *self.aliases} if a]


def roster_from(cfg: dict) -> list[Provider]:
    out = []
    for p in cfg.get("roster") or []:
        try:
            out.append(Provider(**{k: v for k, v in p.items() if k in Provider.__dataclass_fields__}))
        except TypeError:
            continue
    return out


def by_code(roster: list[Provider], code: str) -> Provider | None:
    want = norm_code(code)
    if not want:
        return None
    best = None
    for p in roster:
        for c in p.all_codes():
            nc = norm_code(c)
            if want == nc:
                return p
            if want.startswith(nc) and (best is None or len(nc) > len(norm_code(best[1]))):
                best = (p, c)  # "TYEMGNCV" -> the "TY" provider
    return best[0] if best else None


def by_calendar(roster: list[Provider], calendar: str) -> Provider | None:
    return next((p for p in roster if p.calendar and p.calendar.lower() == calendar.lower()), None)


def by_name(roster: list[Provider], name: str) -> Provider | None:
    f = _fold(name)
    return next((p for p in roster if p.surname and _fold(p.surname) in f), None)


@dataclass
class Decision:
    rendering: Provider | None
    attending: Provider | None
    medicare: bool
    payer: str
    reasons: list[str]
    confidence: str   # "high", "medium" or "low"


def _mentions(note: str, p: Provider) -> bool:
    return any(re.search(rf"(?<![A-Za-z]){re.escape(a)}(?![A-Za-z])", note, re.I) for a in p.all_aliases())


def decide(rendering: Provider | None, roster: list[Provider], rules: dict, *, payer: str = "",
           medicare: bool = False, categories: list[str] | None = None, day_notes: list[str] | None = None,
           physician_patients: dict[str, int] | None = None, referral_names: list[str] | None = None) -> Decision:
    """Suggest the attending physician for a visit, most decisive evidence first."""
    reasons: list[str] = []
    categories = categories or []
    physicians = [p for p in roster if p.is_physician]

    if rendering is not None and rendering.is_physician:
        return Decision(rendering, None, medicare, payer, [f"{rendering.name} is a physician: no attending to mark"], "high")

    # 1. The booking itself says who has to be there.
    for cat in categories:
        for label, code in (rules.get("present_categories") or {}).items():
            if cat.strip().lower() == label.lower() and (p := by_code(physicians, code)):
                return Decision(rendering, p, medicare, payer, [f'Appointment is tagged "{cat}"'], "high")

    candidates = list(physicians)
    hard = False
    # 2. Payer rules: some physicians can't see some payers.
    for payer_key, codes in (rules.get("payer_excludes") or {}).items():
        if payer and re.search(rf"\b{re.escape(payer_key)}\b", payer, re.I):
            for code in codes:
                p = by_code(candidates, code)
                if p:
                    candidates.remove(p)
                    hard = True
                    reasons.append(f"{payer_key} patients can't be seen by {p.name}")
    # 3. Anyone marked out that day.
    for note in day_notes or []:
        if re.search(OUT_WORDS, note, re.I):
            for p in list(candidates):
                if _mentions(note, p):
                    candidates.remove(p)
                    reasons.append(f'{p.name} out that day ("{note[:60]}")')
    # 4. A referral/authorization addressed to one physician.
    for name in referral_names or []:
        p = by_name(candidates, name)
        if p:
            reasons.append(f"Referral / authorization is addressed to {p.name}")
            return Decision(rendering, p, medicare, payer, reasons, "high")
    if len(candidates) == 1:
        reasons.append(f"{candidates[0].name} is the only physician left")
        return Decision(rendering, candidates[0], medicare, payer, reasons, "high" if hard else "medium")
    # 5. Who had patients booked that day.
    busy = [p for p in candidates if (physician_patients or {}).get(p.calendar, 0) > 0]
    if len(busy) == 1:
        reasons.append(f"Only {busy[0].name} had patients on the schedule that day")
        return Decision(rendering, busy[0], medicare, payer, reasons, "medium")
    # 6. Office default.
    default = by_code(candidates, rules.get("default", "")) if candidates else None
    if default:
        reasons.append(f"Office default attending ({default.name}) - please confirm")
        return Decision(rendering, default, medicare, payer, reasons, "low")
    reasons.append("Couldn't narrow it down - please choose")
    return Decision(rendering, None, medicare, payer, reasons, "low")
