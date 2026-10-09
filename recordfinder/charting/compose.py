"""Write the note out from what was ticked and dictated, in the practice's own words.

In the exam room the provider ticks boxes (symptoms, exam rows, positional testing, plan) and
dictates. compose_note() turns that into the parts of the note that are written out: the HPI, the
findings line on each normal exam row, the numbered impression / plan, the chief complaint and the
oculography treatment line, using the sentences in phrases.py.

Nothing is applied by itself. Each part comes back as a proposal the screen shows next to what the
note says now, to apply, edit or skip. The HPI is drafted, not pasted: the practice's opening, a
paragraph for each symptom from its ticked boxes and text boxes, then whatever the provider's own
words (typed or dictated) say that the boxes don't, put in the clinic's wording by wording.py (fixed
rules, no language model), then the falls and pertinent negatives (only what was denied) and the closing line. A sentence of
the provider's is only left out when every word of it is already said by the boxes it ticked; a
denial is never split, a hedge never dropped, a part that would not stand on its own ("Not AD.",
"Morning after a cold.") never left alone. It works both ways: a box the provider's kept words say
("Occasionally worse w/ standing") isn't written again in the paragraph, an association is written
once, and a paragraph that would only repeat the provider's sentence goes. On a rebuild the builder
replaces what it wrote the last time (the screen sends that back as `previous`) and keeps the
provider's sentences.

It never writes what only the provider knows: that a maneuver was done, minutes spent, the finding
on an abnormal exam row, a plan nobody ticked. Those are left as *** or listed as to-dos.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field as _field

from . import medlist, phrases, story, wording
from .note import FIELDS, SCHEMA, age_on, coerce, impressions, parse_date, split_name

HPI = "ChiefComplaint-descr"
IMPRESSION = "impression-plan-descr"
TREATMENT = "goggle-treatment-x"
BLANK = "***"
OPENING_RE = re.compile(r"^\s*(?:Mr\./Ms\.|Mr\.|Ms\.|Mrs\.|Miss|Pt\b|Patient\b|The patient\b).*?\bis an?\b.*?\by/?o\b", re.I)

# A box's printed label, where it doesn't read well inside a sentence.
WORDS = {
    "better-sit": "sitting", "better-supine": "lying supine", "better-headstill": "holding head still in a neutral position",
    "better-meds": "medications", "occur-sitting": "sitting", "occur-supine": "lying supine", "occur-standing": "standing",
    "occur-walking": "walking", "hlbetter-hearingaids": "hearing aids", "t-better-hearingaids": "hearing aids",
    "hlassoc-hearingfluc": "hearing fluctuation", "t-assoc-fluc": "hearing fluctuation", "h-assoc-fluc": "hearing fluctuation",
    "ga-better-sitrest": "sitting or resting", "ga-worse-walk": "walking", "ga-worse-corners": "turning corners",
    "ga-assoc-veerleft": "veering to the left", "ga-assoc-veerright": "veering to the right",
    "ga-assoc-weakness": "leg weakness", "ga-assoc-sensory": "sensory loss in the legs",
    "h-right": "right-sided", "h-left": "left-sided", "h-course-pressure": "pressure-like", "h-worse-diet": "dietary triggers",
    "h-worse-noise": "loud noise", "h-better-meds": "medications", "h-better-water": "hydration",
    "h-assoc-hearing": "hearing loss", "h-assoc-emesis": "vomiting",
}
SYMPTOMS = [("dizziness", "Dizziness"), ("hearingloss", "Hearing loss"), ("tinnitus", "Tinnitus"), ("gaitataxia", "Imbalance"),
            ("headache", "Headaches")]
DENIED = {"dizziness": "dizziness", "hearingloss": "hearing loss", "tinnitus": "tinnitus", "gaitataxia": "gait ataxia",
          "headache": "headaches"}
EAR = {"right": "AD", "left": "AS", "bilat": "AU"}
BPPV = {"imp-rightpost": "Right posterior canal BPPV", "imp-leftpost": "Left posterior canal BPPV",
        "imp-rightlateral": "Right lateral canal BPPV", "imp-leftlateral": "Left lateral canal BPPV",
        "imp-rightanterior": "Right anterior canal BPPV", "imp-leftanterior": "Left anterior canal BPPV",
        "imp-rightcupu": "Right cupulolithiasis", "imp-leftcupu": "Left cupulolithiasis"}
GAIT_DENIALS = {"ga-assoc-weakness-denies": "leg weakness", "ga-assoc-sensory-denies": "sensory loss in the legs",
                "ga-assoc-bowel-denies": "bowel incontinence", "ga-assoc-bladder-denies": "bladder incontinence"}


@dataclass
class Proposal:
    id: str
    title: str
    why: str
    changes: dict                 # field -> value, all applied together
    field: str = ""               # the one text box, when the proposal is one (the screen lets it be edited)
    text: str = ""
    current: str = ""
    lines: list[str] = _field(default_factory=list)       # what changes, for proposals over several boxes
    generated: list[str] = _field(default_factory=list)   # what the builder wrote; sent back as `previous`
    items: list[dict] = _field(default_factory=list)      # boxes to tick one by one: {text, changes, why}
    parts: list[dict] = _field(default_factory=list)      # the paragraphs of one text, each its own tick: {key, title, text, fixed}
    requires_confirmation: bool = False                   # imported/high-risk facts start unchecked on Build
    confirmation_reason: str = ""

    def as_dict(self) -> dict:
        return {"id": self.id, "title": self.title, "why": self.why, "changes": self.changes, "field": self.field,
                "text": self.text, "current": self.current, "lines": self.lines, "generated": self.generated, "items": self.items,
                "parts": self.parts, "requires_confirmation": self.requires_confirmation, "confirmation_reason": self.confirmation_reason}


@dataclass
class Composition:
    proposals: list[Proposal] = _field(default_factory=list)
    todo: list[str] = _field(default_factory=list)
    check: list[str] = _field(default_factory=list)      # what the provider's notes leave unclear: hedged, conflicting, unfinished
    meds: list[str] = _field(default_factory=list)       # medications the notes name
    topics: list[dict] = _field(default_factory=list)    # each symptom section as the HPI has it (story.topic_states)

    def as_dict(self) -> dict:
        return {"proposals": [p.as_dict() for p in self.proposals], "todo": self.todo, "check": self.check, "meds": self.meds,
                "topics": self.topics}


# --------------------------------------------------------------------------- wording helpers

def _text(val) -> str:
    return str(val or "").replace("\r\n", "\n").replace("\r", "\n")


def _join(items, conj: str = "and") -> str:
    items = [i for i in items if i]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + f" {conj} " + items[-1]


def _sentence(s: str) -> str:
    s = " ".join(str(s).split())
    if not s:
        return ""
    s = s[0].upper() + s[1:]
    return s if s.endswith((".", "!", "?")) else s + "."


def _word(name: str) -> str:
    if name in WORDS:
        return WORDS[name]
    label = re.sub(r"\s*/\s*", "/", FIELDS[name]["label"])
    return label if re.match(r"[A-Z]{2}", label) else label[:1].lower() + label[1:]


def _words(v: dict, *names: str) -> list[str]:
    return [_word(n) for n in names if v.get(n)]


def _first(v: dict, options: dict) -> str:
    return next((w for n, w in options.items() if v.get(n)), "")


def _box(v: dict, name: str) -> str:
    return _text(v.get(name)).strip()


def _duration(v: dict, prefix: str) -> str:
    """How long it lasts, every unit ticked: "seconds", "hours to days"."""
    units = [u for u in ("seconds", "minutes", "hours", "days") if v.get(f"{prefix}{u}")]
    return units[0] if len(units) == 1 else (f"{units[0]} to {units[-1]}" if units else "")


def _trend(v: dict, prefix: str, subject: str, plural: bool = False) -> str:
    verbs = ("are", "have") if plural else ("is", "has")
    t = _first(v, {f"{prefix}stable": f"{verbs[0]} stable", f"{prefix}worsening": f"{verbs[1]} been worsening",
                   f"{prefix}worse": f"{verbs[1]} been worsening", f"{prefix}improving": f"{verbs[1]} been improving",
                   f"{prefix}improve": f"{verbs[1]} been improving"})
    return _sentence(f"{subject} {t}") if t else ""


def _adl(v: dict, prefix: str) -> str:
    how = _first(v, {f"{prefix}mild": "Mildly", f"{prefix}mod": "Moderately", f"{prefix}severe": "Severely", f"{prefix}sev": "Severely"})
    return f"{how} affects ADLs." if how else ""


def _with(v: dict, names: list[str], texts: dict | None = None) -> list[str]:
    """Words for the ticked boxes; a box listed in `texts` reads its text box instead (or adds it in brackets)."""
    out = []
    for n in names:
        if not v.get(n):
            continue
        extra = _box(v, (texts or {}).get(n, "")) if (texts or {}).get(n) else ""
        if n.endswith("-other"):
            if extra:
                out.append(extra)
        else:
            out.append(_word(n) + (f" ({extra})" if extra else ""))
    return out


# A frequency box that says how often ("~2x/month", "daily") reads as "Occurs ~2x/month."
FREQ_TEXT = re.compile(r"^(?:possibly |probably )?(?:~|<|>)?(?:\d|once\b|twice\b|daily\b|nightly\b|weekly\b|several\b|a few\b|multiple\b)", re.I)


def _describe(v: dict, desc: str, onset: str, freq: str = "", skip: str = "") -> list[str]:
    out = []
    d = _box(v, desc) if desc else ""
    if d:
        # a phrase from the notes ("lightheadedness w/ room moving at times") reads as a description
        out.append(_sentence(f"Described as {d}" if d[:1].islower() else d))
    f = _box(v, freq) if freq else ""
    if f and f.lower() != skip.lower():
        out.append(_sentence(f"Occurs {f}" if FREQ_TEXT.match(f) else f))
    o = _box(v, onset) if onset else ""
    if o and not re.match(r"see below|2/2", o, re.I):
        out.append("Longstanding hx." if re.match(r"longstanding", o, re.I) else _sentence(f"Onset {o}"))
    return out


# --------------------------------------------------------------------------- HPI

def _dizziness(v: dict, said: str = "") -> list[str]:
    course = _first(v, {"course-parox": "paroxysmal", "course-chronic": "chronic"})
    kinds = _words(v, "assoc-vertigo", "assoc-diseq", "assoc-cbd")
    if kinds and story.DIZZ_WORD.search(said or ""):
        kinds = ["dizziness w/ " + _join(kinds)]      # the notes say "dizzy": the paragraph does too ("dizziness w/ vertigo")
    s = "Pt reports " + (course + " " if course else "") + _join(kinds or ["dizziness"])
    if _duration(v, "duration-"):
        s += " lasting " + _duration(v, "duration-")
    worse = _words(v, "worse-standing", "worse-walking", "worse-walking-in-dark", "worse-transitioning", "worse-pitching",
                   "worse-yaw", "worse-supine", "worse-stress", "worse-opto", "worse-highgain")
    if worse:
        s += ", worse w/ " + _join(worse)
    out = [_sentence(s)] + _describe(v, "DizzyDesc", "DizzOnsetDate", "DizzFreq", skip=course)
    out.append(_trend(v, "course-", "Sxs", plural=True))
    if v.get("assoc-attacks"):
        out.append("Occurs in attacks.")
    occur = _words(v, "occur-sitting", "occur-supine", "occur-standing", "occur-walking")
    if occur:
        out.append(_sentence("Occurs while " + _join(occur, "or")))
    better = _with(v, ["better-sit", "better-supine", "better-headstill", "better-meds", "better-other"],
                   {"better-meds": "better-thesemeds", "better-other": "better-factors"})
    if better:
        out.append(_sentence("Better w/ " + _join(better)))
    if v.get("better-resolves"):
        out.append("Resolves on own.")
    if v.get("better-none"):
        out.append("No clear relieving or exacerbating factors.")
    assoc = _words(v, "assoc-headache", "assoc-tinnitus", "assoc-hearingloss", "assoc-auralfullness")
    side = _first(v, {"assoc-ad": "AD", "assoc-as": "AS", "assoc-au": "AU"})
    if assoc:
        out.append(_sentence("Assoc w/ " + _join(assoc) + (f" {side}" if side else "")))
    out.append(_adl(v, "adl-"))
    return out


def _hearing(v: dict) -> list[str]:
    side = _first(v, {f"hl-{k}": ear for k, ear in EAR.items()})
    course = _first(v, {"hlcourse-parox": "paroxysmal", "hlcourse-chronic": "chronic"})
    s = "Reports " + (course + " " if course else "") + "hearing loss" + (f" {side}" if side else "")
    if _duration(v, "hldur-"):
        s += " lasting " + _duration(v, "hldur-")
    out = [_sentence(s)] + _describe(v, "HLdesc", "HLOnsetDate")
    out.append(_trend(v, "hlcourse-", "Hearing loss"))
    if v.get("hlcourse-attacks"):
        out.append("Occurs in attacks.")
    better = _with(v, ["hlbetter-hearingaids", "hlbetter-other"], {"hlbetter-other": "hl-betterif-other-descr"})
    if better:
        out.append(_sentence("Better w/ " + _join(better)))
    worse = _with(v, ["hlworse-loudbg", "hlworse-other"], {"hlworse-other": "hl-worseif-other-descr"})
    if worse:
        out.append(_sentence("Worse w/ " + _join(worse)))
    if v.get("hlbetter-none"):
        out.append("No clear relieving or exacerbating factors.")
    if v.get("hlassoc-notab"):
        out.append("No associated vertigo, disequilibrium, headache, tinnitus, fluctuation or aural fullness.")
    else:
        assoc = _words(v, "hlassoc-vertigo", "hlassoc-diseq", "hlassoc-headache", "hlassoc-tinnitus", "hlassoc-hearingfluc", "hlassoc-aural")
        if assoc:
            out.append(_sentence("Assoc w/ " + _join(assoc)))
    return out


def _tinnitus(v: dict) -> list[str]:
    side = _first(v, {f"t-{k}": ear for k, ear in EAR.items()})
    kind = [x for x in (_first(v, {"t-course-parox": "paroxysmal", "t-course-chronic": "chronic"}),
                        _first(v, {"t-pulsatile": "pulsatile", "t-nonpulsatile": "non-pulsatile"})) if x]
    s = "Reports " + (", ".join(kind) + " " if kind else "") + "tinnitus" + (f" {side}" if side else "")
    if _duration(v, "t-dur-"):
        s += " lasting " + _duration(v, "t-dur-")
    out = [_sentence(s)] + _describe(v, "tinnitus-descr", "TinnitusOnsetDate")
    out.append(_trend(v, "t-course-", "Tinnitus"))
    if v.get("t-course-attacks"):
        out.append("Occurs in attacks.")
    freq = _first(v, {"t-course-freq-hi": "high", "t-course-freq-med": "medium", "t-course-freq-low": "low"})
    if freq:
        out.append(f"Frequency: {freq}.")
    better = _with(v, ["t-better-hearingaids", "t-better-other"], {"t-better-other": "t-symptomsbetterif-other-descr"})
    if better:
        out.append(_sentence("Better w/ " + _join(better)))
    worse = _with(v, ["t-worse-loudbg", "t-worse-other"], {"t-worse-other": "t-symptomsworseif-other-descr"})
    if worse:
        out.append(_sentence("Worse w/ " + _join(worse)))
    if v.get("t-better-none") or v.get("t-worse-none"):
        out.append("No clear relieving or exacerbating factors.")
    if v.get("t-assoc-notab"):
        out.append("No associated vertigo, disequilibrium, headache, hearing loss, fluctuation or aural fullness.")
    else:
        assoc = _words(v, "t-assoc-vertigo", "t-assoc-diseq", "t-assoc-headache", "t-assoc-hearingloss", "t-assoc-fluc", "t-assoc-aural")
        if assoc:
            out.append(_sentence("Assoc w/ " + _join(assoc)))
    out.append(_adl(v, "t-adl-"))
    return out


# The patient's word for the gait complaint, when the notes give one ("imbalance"); "gait ataxia" is an exam term.
GAIT_WORD = [(re.compile(r"\bataxi\w*", re.I), "gait ataxia"), (re.compile(r"\bunsteadiness\b|\bunsteady\b", re.I), "unsteadiness"),
             (re.compile(r"\bimbalance\b|\boff[- ]balance\b|\bwobbly\b|\bbalance (?:problems?|issues?|trouble)\b", re.I), "imbalance")]


def _gait(v: dict, said: str = "") -> list[str]:
    course = _first(v, {"ga-course-parox": "paroxysmal", "ga-course-chronic": "chronic"})
    descr = _box(v, "gaitataxia-descr")
    word = next((w for rx, w in GAIT_WORD if rx.search(said or "")), "gait ataxia")
    s = "Reports " + (course + " " if course else "") + word
    if descr.lower().startswith("2/2"):
        s, descr = s + " " + descr, ""
    out = [_sentence(s)] + ([_sentence(descr)] if descr else []) + _describe(v, "", "GAOnsetDate")
    out.append(_trend(v, "ga-course-", "Gait", plural=False))
    if v.get("ga-course-attacks"):
        out.append("Occurs in attacks.")
    worse = _with(v, ["ga-worse-walk", "ga-worse-corners", "ga-worse-other"], {"ga-worse-other": "ga-worseother-descr"})
    if worse:
        out.append(_sentence("Worse w/ " + _join(worse)))
    for box, aid, dur in (("ga-better-cane", "a cane", "ga-usecanedur-descr"), ("ga-better-walker", "a walker", "ga-usewalkerdur-descr"),
                          ("ga-better-chair", "a wheelchair", "ga-usewheelchairdur-descr")):
        if v.get(box):
            out.append(_sentence(f"Uses {aid}" + (f" x {_box(v, dur)}" if _box(v, dur) else "")))
    if v.get("ga-better-sitrest"):
        out.append("Better w/ sitting or resting.")
    if v.get("ga-better-none") or v.get("ga-worse-none"):
        out.append("No clear relieving or exacerbating factors.")
    assoc = _with(v, ["ga-assoc-orthostasis", "ga-assoc-veerleft", "ga-assoc-veerright", "ga-assoc-bradykinesia", "ga-assoc-headache",
                      "ga-assoc-mental", "ga-assoc-weakness", "ga-assoc-sensory", "ga-assoc-bowel", "ga-assoc-bladder"],
                  {"ga-assoc-weakness": "ga-weak-descr", "ga-assoc-sensory": "ga-sens-descr", "ga-assoc-bowel": "ga-bowel-descr",
                   "ga-assoc-bladder": "ga-blad-descr"})
    if assoc:
        out.append(_sentence("Assoc w/ " + _join(assoc)))
    return out


def _headache(v: dict) -> list[str]:
    adj = [x for x in [_first(v, {"h-course-parox": "paroxysmal", "h-course-chronic": "chronic"})]
           + _words(v, "h-course-sharp", "h-course-dull", "h-course-throb", "h-course-pressure", "h-course-const") if x]
    where = _join(_words(v, "h-right", "h-left", "h-global", "h-frontal", "h-occipital"))
    s = "Reports " + ", ".join(adj) + (" " if adj else "") + (where + " " if where else "") + "headaches"
    for box, per in (("h-freq-wk", "week"), ("h-freq-month", "month")):
        n = _box(v, box)
        if n:
            s += f", {n}x/{per}" if re.fullmatch(r"~?[\d\-\s]+", n) else f", {n}"
    if _duration(v, "h-dur-"):
        s += ", lasting " + _duration(v, "h-dur-")
    scale = re.sub(r"\s*/\s*10\s*$", "", _box(v, "h-scale"))
    if scale:
        s += f", rated {scale}/10"
    out = [_sentence(s)] + _describe(v, "headache-descr", "HOnsetDate")
    out.append(_trend(v, "h-course-", "Headaches", plural=True))
    if v.get("h-course-attacks"):
        out.append("Occurs in attacks.")
    worse = _words(v, "h-worse-diet", "h-worse-noise", "h-worse-lights", "h-worse-stress")
    if _box(v, "h-symptomsworseif-descr"):
        worse.append(_box(v, "h-symptomsworseif-descr"))
    if worse:
        out.append(_sentence("Worse w/ " + _join(worse)))
    better = _with(v, ["h-better-meds", "h-better-water", "h-better-other"],
                   {"h-better-meds": "h-better-thesemeds", "h-better-other": "h-better-other-descr"})
    if better:
        out.append(_sentence("Better w/ " + _join(better)))
    if v.get("h-worse-norelief"):
        out.append("No clear relieving factors.")
    if v.get("h-worse-noexac"):
        out.append("No clear exacerbating factors.")
    assoc = _words(v, "h-assoc-nausea", "h-assoc-emesis", "h-assoc-photo", "h-assoc-phono", "h-assoc-aura", "h-assoc-scotomas",
                   "h-assoc-vertigo", "h-assoc-diseq", "h-assoc-hearing", "h-assoc-fluc", "h-assoc-aural", "h-assoc-tinnitus")
    if assoc:
        out.append(_sentence("Assoc w/ " + _join(assoc)))
    elif v.get("h-assoc-none"):
        out.append("No associated sxs.")
    out.append(_adl(v, "h-adl-"))
    return out


# Words that show a negative is already in the provider's text ("denies ... recent falls").
SAID_WORDS = {"dizziness": ("dizz", "vertigo"), "hearingloss": ("hearing", "hl", "snhl"), "tinnitus": ("tinnitus", "ringing"),
              "gaitataxia": ("gait", "ataxia", "imbalance", "balance"), "headache": ("headache", "ha\\b", "migraine"), "falls": ("fall",),
              "ga-assoc-weakness-denies": ("weakness",), "ga-assoc-sensory-denies": ("numb", "sensory", "paresthes", "tingling"),
              "ga-assoc-bowel-denies": ("bowel",), "ga-assoc-bladder-denies": ("bladder",)}


def _said_no(text: str, key: str) -> bool:
    """The provider's own text already denies this ("Denies resting tremor, weakness, ... or recent falls")."""
    for sentence in re.split(r"(?<=[.;!?])\s+", (text or "").lower()):
        if re.search(r"\b(denies|denied|no|not|without|negative for|neg)\b|w/o", sentence) and \
                any(re.search(rf"\b{w}", sentence) for w in SAID_WORDS.get(key, ())):
            return True
    return False


def _falls(v: dict, follow_up: bool, said: str = "") -> list[str]:
    if v.get("ga-falls-yes"):
        if said and re.search(r"\b(?:falls?|fell|fallen)\b", said, re.I):
            return []                                 # "One fall but reports tripped over rug" says it
        return ["Reports falls in the interim." if follow_up else "Reports hx of falls."]
    if v.get("ga-falls-no") and not _said_no(said, "falls"):
        return ["Denies any falls over the interim." if follow_up else "Denies any recent falls."]
    return []


def _negatives(v: dict, said: str = "") -> list[str]:
    """The pertinent negatives the boxes say, only those: "Denies hearing loss or tinnitus." (the practice's longer
    sentence also denies fluctuation and aural fullness, which a box can't say was asked)."""
    out = []
    denied = {k: w for k, w in DENIED.items() if v.get(k + "-denies") and not v.get(k) and not _said_no(said, k)}
    if denied:
        out.append(_sentence("Denies " + _join(list(denied.values()), "or")))
    gait = [w for n, w in GAIT_DENIALS.items() if v.get(n)]
    if len(gait) == len(GAIT_DENIALS) and not any(_said_no(said, n) for n in GAIT_DENIALS):
        out.append(phrases.HPI_NEGATIVES[4])        # "Denies any BLE weakness, hypo/paresthesias, or b/b incontinence."
    elif gait:
        gait = [w for n, w in GAIT_DENIALS.items() if v.get(n) and not _said_no(said, n)]
        if "bowel incontinence" in gait and "bladder incontinence" in gait:
            gait = [w for w in gait if w not in ("bowel incontinence", "bladder incontinence")] + ["bowel or bladder incontinence"]
        if gait:
            out.append(_sentence("Denies " + _join(gait, "or")))
    return out


HEADS = {"dizziness", "hearingloss", "tinnitus", "gaitataxia", "headache"}
DETAIL_PREFIX = {"dizziness": ("assoc-", "worse-", "course-", "occur-", "duration-", "better-", "adl-"),
                 "hearingloss": ("hl-", "hlcourse-", "hlbetter-", "hlworse-", "hlassoc-", "hldur-"),
                 "tinnitus": ("t-",), "gaitataxia": ("ga-",), "headache": ("h-",)}


def present(v: dict) -> list[str]:
    return [name for key, name in SYMPTOMS if v.get(key)]


def _reason(v: dict, fallback: str = "") -> str:
    """The reason for the visit: the chief complaint, else what an earlier build's opening said, else the symptoms."""
    cc = _box(v, "ChiefComplaint")
    if cc:
        first = cc.split(" ", 1)[0]
        return cc[:1].lower() + cc[1:] if first[1:].islower() and not first.endswith("'s") else cc
    if fallback:
        return fallback
    names = present(v)
    return _join([n.lower() for n in names]) if names else "*****"


def follow_up(v: dict) -> bool:
    return bool(v.get("neuro-progress"))


# A dictated or typed first sentence: "64-year-old right-handed female presents for evaluation of hand tremor ...",
# "67-year-old with shaking in both hands ...", "71 yo F brought by daughter for memory concerns ...".
_HAND = r"(?:,?\s*(?:(?P<hand>right|left)[- ]?handed|(?P<hand2>[RL])HD)\b)"
DICTATED_OPENING = re.compile(
    r"^(?:(?:so|okay|ok|um+|uh+)[,\s]+)*(?:(?:this is|here is|we have|i'm seeing|seeing|(?:the\s+)?(?:patient|pt)\s+is|(?:she|he)(?:'s|\s+is))\s+)?(?:an?\s+)?"
    r"(?P<age>\d{1,3})\s*(?:-?\s*(?:years?|yrs?)[- ]?old|y/?o\b|yo\b|y\.o\.)"
    rf"{_HAND}?"
    r"(?:,?\s*(?P<sex>female|male|woman|man|gentleman|lady|F|M)\b)?"
    rf"(?:{_HAND.replace('hand>', 'handb>').replace('hand2>', 'hand2b>')})?"
    r",?\s*(?:who\s+)?(?:(?:presents?|presenting|is\s+(?:here|seen|presenting)|comes?\s+in|came\s+in|here|seen|referred|returns?|"
    r"(?P<fu>f/u|follow[- ]?up|returns?\s+for\s+f/u)|"
    r"brought(?:\s+in)?(?:\s+by\s+(?:her\s+|his\s+|their\s+)?\w+)?)\s+)?"
    r"(?:to\s+(?:the\s+)?clinic\s+)?(?:today\s+)?(?:(?:for|with|w/|c/o|complaining\s+of|due\s+to|regarding)\s+|(?P<comma>[,.])\s*|"
    r"(?P<says>(?=(?:says?|reports?|states?|endorses|notes|hx of|w/ hx|with hx|with a history)\b)))"
    r"(?:(?:an?\s+)?(?P<kind>evaluation|consultation|consult|follow[- ]?up|eval|f/u)\s+(?:(?:of|for|on|regarding)\s+)?)?",
    re.I)
# The practice's own opening, whose details can be refreshed: "Ms. Sample is a 64 y/o RHD female presenting to the clinic today for ..."
CLINIC_LEAD = re.compile(r"^(?P<title>Mr\./Ms\.|Mr\.|Ms\.|Mrs\.|Miss)\s+(?P<last>\S+)\s+is\s+an?\s+(?P<age>\S+)\s+y/o\s+(?P<hand>\S*?)HD\s+"
                         r"(?P<sex>\S+)\s+presenting to the clinic today for\s+(?P<kind>a consultation on|a follow-up on|an evaluation of)?\s*", re.I)
HISTORY_SAID = re.compile(r"\b(pmhx|past medical history|medical history|history of|hx of|hx significant)\b", re.I)
NO_HISTORY_SAID = re.compile(r"\b(no (significant |prior |other )?(?:(past )?medical history|pmhx)|pmhx (is )?(non|in)significant|"
                             r"pmhx noncontributory)\b", re.I)
# "... w/ hx migraine c/o dizziness": the complaint starts after "c/o".
COMPLAINT_START = re.compile(r"\b(?:c/o|complain(?:s|ing)? of|presents? (?:for|with|w/)|presenting (?:for|with|w/)|here for|comes? in for|"
                             r"f/u (?:for|of)|follow[- ]?up (?:for|of|on))\s+", re.I)
# What a visit is for, besides the symptom words: a diagnosis the clinic follows ("F/u for BPPV").
DIAGNOSIS = re.compile(r"\b(?:bppv|meniere'?s?|vestibular (?:migraine|neuritis|schwannoma|hypofunction|rehab\w*)|vm|labyrinthitis|pppd|mdds|"
                       r"s?snhl|chl|vestibulopathy|concussion|post-?concussive|tremor|neuropathy|syncope|presyncope|memory|cognitive|"
                       r"numbness|weakness|vision|diplopia|tinnitus|falls?|otitis|cholesteatoma|otosclerosis|acoustic neuroma)\b", re.I)
# A reason said loosely, in the section's word: "dizzy" -> "dizziness", "ringing" -> "tinnitus".
REASON_WORDS = [(re.compile(r"\bdizzy\b", re.I), "dizziness"), (re.compile(r"\bunsteady\b", re.I), "unsteadiness"),
                (re.compile(r"\blight[- ]?headed\b", re.I), "lightheadedness"), (re.compile(r"\bwoozy\b", re.I), "wooziness"),
                (re.compile(r"\bringing(?: in (?:the|her|his) ears?)?\b", re.I), "tinnitus"), (re.compile(r"\boff[- ]balance\b", re.I), "imbalance")]
# Where a dictated reason stops: a new clause (a pronoun, a verb), a filler, how often, a qualifier.
REASON_STOP = re.compile(r"\s+(?:he|she|they|pt|patient|it|it's|which|who|that|um+|uh+|and|but|for|reports?|says?|states?|has|had|have|is|was|"
                         r"are|were|worse|better|lasting|lasts?|throbbing|behind|at|w/|with|since|after|before|no|not|denies|"
                         r"maybe|possibly|probably|daily|weekly|nightly|constant\w*|intermittent\w*|occasional\w*|sometimes)\b|"
                         r"\s+(?:in|on)\b(?!\s+(?:both|the|her|his|my)?\s*(?:right|left|r|l)?\s*(?:hands?|arms?|legs?|feet|foot|ears?|eyes?|head|"
                         r"neck|face|sides?)\b)|\s+(?:~|about\s+|around\s+)?\d+(?:-\d+)?\s*x/|\s+(?:~|about\s+)?\d+\s*(?:times|x)\b", re.I)
# Said after "with" in an opening but not a reason for the visit: aids, people, history.
NOT_REASON = re.compile(r"^(?:an?\s+|his\s+|her\s+)?(?:walkers?|canes?|rollators?|wheelchairs?|hearing aids?|oxygen|glasses|"
                        r"(?:his|her|their)\s+\w+|wife|husband|daughter|son|family|caregiver|friend|hx\b|history\b|h/o\b|pmh)", re.I)


def _names_complaint(text: str) -> bool:
    return bool(story._topics(text) or DIAGNOSIS.search(text) or re.search(r"\b(?:dizz\w*|imbalance|unstead\w*|light[- ]?headed\w*|ringing|"
                                                                       r"shaking|pain|memory|numb\w*|weak\w*|fullness|pressure)\b", text, re.I))


def _split_reason(text: str) -> tuple[str, str]:
    """(the reason, the words after it) from what follows "presents for": the denial before the complaint and the
    words after it stay in the rest, nothing is lost."""
    cut = REASON_END.search(text)
    said = (text[: cut.start()] if cut else text).strip()
    rest = text[cut.start():] if cut else ""
    reason = _clean_reason(said)
    if not reason:
        return "", text
    denial = re.match(r"^(?:(?:no|denies|without|not)\s+[^,]*?(?:,\s*|\s+but\s+))+", said, re.I)
    core = said[denial.end():] if denial else said
    stop = next((m for m in REASON_STOP.finditer(core) if _names_complaint(core[: m.start()])), None)
    extra = core[stop.start():].strip() if stop else ""
    rest = (extra + " " + rest).strip() if extra else rest
    if denial:
        rest = re.sub(r"\s*(?:,|\bbut)\s*$", "", denial.group(0).strip(), flags=re.I) + ". " + rest.lstrip(" ,;")
    return reason, rest


def _clean_reason(reason: str) -> str:
    """A reason fit for "presenting ... for a consultation on ___": the affirmed complaint ("no dizziness but new
    ringing AD" -> "new tinnitus AD"), cut where the next thought starts, in the section's word; "" when what is left
    names no complaint ("walker", "hx of falls")."""
    r = " ".join((reason or "").split())
    r = re.sub(r"^(?:(?:no|denies|without|not)\s+[^,]*?(?:,\s*|\s+but\s+))+", "", r, flags=re.I)        # the denial part goes
    # it stops at the first stop word after the complaint is named: "sudden constant spinning" is all the reason,
    # "headaches about twice a week throbbing ..." stops after "headaches"
    stop = next((m for m in REASON_STOP.finditer(r) if _names_complaint(r[: m.start()])), None)
    r = (r[: stop.start()] if stop else r).strip(" ,;.")
    if story.DENIAL.search(r):
        return ""
    r = re.sub(r"\s+(?:w/|with|for|of|and|or|to|the|a|an)$", "", r, flags=re.I)
    for rx, word in REASON_WORDS:
        r = rx.sub(word, r)
    if not r or NOT_REASON.match(r) or not re.search(r"[A-Za-z]{3}", r):
        return ""
    return r
# Where the reason for the visit ends in a dictated opening: "shaking in both hands | maybe 6 or 8 months, ...".
# A time ends it only when it is one ("for eight months", "x3 mo"), not "Epley x2 for L posterior canal BPPV".
REASON_END = re.compile(r"\s+(?:since|started|starting|that|which|described|maybe|possibly)\b|"
                        r"\s+(?:for|x)\s*(?:the\s+)?(?:past\s+|last\s+|about\s+|around\s+|~)?(?:\d+(?:\s*(?:-|to|or)\s*\d+)?|one|two|three|four|"
                        r"five|six|seven|eight|nine|ten|eleven|twelve|several|many|a few|a couple(?:\s+of)?|a|an)\s*"
                        r"(?:days?|weeks?|wks?|months?|mos?|years?|yrs?)\b|\s+for\s+(?:years|months|weeks|a while|a long time)\b|[,;.]", re.I)


def _sex(v: dict) -> str:
    return "female" if v.get("hpi-female") else ("male" if v.get("hpi-male") else "")


def _age(v: dict) -> str:
    age = _box(v, "HPI-age")
    if not age:
        dob, dos = parse_date(v.get("DOB", "")), parse_date(v.get("Date", ""))
        age = str(age_on(dob, dos)) if dob and dos else ""
    return age


def _hand(text: str) -> str:
    m = re.search(r"(?<![*\w])([RL])HD\b", text) or re.search(r"\b(right|left)[- ]handed\b", text, re.I)
    return m.group(1)[0].upper() if m else ""


def _kind(v: dict, said: str = "") -> str:
    if follow_up(v):
        return "a follow-up on"
    if v.get("neuro-consult"):
        return "a consultation on"
    if v.get("neuro-eval"):
        return "an evaluation of"
    said = (said or "").lower()
    return ("a follow-up on" if re.search(r"follow|f/u", said) else "a consultation on" if "consult" in said
            else "an evaluation of" if "eval" in said else "")


def _lead(v: dict, hand: str, kind: str, keep: dict | None = None) -> str:
    """The opening's first words with what the note knows (name, age, sex, handedness, visit), keeping what an existing
    opening already says where the note doesn't know better."""
    keep = keep or {}
    sex = _sex(v) or (keep.get("sex", "").lower() if keep.get("sex", "").lower() in ("female", "male") else "")
    title = {"female": "Ms.", "male": "Mr."}.get(sex, keep.get("title") if keep.get("title") in ("Mr.", "Ms.", "Mrs.", "Miss") else "Mr./Ms.")
    last = split_name(v.get("Name", ""))[0] or (keep.get("last") if keep.get("last") and "*" not in keep["last"] else "****")
    age = _age(v) or (keep.get("age") if keep.get("age") and "*" not in keep["age"] else BLANK)
    hand = hand or (keep.get("hand") if keep.get("hand") in ("R", "L") else BLANK)
    return _an(f"{title} {last} is a {age} y/o {hand}HD {sex or BLANK} presenting to the clinic today for {kind + ' ' if kind else ''}")


def _pmhx(v: dict, said: str, note_now: str = "") -> str:
    """The PMHx sentence: the history boxes ticked, or *** until someone says what it is. `note_now` is the HPI as it
    stands (the type of diabetes the notes or the last build gave is kept)."""
    words = [w for n, w in phrases.PMHX_WORDS.items() if v.get(n)]
    if "DM" in words and re.search(r"\b(?:dm\s*(?:2|ii)|t2dm|type (?:2|ii)|niddm)\b", (said or "") + "\n" + (note_now or ""), re.I):
        words[words.index("DM")] = "DM2"           # the type, as the notes say it
    pmhx = _join(words)
    if pmhx:
        return f"PMHx significant for {pmhx}."
    if NO_HISTORY_SAID.search(said or ""):
        return ""                                   # the provider said so in their own words
    return "" if HISTORY_SAID.search(said or "") else "PMHx significant for ****."


def dictated_opening(v: dict, para: str) -> tuple[str, dict] | None:
    """A dictated first sentence ("64-year-old right-handed female presents for evaluation of ...", "so this is a
    71 y/o RHD male who presents w/ dizziness ...", "62 y/o F reports ...") as the practice's lead with the reason for
    the visit, and what it says: age, sex, handedness, the reason, the kind of visit and `rest`, the words after the
    reason (the HPI builder keeps those as sentences of their own). The reason comes only from that sentence and only
    when it names a complaint: "84 yo M w/ walker" gives none."""
    para = (para or "").strip()
    m = DICTATED_OPENING.match(para)
    if not m or not 0 < int(m["age"]) < 120:
        return None
    if m["comma"] == "," and not story._topics(re.split(r"[,;.]", para[m.end():], maxsplit=1)[0]):
        return None                      # "38 y/o female, dizzy episodes ..." is an opening; "71 yo F, lives alone" isn't
    sex = ""
    if m["sex"]:
        sex = "female" if m["sex"].lower() in ("female", "woman", "lady", "f") else "male"
    after = para[m.end():]
    first = re.split(r"(?<=[.;?!])\s", after, maxsplit=1)[0]
    lead_with = re.search(r"(?:w/|with)\s*$", m.group(0), re.I)
    pre, reason, rest = "", "", after
    if m["comma"] != "." and m["says"] is None:      # the next sentence, or "says ...", isn't the reason
        complaint = COMPLAINT_START.search(re.split(r"[,;.]", after, maxsplit=1)[0])
        if complaint:                    # "w/ hx migraine c/o dizziness x4 mo": the reason is what comes after "c/o"
            pre, after = after[: complaint.start()].strip(" ,;"), after[complaint.end():]
        reason, rest = _split_reason(after)
        if not reason and not complaint:
            # "84 yo M w/ hx of falls, here for dizziness": what came after "w/" is said, not the reason
            later = COMPLAINT_START.search(first)
            if later:
                reason, rest = _split_reason(first[later.end():])
                if reason:
                    pre = ((lead_with.group(0).strip() + " ") if lead_with else "") + first[: later.start()].strip(" ,;")
                    rest += after[len(first):]
            if not reason:
                pre, rest = "", ((lead_with.group(0).strip() + " ") if lead_with else "") + after
    hand = m["hand"] or m["hand2"] or m["handb"] or m["hand2b"] or ""
    facts = {"age": m["age"], "sex": sex, "hand": hand[:1].upper(), "reason": reason, "rest": rest, "kind": m["kind"] or m["fu"] or "", "pre": pre}
    view = dict(v)
    if not _box(view, "HPI-age"):
        view["HPI-age"] = facts["age"]
    if sex and not _sex(view):
        view["hpi-female"], view["hpi-male"] = sex == "female", sex == "male"
    lead = _lead(view, facts["hand"] or _hand(para), _kind(v, facts["kind"]))
    return lead + (reason + "." if reason else ""), facts


def _an(text: str) -> str:
    """"a 81 y/o" -> "an 81 y/o"."""
    return re.sub(r"\bis a ((?:8\d*|11|18) y/o)\b", r"is an \1", text)


def _opening_rest(rest: str) -> str:
    """The words after the reason in a dictated opening, as sentences of their own. A time first becomes the onset:
    "maybe 6 or 8 months, worse with holding cup" -> "Onset possibly 6 or 8 months ago. Worse with holding cup"."""
    rest = re.sub(r"^[\s,;:.]+", "", rest or "")
    if not rest:
        return ""
    m = re.match(r"([^,;.]*)(?:[,;.]\s*|$)", rest)
    onset = story.onset_phrase(m.group(1)) if m and m.group(1).strip() else ""
    if onset:
        after = rest[m.end():].strip()
        first = "Longstanding hx." if onset == "Longstanding" else f"Onset {onset}."
        # a few words that only say more about the onset stay with it: "Onset 9 days ago, morning after a cold."
        tail = re.match(r"([^,;.]+)(?:[,;.]\s*|$)", after)
        if tail and len(tail.group(1).split()) <= 6 and not re.search(
                r"\b(?:is|was|are|were|has|had|have|reports?|says?|feels?|denies|worse|better|lasts?|lasting|started|gets|got|takes?|"
                r"uses?|no|not|never|w/|with)\b", tail.group(1), re.I):
            first = first[:-1] + ", " + tail.group(1).strip() + "."
            after = after[tail.end():].strip()
        return first + (" " + after[:1].upper() + after[1:] if after else "")
    return rest[:1].upper() + rest[1:]


def _old_reason(opening: str) -> str:
    """The reason an earlier build's opening gave, when it is a real one (not *****)."""
    lead = CLINIC_LEAD.match(opening or "")
    if not lead:
        return ""
    reason = re.split(r"\.\s|\.$", opening[lead.end():], maxsplit=1)[0].strip()
    return "" if "*" in reason else reason


def _opening(v: dict, hpi: str, last_seen: str, said: str = "", reason: str = "", kind: str | None = None) -> str:
    sex = _sex(v) or BLANK
    title = {"female": "Ms.", "male": "Mr."}.get(sex, "Mr./Ms.")
    last = split_name(v.get("Name", ""))[0] or "****"
    age = _age(v) or BLANK
    hand = _hand(hpi) or BLANK
    reason = reason or _reason(v)
    if follow_up(v):
        m = re.search(r"Last seen on (\d{1,2}/\d{1,2}/\d{2,4})", hpi)
        seen = last_seen or (m.group(1) if m else "**/**/**")
        return _an(phrases.HPI_OPENING_FOLLOWUP.format(title=title, last=last, age=age, hand=hand, sex=sex, reason=reason, last_seen=seen))
    kind = _kind(v) if kind is None else kind
    first = _an(phrases.HPI_OPENING.split(" PMHx")[0].format(title=title, last=last, age=age, hand=hand, sex=sex,
                                                              reason=(kind + " " if kind else "") + reason))
    return f"{first} {_pmhx(v, said, hpi)}".strip()


# ----------------------------------------------------------------- the provider's words the boxes don't already say

# A box's section, by the prefix its name starts with; and the text boxes each section's paragraph prints.
SECTION_OF = {"dizziness": "dizziness", "hearingloss": "hearing", "tinnitus": "tinnitus", "gaitataxia": "gait", "headache": "headache"}
SLOT_HEAD = {"DizzOnsetDate": "dizziness", "DizzyDesc": "dizziness", "DizzFreq": "dizziness", "better-thesemeds": "dizziness",
             "HLOnsetDate": "hearingloss", "HLdesc": "hearingloss", "TinnitusOnsetDate": "tinnitus", "tinnitus-descr": "tinnitus",
             "GAOnsetDate": "gaitataxia", "gaitataxia-descr": "gaitataxia", "HOnsetDate": "headache", "headache-descr": "headache",
             "h-freq-wk": "headache", "h-freq-month": "headache", "h-better-thesemeds": "headache"}
# Words that carry nothing a box doesn't: grammar, the reporting verbs, the cue words the paragraphs write themselves.
STOP = set("""a an the of in on at to for from by with w and or but also then as is are was were be been being has have had do does did
    it its this that these those there here she he her his him they them their i me my pt patient reports reported report says said
    states stated c o complains endorses endorsed notes feels felt feel describes described like when while if so very really still
    now worse better lasting lasts last lasted assoc associated occurs occur occurring started starting start began begun since onset
    ago x approximately about around symptoms symptom sxs sx episode some any hx pmhx pmh history significant presents presenting""".split())
SYNONYM = {"mo": "month", "mos": "month", "wk": "week", "wks": "week", "yr": "year", "yrs": "year", "hr": "hour", "hrs": "hour",
           "min": "minute", "mins": "minute", "sec": "second", "secs": "second", "sometimes": "time", "times": "time",
           "possibly": "possible", "ha": "headache", "ad": "right", "as": "left", "au": "both", "r": "right", "l": "left",
           "bilat": "both", "bilateral": "both", "hl": "hearing", "snhl": "hearing"}
TOKEN_RE = re.compile(r"[a-z]+|\d+(?:\.\d+)?")


def _stem(word: str) -> str:
    w = SYNONYM.get(word, word)
    if w.endswith("ness"):
        w = w[:-4]
    for suffix in ("ing", "ed", "es", "s", "ly"):
        if w.endswith(suffix) and len(w) - len(suffix) >= 3:
            w = w[: -len(suffix)]
            break
    return w.rstrip("e") if len(w) > 3 else w


def _content(text: str) -> list[str]:
    # the clinic's ear shorthand is a side only in capitals ("as" is a word)
    text = re.sub(r"\b(AD|AS|AU)\b", lambda m: {"AD": " right ", "AS": " left ", "AU": " both "}[m.group(1)], text)
    return [_stem(w) for w in TOKEN_RE.findall(text.lower()) if w not in STOP and w not in ("as", "ad")]


def _group(v: dict, name: str, value) -> str:
    """Which drafted paragraph prints this box: a symptom's own ("dizziness", ...) or the negatives line."""
    if name in HEADS:
        return name
    if (name.endswith("-denies") and name[: -len("-denies")] in HEADS) or name in GAIT_DENIALS or name.startswith("ga-falls-"):
        return "negatives"
    if name in SLOT_HEAD:
        return SLOT_HEAD[name]
    if name.startswith("hist-"):
        return "pmhx"
    if name == "OnsetDate":
        return next((SLOT_HEAD[n] for n in story.ONSET_FIELD.values() if _box(v, n) == str(value)), "")
    return next((h for h, prefixes in DETAIL_PREFIX.items() if name.startswith(prefixes)), "")


def _printed(v: dict, name: str, value) -> bool:
    """The note's drafted paragraphs say this box: it is ticked (or filled) and its section is written out."""
    if name in HEADS:
        return value is True and bool(v.get(name))
    if name.endswith("-denies") and name[: -len("-denies")] in HEADS:
        return value is True and bool(v.get(name)) and not v.get(name[: -len("-denies")])
    if name in GAIT_DENIALS or name in ("ga-falls-yes", "ga-falls-no") or name.startswith("hist-"):
        return value is True and bool(v.get(name))           # history: printed when the opening's PMHx is (see _covered)
    if name == "OnsetDate":
        return any(_printed(v, n, value) for n in story.ONSET_FIELD.values())
    if name in SLOT_HEAD:
        return bool(v.get(SLOT_HEAD[name])) and _box(v, name) == str(value)
    if value is not True or FIELDS.get(name, {}).get("type") != "checkbox" or name.endswith(("-other", "-descr")):
        return False
    head = next((h for h, prefixes in DETAIL_PREFIX.items() if name.startswith(prefixes)), "")
    return bool(head) and bool(v.get(head)) and bool(v.get(name))


def _mask(text: str, name: str) -> str:
    """The words in `text` that ticked this box, blanked out."""
    rxs = []
    head = name[: -len("-denies")] if name.endswith("-denies") else name
    if head == "dizziness":
        rxs.append(re.compile(r"\bdizz\w*", re.I))     # "lightheaded", "rocking", "spinning": a description, said by its own box
    elif head in SECTION_OF:
        rxs.append(story.TOPIC_RE[SECTION_OF[head]])
        if name.endswith("-denies"):
            rxs += [story.DENIAL, story.NORMAL_WORDS]
    elif name in GAIT_DENIALS:
        rxs += [re.compile(rf"(?<![a-z])(?:{story.GAIT_DENY[name]})(?![a-z])", re.I), story.DENIAL,
                re.compile(r"\b(?:legs?|lower extremit\w*|ble|feet|incontinence)\b", re.I)]
    elif name == "ga-falls-yes":
        rxs += [story.FALLS_YES, re.compile(r"\bfalls?\b", re.I)]
    elif name == "ga-falls-no":
        rxs += [story.FALLS_NO, story.DENIAL, re.compile(r"\bfalls?\b", re.I)]
    elif name.startswith("hist-") and name in story.PMH_RE:
        rxs.append(story.PMH_RE[name])
    else:
        section = next((sec for sec, groups in story.GROUPS.items() for pre in groups.values() if name.startswith(pre)), "")
        if section:
            words = story._words_for(name, section)
            if words:
                rxs.append(re.compile(r"(?<![a-z])(?:" + "|".join(re.escape(w) for w in words) + r")(?![a-z])", re.I))
            key = name.split("-", 1)[1] if "-" in name else name
            if key in story.SIDES:
                rxs += [re.compile(rf"\b(?:{story.SIDES[key]})\b", re.I), re.compile(rf"\b{story.SIDE_CONTEXT}\b", re.I)]
    for rx in rxs:
        text = rx.sub(lambda m: " " * len(m.group(0)), text)
    return text


def _covered(v: dict, text: str, ticks: dict, drafted: dict[str, set[str]]) -> bool:
    """Every word of `text` is already in the drafted paragraphs: it ticked only boxes they print, and what is left
    once those boxes' own words are taken out is in the paragraph that prints them ("Denies headache w/ the attacks"
    isn't said by "Denies headaches." just because another paragraph mentions attacks)."""
    if not ticks or wording.BLANK in text or "?" in text:
        return False
    if not all(_printed(v, k, val) for k, val in ticks.items()):
        return False
    if any(_group(v, k, val) not in drafted for k, val in ticks.items()):
        return False                                  # its paragraph (or the opening's PMHx) isn't written out
    words: set[str] = set()
    for k, val in ticks.items():
        words |= drafted.get(_group(v, k, val), set())
        text = _mask(text, k)
    return all(w in words for w in _content(text))


# A part that only qualifies the one before it ("..., possibly less than a minute", "..., especially at night").
QUALIFIER = re.compile(r"\s*(?:(?:possibly|maybe|probably|perhaps|about|approximately|around|or|mostly|mainly|especially|sometimes|"
                       r"occasionally|only|usually|at least|less|more than|up to)\b|~|\d)", re.I)


# A part that can be a sentence of its own: it has a verb, or a denial, or says how something is.
STANDS_ALONE = re.compile(r"\b(?:is|are|was|were|has|have|had|reports?|reported|says?|feels?|felt|gets?|got|takes?|took|uses?|used|lasts?|"
                          r"lasting|started|began|occurs?|happens?|worse|better|helps?|helped|denies|denied|endorses|c/o|no|not|never|w/|"
                          r"with|w/o|without|\w+ed)\b", re.I)
# A time with no verb of its own ("morning after a cold"): it says when the part before it happened.
WHEN_PART = re.compile(r"\s*(?:the\s+)?(?:morning|night|evening|afternoon|day|days|week|weeks|month|months|year|years|after|before|around|"
                       r"following|during|upon|on waking|a day|a week)\b", re.I)
# A part that hangs on the one before it: "..., around when dizziness started", "..., then fine", "..., since lisinopril".
CONNECTIVE = re.compile(r"\s*(?:around when|right when|when|whenever|after|since|ever since|before|because|then|so|which|that|until|while)\b", re.I)


def _parts(sentence: str) -> list[tuple[str, str]]:
    """A sentence in parts, (separator before it, words): split at commas and before "but", a denial's list kept whole
    ("Denies vertigo, nausea" is one part)."""
    pieces = re.split(r"(,\s*|;\s*|\s+(?=(?:but|although|though|however|whereas)\b))", sentence)
    parts: list[list[str]] = [["", pieces[0]]]
    for i in range(1, len(pieces) - 1, 2):
        parts.append([pieces[i], pieces[i + 1]])
    merged: list[list[str]] = []
    for sep, words in parts:
        if merged and story.DENIAL.search(merged[-1][1]) and "," in sep and not re.match(
                r"\s*(?:but|although|though|however|whereas|has|have|had|reports?|endorses?|w/|with|also|now|more|mostly|mainly|rather|"
                r"instead|just)\b", words, re.I):
            merged[-1][1] += sep + words                 # still inside the denial ("denies X, Y")
        else:
            merged.append([sep, words])
    return [(sep, words) for sep, words in merged if words.strip()]


def _uncovered(v: dict, sentence: str, rec: dict, final: dict, drafted: dict[str, set[str]], topics: list[str]) -> str:
    """What is left of one of the provider's sentences once the parts the drafted paragraphs already say are taken out;
    the sentence unchanged when nothing can safely go."""
    if rec["cut"] or any(kind != "hedge" for kind in rec["unclear"]):
        return sentence                                 # unfinished, conflicting, past: kept as written for the provider
    if story.DOUBLE_NEG.search(sentence):
        return sentence                                 # "never free of the ringing": two negatives are never taken apart
    if any(final.get(k) != val for k, val in rec["set"].items()):
        return sentence                                 # said both ways somewhere in the notes
    ticks = {k: val for k, val in rec["set"].items() if val is not False}
    if _covered(v, sentence, ticks, drafted):
        return ""
    end = re.search(r"[.;?!]*$", sentence.rstrip()).group(0)[-1:] or "."
    parts = _parts(sentence.rstrip(" .;?!"))
    if len(parts) < 2:
        return sentence
    drop = []
    for sep, words in parts:
        r = story.read_notes(words, meds=[], topics=rec["topics"] or topics)
        uticks = {k: val for k, val in r.set.items() if val is not False}
        doubts = [kind for one in r.sentences for kind in one["unclear"]]
        drop.append(bool(uticks) and all(kind == "hedge" for kind in doubts) and all(rec["set"].get(k) == val for k, val in uticks.items())
                    and _covered(v, words, uticks, drafted))
    for i in range(1, len(parts)):
        if story.ELLIPTIC_NOT.match(parts[i][1]):
            drop[i] = drop[i - 1] = False               # "helps the headache but not the dizzy": one thought
        if CONNECTIVE.match(parts[i][1]) and not drop[i - 1]:
            drop[i] = False                             # "..., around when dizziness started" says when, about what came before
        if not drop[i] and (QUALIFIER.match(parts[i][1]) or CONNECTIVE.match(parts[i][1])):
            drop[i - 1] = False                         # "lasts seconds, possibly less than a minute", "..., then fine",
                                                        # "..., around when dizziness started": about what is before it
    for i in range(len(parts)):                         # the part that says what the sentence is about stays with the rest
        if drop[i] and story._topics(parts[i][1]) and any(not drop[j] and not story._topics(parts[j][1]) for j in range(i + 1, len(parts))):
            drop[i] = False
    if not any(drop):
        return sentence
    kept = [p for p, d in zip(parts, drop) if not d]
    if not kept:
        return ""
    for i in range(1, len(parts)):                      # what is left must stand on its own: never "Not AD.", "Fine." or
        words = parts[i][1]                             # "Morning after a cold." (the onset it was about gone)
        if drop[i - 1] and not drop[i] and (re.match(r"\s*(?:and\s+|but\s+)?(?:not|no|never|nor|neither)\b", words, re.I)
                                            or len(re.findall(r"[A-Za-z0-9]+", words)) <= 2
                                            or (WHEN_PART.match(words) and not (STANDS_ALONE.search(words) or story._topics(words)))):
            return sentence
    first = re.sub(r"^\s*(?:and|but|although|though|however|whereas|also|then)\b[\s,]*", "", kept[0][1])
    text = first + "".join(sep + words for sep, words in kept[1:])
    return text[:1].upper() + text[1:] + end


def _residual(v: dict, text: str, paragraphs: dict[str, str], topics: list[str] | None = None) -> str:
    """The provider's own words in the clinic's wording, without what the drafted paragraphs (by symptom, and the
    negatives) already say. Repeated until nothing changes, so a rebuild of the result leaves it as it is."""
    text = wording.clinical(text)
    drafted = {key: set(_content(words)) for key, words in paragraphs.items()}
    topics = story.topics_of(v) if topics is None else topics
    for _ in range(4):
        reading = story.read_notes(text, meds=[], topics=topics)
        recs = iter(reading.sentences)
        lines = []
        for line in text.split("\n"):
            out = []
            for sentence in story.sentences(line):
                rec = next(recs, None)
                if rec is None or rec["text"] != sentence:
                    out.append(sentence)
                    continue
                left = _uncovered(v, sentence, rec, reading.set, drafted, topics)
                if left:
                    out.append(left)
            lines.append(" ".join(out))
        new = wording.clinical("\n".join(lines))
        if new == text:
            break
        text = new
    return text


def _hpi(v: dict, previous: list[str], last_seen: str, todo: list[str], check: list[str] | None = None,
         topics: list[str] | None = None) -> Proposal | None:
    cur = _text(v.get(HPI))
    check = check if check is not None else []
    prev = {p.strip() for p in previous}
    opening_line, closer, kept = "", "", []
    for line in cur.split("\n"):
        s = line.strip()
        if not s:
            kept.append("")
        elif not opening_line and OPENING_RE.match(s):
            opening_line = s
        elif s in phrases.HPI_CLOSERS:
            closer = s
        elif s not in prev:                    # what the builder wrote last time is written again below
            for c in phrases.HPI_CLOSERS:      # the closing line typed on the same line as notes
                if c in s:
                    closer, s = c, s.replace(c, " ").strip()
            if s:
                kept.append(s)
    body = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()
    paras = [x.strip() for x in re.split(r"\n\s*\n", body) if x.strip()]
    lead = CLINIC_LEAD.match(opening_line) if opening_line else None
    mine = bool(opening_line) and opening_line not in prev       # typed, edited or carried, not what the builder wrote
    dictated = dictated_opening(v, wording.clinical(paras[0])) if paras else None
    built = ""
    if lead and mine and "*****" not in opening_line[lead.end():]:
        # the practice's opening with real content after it: refresh its details, keep every word after them. The kind
        # of visit follows the visit type chosen on the note (a note carried forward from the last visit opens as a
        # follow-up; booked as a new consult, it reads as one), and "Last seen on" goes with a follow-up only.
        rest = opening_line[lead.end():]
        if follow_up(v):
            if "Last seen on" not in rest and last_seen:
                first_stop = re.match(r"(.*?\.)(\s+.*)?$", rest, re.S)      # after the reason, before anything else said
                rest = (first_stop.group(1) + f" Last seen on {last_seen}." + (first_stop.group(2) or "")) if first_stop \
                    else rest.rstrip() + f". Last seen on {last_seen}."
        else:
            rest = re.sub(r"\s*Last seen on \S+\.?", "", rest)
        opening = _lead(v, _hand(cur), _kind(v) or lead["kind"], lead.groupdict()) + rest
    elif dictated and not (mine and "*" not in opening_line):
        # a dictated opening ("64-year-old right-handed female presents for ...") becomes the practice's lead and the
        # reason; what it says after the reason stays, as the provider's own sentences
        facts = dictated[1]
        reason = _reason(v, facts["reason"])
        rest = facts["rest"] if reason.lower() == facts["reason"].lower() else (facts["reason"] + " " + facts["rest"]).strip()
        pre = facts["pre"][:1].upper() + facts["pre"][1:] + ". " if facts["pre"] else ""     # "Hx migraine." before the rest
        paras = [p for p in [(pre + _opening_rest(rest)).strip()] + paras[1:] if p]
        opening = built = _opening(v, cur, last_seen, "\n\n".join(paras), reason, _kind(v, facts["kind"]))
    elif mine and "*" not in opening_line:
        opening = opening_line
    else:
        old = _old_reason(opening_line) if opening_line and not mine else ""
        opening = built = _opening(v, cur, last_seen, body, _reason(v, old), _kind(v, lead["kind"] or "") if lead else None)

    fu = follow_up(v)
    said = "\n\n".join(paras)
    topics = story.topics_of(v) if topics is None else topics
    order = _order(v)
    # On a follow-up, a symptom carried from the last visit that the notes say is over gets no present-tense paragraph.
    over: set[str] = set()
    if fu and said.strip():
        reading = story.read_notes(said, meds=[], topics=topics)
        for sec in reading.past:
            head = story.SECTION_HEAD[sec]
            if v.get(head) and reading.set.get(head) is not True:
                over.add(head)
                check.append(f"The boxes say current {DENIED[head]}; your notes say it is over. Untick it, or mark it improving.")
    order = [k for k in order if k not in over]
    # the notes' own words for the symptoms, as the note has them now (the builder's lines included, so a rebuild
    # words them the same way): "dizziness" or "vertigo", "imbalance" or "gait ataxia"
    words_now = "\n".join(ln for ln in cur.split("\n") if not OPENING_RE.match(ln.strip()))
    said_words = {"dizziness": words_now + "\n" + _box(v, "DizzyDesc"),
                  "gaitataxia": words_now + "\n" + _box(v, "gaitataxia-descr") + "\n" + _box(v, "ChiefComplaint")}
    paragraphs = _draft(v, order, said_words)
    paragraphs["negatives"] = " ".join(_falls(v, fu) + _negatives(v))
    if opening == built and "PMHx significant for" in opening and "****" not in opening.split("PMHx", 1)[1]:
        paragraphs["pmhx"] = opening.split("PMHx", 1)[1]
    residual = _residual(v, said, paragraphs, topics)
    if opening == built and residual:
        first = story.sentences(residual)[0] if story.sentences(residual) else ""
        m = VISIT_FOR.match(first)
        if m and re.fullmatch(r"[\s.]*", first[m.end():]) and _split_reason(m["r"])[0] and \
                _split_reason(m["r"])[0].lower() in opening.lower():
            residual = residual.replace(first, "", 1).strip()           # "F/u for BPPV." under "... a follow-up on BPPV."
    # Both ways: what the provider's kept words say ("Occasionally worse w/ standing", "Vertigo attacks 2x/month ...") is
    # left out of the drafted paragraphs, and a paragraph that would be only its first line goes.
    again = _said_boxes(v, residual, topics)
    view = {k: (False if k in again and k not in HEADS and k not in HEADLINE_NOUN else val) for k, val in v.items()}
    view.update({k: "" for k in again if k in story.SLOTS})
    drafted = _draft(view, order, said_words, whole=True)
    symptoms: list[str] = []
    by_symptom: dict[str, list[str]] = {}            # each complaint's own sentences, for the Build card's ticks
    for key in order:
        lines = drafted.get(key, [])
        nouns = {key: True, **{n: v.get(n) for n in HEADLINE_NOUN}}
        bare = [x for x in SECTION_FN[key](nouns, said_words.get(key, "")) if x] if key in said_words else \
            [x for x in SECTION_FN[key](nouns) if x]
        if key in again and lines == bare[:1]:
            continue                                  # "Reports imbalance." under "Imbalance worse in the dark ...": said
        by_symptom[key] = [x for x in lines if x and x not in symptoms]
        symptoms += lines
    symptoms = [x for x in dict.fromkeys(symptoms) if x]
    have = opening + "\n" + residual
    sym_para = " ".join(x for x in symptoms if x not in have)
    have_all = have + "\n" + sym_para
    negatives = [x for x in dict.fromkeys(_falls(v, fu, residual) + _negatives(v, have_all)) if x and x not in have and x not in sym_para]
    neg_para = " ".join(negatives)
    if not closer and not any(c in residual for c in phrases.HPI_CLOSERS):
        closer = phrases.HPI_CLOSERS[0]
    # a paragraph for each complaint, the chief complaint's first
    sym_paras = [" ".join(x for x in by_symptom.get(key, []) if x not in have) for key in order]
    sym_paras = [p for p in sym_paras if p]
    text = "\n\n".join(p for p in (opening, *sym_paras, residual, neg_para, closer) if p)
    # The same text as parts the provider can leave out one by one: the opening, a paragraph per complaint, their own
    # words, the negatives, the closing line. Joined in this order they are `text` again.
    parts = [{"key": "opening", "title": "Opening", "text": opening, "fixed": opening != built}]
    for key in order:
        own = " ".join(x for x in by_symptom.get(key, []) if x not in have)
        if own:
            parts.append({"key": key, "title": HEAD_TEXT[key], "text": own, "fixed": False})
    if residual:
        parts.append({"key": "notes", "title": "Your notes", "text": residual, "fixed": True})
    if neg_para:
        parts.append({"key": "negatives", "title": "Pertinent negatives", "text": neg_para, "fixed": False})
    if closer:
        parts.append({"key": "closer", "title": "Closing line", "text": closer, "fixed": False})

    if opening == built:
        missing = []
        if "Mr./Ms." in opening:
            missing.append("sex (on Patient)")
        if f"{BLANK}HD" in opening:
            missing.append("handedness (replace ***HD with RHD or LHD)")
        if f" {BLANK} y/o" in opening:
            missing.append("age / DOB")
        if " **** " in re.split(r" is an? ", opening)[0] + " ":
            missing.append("the patient's name")
        if "*****" in opening:
            missing.append("the reason for the visit")
        if "PMHx significant for ****" in opening:
            missing.append("past medical history")
        if "**/**/**" in opening:
            missing.append("the last visit date")
        if missing:
            todo.append("HPI opening still needs " + _join(missing) + ".")
    if " ".join(text.split()) == " ".join(cur.split()):
        return None
    return Proposal("hpi", "History of present illness",
                    "Opening line, a paragraph for each complaint from its boxes, your notes in the clinic's wording (what the "
                    "boxes already say left out), negatives and the closing line. Untick a paragraph to leave it out.",
                    {HPI: text}, field=HPI, text=text, current=cur, generated=[x for x in (built, *sym_paras, neg_para) if x],
                    parts=parts)


# The boxes that are the paragraph's noun ("Pt reports vertigo"): kept in the headline even when the notes say them.
HEADLINE_NOUN = ("assoc-vertigo", "assoc-diseq", "assoc-cbd")
SECTION_FN = {"dizziness": _dizziness, "hearingloss": _hearing, "tinnitus": _tinnitus, "gaitataxia": _gait, "headache": _headache}
# The association boxes and what each names: another section's symptom, or something with no paragraph of its own.
ASSOC_TARGET = {
    "dizziness": {"assoc-headache": "headache", "assoc-tinnitus": "tinnitus", "assoc-hearingloss": "hearingloss", "assoc-auralfullness": "aural"},
    "hearingloss": {"hlassoc-vertigo": "dizziness", "hlassoc-diseq": "dizziness", "hlassoc-headache": "headache", "hlassoc-tinnitus": "tinnitus",
                    "hlassoc-hearingfluc": "fluc", "hlassoc-aural": "aural"},
    "tinnitus": {"t-assoc-vertigo": "dizziness", "t-assoc-diseq": "dizziness", "t-assoc-headache": "headache", "t-assoc-hearingloss": "hearingloss",
                 "t-assoc-fluc": "fluc", "t-assoc-aural": "aural"},
    "gaitataxia": {"ga-assoc-headache": "headache"},
    "headache": {"h-assoc-vertigo": "dizziness", "h-assoc-diseq": "dizziness", "h-assoc-hearing": "hearingloss", "h-assoc-fluc": "fluc",
                 "h-assoc-aural": "aural", "h-assoc-tinnitus": "tinnitus"},
}


def _order(v: dict) -> list[str]:
    """The symptoms ticked, the chief complaint's first, then the form's order."""
    cc = [story.SECTION_HEAD[sec] for sec in story.topics_of({"ChiefComplaint": v.get("ChiefComplaint")})] if _box(v, "ChiefComplaint") else []
    return [k for k in dict.fromkeys(cc + list(SECTION_FN)) if v.get(k)]


def _draft(v: dict, order: list[str], said: dict, whole: bool = False) -> dict:
    """Each symptom's paragraph from the boxes (as one string, or with `whole` as its sentences). An association is
    written once: "Assoc w/ tinnitus" in the dizziness paragraph isn't said again as "assoc w/ vertigo" under
    tinnitus, nor aural fullness under every symptom."""
    pairs: set[frozenset] = set()
    items: set[str] = set()
    out: dict = {}
    for key in order:
        view = dict(v)
        for box, target in ASSOC_TARGET.get(key, {}).items():
            if not v.get(box):
                continue
            if target in HEADS and v.get(target) and target in order:
                pair = frozenset((key, target))
                if pair in pairs:
                    view[box] = False
                pairs.add(pair)
            else:
                if target in items:
                    view[box] = False
                items.add(target)
        fn = SECTION_FN[key]
        lines = [x for x in (fn(view, said.get(key, "")) if key in said else fn(view)) if x]
        out[key] = lines if whole else " ".join(lines)
    return out


def _said_boxes(v: dict, residual: str, topics: list[str]) -> set[str]:
    """The boxes (and text boxes) the provider's kept words say plainly, ticked on the note too."""
    if not residual.strip():
        return set()
    r = story.read_notes(residual, meds=[], topics=topics)
    out = set()
    for k, val in r.set.items():
        if val is True and v.get(k) is True and FIELDS.get(k, {}).get("type") == "checkbox":
            out.add(k)
        elif k in story.SLOTS and val and _box(v, k) == str(val):
            out.add(k)
    return out


# --------------------------------------------------------------------------- exam, oculography

def _exam(v: dict, todo: list[str]) -> Proposal | None:
    changes, lines = {}, []
    for row in SCHEMA["exam_rows"]:
        f = row["findings"]
        if _box(v, row["abnormal"]):
            if f and not _box(v, f):
                todo.append(f"Exam: \"{row['label']}\" is marked abnormal with no finding written.")
            continue
        if not f or not row["normal"] or not v.get(row["normal"]) or _box(v, f):
            continue
        if f in phrases.EXAM_NORMAL_TEXT:
            changes[f] = phrases.EXAM_NORMAL_TEXT[f]
            lines.append(f"{row['label']}: {changes[f]}")
    if not changes:
        return None
    return Proposal("exam", "Exam wording", f"Standard wording on {len(changes)} normal row{'s' if len(changes) != 1 else ''} left blank.",
                    changes, lines=lines)


def _treatment(v: dict, todo: list[str]) -> Proposal | None:
    if _box(v, TREATMENT):
        return None
    found = [dx for n, dx in BPPV.items() if v.get(n)]
    if found:
        todo.append(f"Oculography: write the treatment for {_join(found)} (Epley lines are under Phrases on Tests).")
        return None
    if v.get("imp-normal") and not v.get("imp-atyp"):
        text = phrases.GOGGLE_TREATMENT[0]
        return Proposal("treatment", "Oculography treatment", "The study is marked normal.", {TREATMENT: text},
                        field=TREATMENT, text=text, current="")
    return None


def _positional(v: dict) -> str:
    if v.get("imp-normal") and not v.get("imp-atyp"):
        return phrases.POSITIONAL_NEGATIVE.format(date=_box(v, "goggle-date") or _box(v, "Date") or "**/**/**")
    atypical = v.get("imp-atyp") or any(v.get(n) for n in v if n.startswith(("dixhall-", "supine-")) and n.endswith(("-atyp", "-atypical")))
    if not atypical:
        return ""
    up = v.get("supine-center-upbeat") or v.get("supine-right-up") or v.get("supine-left-up")
    down = v.get("supine-center-downbeat") or v.get("supine-right-down") or v.get("supine-left-down")
    direction = "upbeat " if up and not down else ("downbeat " if down and not up else "")
    return phrases.POSITIONAL_ATYPICAL.format(direction=direction)


def _ccs_negative(v: dict) -> bool:
    """The practice's "no hx/exam finding to suggest CCS" line: every myelopathy symptom denied, motor / sensory /
    reflex rows normal."""
    rows = {r["normal"]: r for r in SCHEMA["exam_rows"] if r["normal"]}
    exam_ok = all(v.get(n) and not _box(v, rows[n]["abnormal"]) for n in ("normal31", "normal33", "normal36", "normal37"))
    return exam_ok and all(v.get(n) for n in GAIT_DENIALS)


# --------------------------------------------------------------------------- impression / plan

def _impressions(v: dict, previous: list[str], todo: list[str]) -> Proposal | None:
    cur = _text(v.get(IMPRESSION))
    prev = {p.strip() for p in previous}
    kept = [line.rstrip() for line in cur.split("\n") if line.strip() and line.strip() not in prev]
    existing = impressions("\n".join(kept))
    number = max((i.number for i in existing), default=0)
    fu = bool(v.get("neuro-progress") or v.get("neuro-urgent"))
    used: set[str] = set()
    new: list[list] = []          # [number, diagnosis, plan sentences]
    numbers: dict[str, int] = {}

    def listed(dx: str) -> int:
        d = dx.lower()
        for i in existing:
            e = i.diagnosis.lower().rstrip(". ")
            if e and (d == e or d in e or e in d):
                return i.number
        return 0

    def boxes(names: list[str]) -> list[str]:
        out = []
        for n in names:
            if v.get(n) and n not in used:
                used.add(n)
                out.append(phrases.PLAN_SENTENCES[n][1 if fu else 0])
        return out

    def add(key: str, dx: str, plan) -> None:
        nonlocal number
        have = listed(dx)
        if have:
            numbers[key] = have
            return
        number += 1
        numbers[key] = number
        new.append([number, dx, [s for s in plan() if s]])

    gait = bool(v.get("gaitataxia"))
    dizzy_boxes = ["plan-balancelog", "plan-horizontal", "plan-sleep", "plan-driving"] + ([] if gait else ["plan-continue", "plan-fall"])
    bppv = [dx for n, dx in BPPV.items() if v.get(n)]
    for i, dx in enumerate(bppv):
        add(f"bppv{i}", dx, lambda first=(i == 0): ([_sentence(_box(v, TREATMENT))] if _box(v, TREATMENT) and first else [])
            + (boxes(dizzy_boxes) if first else []))
    if not bppv and v.get("dizziness"):
        add("dizziness", "Dizziness and lack of coordination", lambda: [_positional(v)] + boxes(dizzy_boxes))
    dizzy = numbers.get("bppv0") or numbers.get("dizziness")
    if gait:
        add("gait", "Gait and balance difficulties", lambda: ([phrases.IMPRESSION_SECONDARY.format(n=dizzy)] if dizzy else [])
            + boxes(["plan-fall", "plan-continue"]) + ([phrases.CCS_NEGATIVE] if _ccs_negative(v) else []))
    if v.get("headache"):
        migrainous = sum(bool(x) for x in (v.get("h-assoc-photo"), v.get("h-assoc-phono"),
                                            v.get("h-assoc-nausea") or v.get("h-assoc-emesis"), v.get("h-assoc-aura"))) >= 2
        meds = _box(v, "plan-adverse-descr")
        add("headache", "Headaches with migrainous features" if migrainous else "Headaches",
            lambda: boxes(["plan-halog", "plan-meds"]) + ([phrases.PLAN_ADVERSE.format(meds=meds)] if v.get("plan-adverse") and meds else [])
            + boxes(["plan-prophy"]))
    see = lambda rel: [phrases.IMPRESSION_SEE_PLAN[0].format(n=dizzy)] if dizzy and v.get(rel) else []  # noqa: E731
    if v.get("hearingloss"):
        side = _first(v, {f"hl-{k}": ear for k, ear in EAR.items()})
        add("hearing", f"Hearing loss {side}".strip(), lambda: see("assoc-hearingloss"))
    if v.get("tinnitus"):
        side = _first(v, {f"t-{k}": ear for k, ear in EAR.items()})
        add("tinnitus", f"Tinnitus {side}".strip(), lambda: see("assoc-tinnitus"))
    if new and v.get("plan-return") and "plan-return" not in used:
        new[0][2] += boxes(["plan-return"])

    lines = [f"{n}) {dx} - {' '.join(plan) if plan else BLANK}" for n, dx, plan in new]
    for n, dx, plan in new:
        if not plan:
            todo.append(f"Impression #{n} ({dx}): write the plan.")
    text = "\n".join(kept + lines)
    if text.strip() == cur.strip():
        return None
    return Proposal("impression", "Impression / plan", "Numbered impressions for what you ticked, with your plan boxes. Ones already on the note stay.",
                    {IMPRESSION: text}, field=IMPRESSION, text=text, current=cur, generated=lines)


# "F/u for BPPV.", "Here for imbalance.": the reason for the visit said at the start of the notes.
VISIT_FOR = re.compile(r"^\W*(?:f/u|follow[- ]?up|here|presents?|presenting|seen|referred|returns?|c/o|complains? of)\s+(?:visit\s+)?"
                       r"(?:for|of|on|re:?|with|w/|regarding)?\s*(?P<r>[^.;,]+)", re.I)


def _visit_reason(text: str) -> str:
    first = next(iter(story.sentences(wording.clinical(text or ""))), "")
    m = VISIT_FOR.match(first)
    return _split_reason(m["r"])[0] if m else ""


def _chief_complaint(v: dict, facts: dict | None = None) -> Proposal | None:
    if _box(v, "ChiefComplaint"):
        return None
    reason = (facts or {}).get("reason", "")
    if reason:
        text = reason[:1].upper() + reason[1:]
        if len(text) > 60:
            text = text[:60].rsplit(" ", 1)[0]
        return Proposal("cc", "Chief complaint", "From the reason for the visit you dictated.", {"ChiefComplaint": text},
                        field="ChiefComplaint", text=text, current="")
    names = present(v)
    if not names:
        return None
    text = "Multiple complaints" if len(names) >= 3 else _join([names[0]] + [n.lower() for n in names[1:]])
    return Proposal("cc", "Chief complaint", "From the symptoms marked present.", {"ChiefComplaint": text},
                    field="ChiefComplaint", text=text, current="")


SHORT_SECTION = {"dizziness": "Dizziness", "hearing": "Hearing", "tinnitus": "Tinnitus", "gait": "Gait", "headache": "Headache",
                 "pmh": "History", "social": "Social", "family": "Family", "hpi": "Complaint"}


GROUP_WORDS = {"attacks associated": "with", "associated": "with", "symptoms worse": "worse with", "worse": "worse with",
               "symptoms better": "better with", "better": "better with", "symptom course": "", "duration": "lasts", "occurs": "occurs when",
               "side": "", "affects adl": "affects ADLs:", "falls": "falls:"}
HEAD_TEXT = {"dizziness": "Dizziness", "hearingloss": "Hearing loss", "tinnitus": "Tinnitus", "gaitataxia": "Gait ataxia", "headache": "Headache"}
FIELD_ORDER = {n: i for i, n in enumerate(FIELDS)}


def box_text(name: str, value=True) -> str:
    """"Dizziness: worse with standing" for the list of boxes a proposal ticks."""
    spec = FIELDS[name]
    if name in HEADS:
        return f"{HEAD_TEXT[name]}: present"
    if name.endswith("-denies") and name[: -len("-denies")] in HEADS:
        return f"{HEAD_TEXT[name[: -len('-denies')]]}: patient denies"
    if name.startswith("ga-falls-"):
        return "Falls: " + ("yes" if name.endswith("yes") else "no")
    sec = SHORT_SECTION.get(spec.get("section", ""), (spec.get("section") or "").title())
    group = (spec.get("group") or "").strip().lower()
    label = spec.get("label", name)
    if spec["type"] != "checkbox":
        return f"{sec}: {label}: {value}"
    if spec.get("section") in ("pmh", "social", "family") or label.lower().startswith(("denies", "hx", "history")):
        return f"{sec}: {label}"
    lead = next((w for g, w in GROUP_WORDS.items() if group.startswith(g)), group)
    return f"{sec}: {lead + ' ' if lead else ''}{_word(name)}"


def boxes_proposal(pid: str, title: str, why: str, wanted: dict, values: dict, whys: dict | None = None,
                   todo: list[str] | None = None) -> Proposal | None:
    """Boxes to tick one by one. A box already ticked is skipped; one whose twin the provider ticked the other way is
    a conflict for the provider, not a change."""
    whys = whys or {}
    items, seen = [], set()
    for name, val in wanted.items():
        if name not in FIELDS or name in seen or val is False or val in ("", None):
            continue
        spec = FIELDS[name]
        if spec["type"] == "checkbox":
            if values.get(name):
                continue
            twin = story._twin(name)
            if twin in FIELDS and values.get(twin) and twin != name:
                if todo is not None:
                    todo.append(f"Your words say \u201c{box_text(name)}\u201d but the box says \u201c{box_text(twin)}\u201d.")
                continue
            changes = {name: True}
            if twin in FIELDS and twin != name:
                changes[twin] = False
            for other, ov in wanted.items():               # the named medication goes with "better with medications"
                if other in FIELDS and FIELDS[other]["type"] != "checkbox" and ov and other == story.MED_TEXT.get(name) and not _box(values, other):
                    changes[other] = ov
                    seen.add(other)
            text = box_text(name)
            if story.MED_TEXT.get(name) in changes:
                text += f" ({changes[story.MED_TEXT[name]]})"
            items.append({"text": text, "changes": changes, "why": whys.get(name, ""), "section": story.section_of(name)})
        elif not _box(values, name) and name not in story.MED_TEXT.values():
            items.append({"text": box_text(name, val), "changes": {name: val}, "why": whys.get(name, ""), "section": story.section_of(name)})
        seen.add(name)
    if not items:
        return None
    items.sort(key=lambda it: min(FIELD_ORDER.get(k, 10**6) for k in it["changes"]))
    return Proposal(pid, title, why, {k: v for it in items for k, v in it["changes"].items()}, items=items)


def _provider_text(v: dict, previous: list[str]) -> str:
    """The provider's own words in the HPI: what the builder wrote last time, the template opening and the closing
    line taken out."""
    prev = {p.strip() for p in previous}
    out = []
    for line in _text(v.get(HPI)).split("\n"):
        st = line.strip()
        if not st or st in prev or st in phrases.HPI_CLOSERS:
            continue
        if OPENING_RE.match(st) and ("*" in st or CLINIC_LEAD.match(st)):
            # the opening's own words are only the reason for the visit; anything said after its first sentence counts
            rest = st[CLINIC_LEAD.match(st).end():] if CLINIC_LEAD.match(st) else ""
            rest = re.sub(r"\s*Last seen on \S+\.?", "", rest)
            rest = rest.split(". ", 1)[1] if ". " in rest else ""
            if rest.strip() and "*" not in rest:
                out.append(rest.strip())
            continue
        out.append(st)
    return "\n".join(out)


def _reading_topics(v: dict, text: str) -> list[str]:
    """The sections words that name no symptom belong to: the chief complaint's, else the symptoms ticked, else the
    ones the notes themselves start with. The same on the first Build as after its boxes are applied."""
    topics = story.topics_of(v)
    if topics or _box(v, "ChiefComplaint") or not text.strip():
        return topics
    first = story.read_notes(text, meds=[])
    return [sec for sec, head in story.SECTION_HEAD.items() if first.set.get(head) is True][:1] or []


def _story(v: dict, previous: list[str], out: "Composition", topics: list[str] | None = None) -> Proposal | None:
    text = _provider_text(v, previous)
    complaint = story.topics_of(v) if _box(v, "ChiefComplaint") else []
    if not text.strip():
        out.topics = story.topic_states(None, v, complaint)
        return None
    r = story.read_notes(text, topics=_reading_topics(v, text) if topics is None else topics)
    out.topics = story.topic_states(r, v, complaint)
    age = _age(v)
    if r.facts.get("age") and age and r.facts["age"] != age and parse_date(v.get("DOB", "")):
        out.check.append(f"Your notes say {r.facts['age']} years old; the date of birth makes the patient {age}.")
    if r.facts.get("sex") and _sex(v) and r.facts["sex"] != _sex(v):
        out.check.append(f"Your notes say {r.facts['sex']}; the note is marked {_sex(v)}.")
    out.check += r.unclear
    if r.unfinished:
        out.check.append(f"Unfinished: \u201c{r.unfinished}\u201d")
    out.meds = r.meds
    meds_now = _text(v.get("meds-descr")).lower()
    missing = [m for m in r.meds if m.lower() not in meds_now]
    if missing:
        out.todo.append(f"Your notes mention {_join(missing)}, not on the medication list (Meds).")
    for line in r.med_notes:
        out.todo.append(f"Medication to review (Meds): \u201c{line}\u201d")
    wanted = {k: val for k, val in r.set.items() if k not in ("HPI-age",)}
    for sec in story.topics_of(v) if _box(v, "ChiefComplaint") else []:
        head = story.SECTION_HEAD[sec]
        if wanted.get(head + "-denies") is True:
            wanted.pop(head + "-denies", None)
            wanted.pop(head, None)
            out.check.append(f"Your notes deny {DENIED[head]}, but it is the chief complaint ({_box(v, 'ChiefComplaint')}): "
                             f"\u201c{r.why.get(head + '-denies', '')}\u201d")
    return boxes_proposal("story", "Boxes from your notes", "What your notes say plainly. Hedged, conflicting or unfinished points are listed to check, not ticked.",
                          wanted, v, r.why, out.todo)


def _dictated_facts(v: dict) -> dict | None:
    """Age, sex, handedness and reason from a dictated opening in the HPI, if there is one."""
    lines = [ln.strip() for ln in _text(v.get(HPI)).split("\n")]
    body = "\n".join(ln for ln in lines if not OPENING_RE.match(ln) and ln not in phrases.HPI_CLOSERS)
    for para in re.split(r"\n\s*\n", body):
        if para.strip():
            found = dictated_opening(v, wording.clinical(para.strip()))
            return found[1] if found else None
    return None


def compose_note(values: dict, previous: dict | None = None, last_seen: str = "") -> Composition:
    """What the builder would write into the note. `previous` is {field: [text written last time]}."""
    v = coerce(values or {})
    previous = previous or {}
    out = Composition()
    if not any(v.get(n) for n in ("neuro-consult", "neuro-eval", "neuro-progress", "neuro-urgent")):
        out.todo.append("Choose the visit type on Patient: the HPI opening depends on it.")
    facts = _dictated_facts(v)
    text = _provider_text(v, previous.get(HPI) or [])
    if not (facts or {}).get("reason"):
        said_for = _visit_reason(text)
        if said_for:
            facts = dict(facts or {}, reason=said_for)
    extra: dict = {}                        # boxes the dictated opening fills (age, sex), written with the HPI
    if facts and facts.get("age"):
        if not _box(v, "HPI-age") and not parse_date(v.get("DOB", "")):
            extra["HPI-age"] = facts["age"]
        if facts.get("sex") and not _sex(v):
            extra["hpi-female"], extra["hpi-male"] = facts["sex"] == "female", facts["sex"] == "male"
        v.update(extra)
    cc = _chief_complaint(v, facts)
    if cc:
        v["ChiefComplaint"] = cc.text      # the HPI's reason for the visit reads it
    topics = _reading_topics(v, text)
    boxes = _story(v, previous.get(HPI) or [], out, topics)
    hpi = _hpi(v, previous.get(HPI) or [], last_seen, out.todo, out.check, topics)
    if hpi and extra:
        hpi.changes.update(extra)
    for p in (cc, boxes, hpi, _exam(v, out.todo), _treatment(v, out.todo), _impressions(v, previous.get(IMPRESSION) or [], out.todo)):
        if p:
            out.proposals.append(p)
    if not medlist.reviewed_today(v):
        out.todo.append("Medications: go over the list with the patient and mark it reviewed (Meds).")
    return out
