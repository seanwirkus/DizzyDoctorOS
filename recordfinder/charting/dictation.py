"""Speech to note: transcribe on this computer, then turn what was said into form values.

Transcription uses faster-whisper (Whisper running locally through CTranslate2); the audio never
leaves the PC, so no business associate agreement is involved. The first use downloads the model
(~500 MB for small.en) into the app's data folder.

interpret() is deliberately rule-based: it understands the way the form is organised ("dizziness,
worse with standing, lasts seconds, paroxysmal", "blood pressure 128 over 76", "impression one
dizziness and lack of coordination, monitor clinically"), the practice's exam wording ("exam
otherwise normal, mild sway on Romberg") and puts anything else into the text field the provider is
dictating into, in the clinic's wording ("patient says she's had it for about two months" ->
"Pt reports she's had it for ~2 months.", wording.py), not as a word-for-word transcript. Story said
in a symptom's section goes into the HPI, where the note builder keeps only what the boxes don't
say. A denial ("no", "not", "never", "doesn't") covers only the words it is about, a hedged or past
thought ticks nothing, and spoken numbers and punctuation ("one forty two over eighty eight",
"period") are read as written ones. A whole visit can be dictated in one go: "next section exam",
"plan:" or "impression one" starts the next part, and "build the note" asks the screen to write it
out.
"""
from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass, field

from . import phrases, story, wording
from .note import FIELDS, SCHEMA

VOCABULARY = (
    "Otoneurology progress note. Dizziness, vertigo, disequilibrium, BPPV, Dix-Hallpike, nystagmus, geotropic, "
    "ageotropic, cupulolithiasis, canalithiasis, Epley, vestibulopathy, VNG, VEMP, CDP, posturography, IR oculography, "
    "tinnitus, SNHL, presbyastasis, meclizine, Nurtec, gabapentin, topiramate, Romberg, dysdiadochokinesia, dysmetria, "
    "Hoffman's, papilledema, PERRLA, RRR, Weber, Rinne, saccades, smooth pursuits, gaze-evoked, finger friction rub, "
    "tuning fork, DTRs, longitudinal, incident-to, migraine variant, vestibular migraine, Meniere's."
    " MRI brain, MRI IAC, MRA head, MRA neck, CTA head and neck, CT temporal bone, carotid duplex, DaTscan, Zeto EEG."
)

_model = None
_lock = threading.Lock()


def model_dir(data_dir: str) -> str:
    return os.path.join(data_dir, "whisper")


def available() -> bool:
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


def load_model(size: str = "small.en", data_dir: str = ""):
    """The Whisper model, loaded once. Downloads it on first use."""
    global _model
    with _lock:
        if _model is None:
            from faster_whisper import WhisperModel
            kwargs = {"device": "cpu", "compute_type": "int8"}
            if data_dir:
                kwargs["download_root"] = model_dir(data_dir)
            _model = WhisperModel(size, **kwargs)
        return _model


def load_wav(path: str):
    """A 16-bit PCM WAV as the float32 array Whisper takes (16 kHz, mono). The chart screen records
    straight to this format, so no media decoder is needed on the PC."""
    import wave

    import numpy as np

    with wave.open(path, "rb") as w:
        rate, channels, width, frames = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
        raw = w.readframes(frames)
    if width != 2:
        raise ValueError(f"expected 16-bit PCM, got {width * 8}-bit")
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    if rate != 16000:
        n = int(len(audio) * 16000 / rate)
        audio = np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio).astype(np.float32)
    return audio


def transcribe(audio_path: str, size: str = "small.en", data_dir: str = "") -> str:
    model = load_model(size, data_dir)
    audio = load_wav(audio_path) if audio_path.lower().endswith(".wav") else audio_path
    segments, _info = model.transcribe(audio, language="en", beam_size=5, vad_filter=True,
                                       initial_prompt=VOCABULARY, condition_on_previous_text=False)
    return " ".join(s.text.strip() for s in segments).strip()


# --------------------------------------------------------------------------- interpretation

NUMBER_WORDS = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen "
                                            "fourteen fifteen sixteen seventeen eighteen nineteen twenty".split())}
SECTION_WORDS = {
    "dizziness": "dizziness", "dizzy": "dizziness", "hearing loss": "hearing", "hearing": "hearing",
    "tinnitus": "tinnitus", "gait": "gait", "ataxia": "gait", "headache": "headache", "headaches": "headache",
    "past medical history": "pmh", "medical history": "pmh", "family history": "family", "social history": "social",
    "review of systems": "ros", "labs": "labs", "vitals": "vitals", "exam": "exam", "physical exam": "exam",
    "oculography": "goggles", "goggles": "goggles", "positional testing": "goggles", "studies": "studies",
    "imaging": "studies", "impression": "impression", "assessment": "impression", "plan": "plan",
    "hpi": "hpi", "history of present illness": "hpi", "medications": "meds", "meds": "meds", "allergies": "allergies",
}
# Default text field for free dictation in each section.
SECTION_TEXT = {
    "hpi": "ChiefComplaint-descr", "dizziness": "DizzyDesc", "hearing": "HLdesc", "tinnitus": "t-other-descr",
    "gait": "ga-other-descr", "headache": "headache-descr", "pmh": "family-other-descr", "family": "family-other-descr",
    "social": "family-other-descr", "labs": "labs", "impression": "impression-plan-descr", "plan": "plan-descr",
    "meds": "meds-descr", "allergies": "Allergies", "goggles": "goggle-treatment-x", "studies": "x-study1-result",
    "exam": "", "vitals": "", "ros": "",
}
# Checkbox prefixes per section, in the order phrases are tried.
SECTION_PREFIX = {
    "dizziness": ["assoc-", "worse-", "course-", "occur-", "duration-", "better-", "adl-", "dizziness"],
    "hearing": ["hlassoc-", "hlcourse-", "hlbetter-", "hlworse-", "hldur-", "hl-", "hearingloss"],
    "tinnitus": ["t-assoc-", "t-course-", "t-better-", "t-worse-", "t-dur-", "t-adl-", "t-", "tinnitus"],
    "gait": ["ga-assoc-", "ga-course-", "ga-better-", "ga-worse-", "ga-falls-", "gaitataxia"],
    "headache": ["h-assoc-", "h-course-", "h-better-", "h-worse-", "h-dur-", "h-adl-", "h-", "headache"],
    "pmh": ["hist-"], "family": ["family-"], "social": ["social-"], "ros": ["review-"],
    "goggles": ["dixhall-", "supine-", "gaze-", "imp-", "see-epley"], "plan": ["plan-"], "exam": ["debris-"],
}
# Spoken aliases for checkbox keys (label words work too).
ALIASES = {
    "standing": ["standing", "stands up", "standing up"], "walking": ["walking", "walks"],
    "walking-in-dark": ["walking in the dark", "in the dark", "dark"],
    "transitioning": ["transitioning", "getting up", "sit to stand", "transitions"],
    "pitching": ["pitching", "looking up", "looking down", "head up", "head down"],
    "yaw": ["yaw", "turning the head", "turning head", "turning left or right"],
    "supine": ["lying supine", "lying down", "lying in bed", "supine"], "stress": ["stress", "fatigue", "tired"],
    "opto": ["optokinetic", "busy visual", "screens", "grocery store"], "highgain": ["high gain", "quick head movement", "fast head"],
    "parox": ["paroxysmal", "episodic", "episodes", "comes and goes"], "chronic": ["chronic", "constant", "all the time"],
    "stable": ["stable", "unchanged"], "worsening": ["worsening", "getting worse", "has worsened", "progressively worse"],
    "worse": ["worsening", "getting worse", "has worsened"],
    "improving": ["improving", "getting better", "has improved"], "improve": ["improving", "getting better", "has improved"],
    "seconds": ["seconds"], "minutes": ["minutes"], "hours": ["hours"], "days": ["days"],
    "sit": ["sitting down", "sits", "sit down"], "headstill": ["head still", "holding head still", "holds head still"],
    "resolves": ["resolves on its own", "resolves", "goes away on its own"], "meds": ["medication", "medications", "meclizine"],
    "none": ["no clear", "nothing helps", "no relieving"], "mild": ["mild", "mildly"], "mod": ["moderate", "moderately"],
    "severe": ["severe", "severely"], "sev": ["severe", "severely"], "right": ["right", "right ear", "right side"],
    "left": ["left", "left ear", "left side"], "bilat": ["bilateral", "both ears", "both sides", "bilaterally"],
    "vertigo": ["vertigo", "spinning"], "cbd": ["chronic baseline disequilibrium", "baseline disequilibrium", "baseline imbalance"],
    "diseq": ["disequilibrium", "imbalance", "unsteadiness", "off balance"], "headache": ["headache", "headaches"],
    "attacks": ["attacks"], "tinnitus": ["tinnitus", "ringing"], "hearingloss": ["hearing loss"],
    "auralfullness": ["aural fullness", "ear fullness", "fullness"], "aural": ["aural fullness", "ear fullness", "fullness"],
    "hearingaids": ["hearing aids", "hearing aid"], "loudbg": ["loud background", "background noise", "noisy"],
    "notab": ["none of the above"], "hearingfluc": ["fluctuation", "fluctuating"], "fluc": ["fluctuation", "fluctuating"],
    "pulsatile": ["pulsatile", "pulsing", "heartbeat"], "nonpulsatile": ["non-pulsatile", "nonpulsatile", "non pulsatile", "steady"],
    "freq-hi": ["high frequency", "frequent"], "freq-med": ["medium frequency"], "freq-low": ["low frequency", "infrequent"],
    "cane": ["cane"], "walker": ["walker"], "chair": ["wheelchair"], "sitrest": ["sitting or resting", "rest", "resting"],
    "walk": ["walking", "walks"], "corners": ["turning corners", "corners", "quick turns"], "orthostasis": ["orthostasis", "orthostatic"],
    "veerleft": ["veers left", "veering left", "veer to the left"], "veerright": ["veers right", "veering right", "veer to the right"],
    "bradykinesia": ["bradykinesia", "slow movements"], "mental": ["mental status", "confusion", "cognitive decline"],
    "weakness": ["leg weakness", "weakness"], "sensory": ["sensory loss", "numbness", "numb"], "bowel": ["bowel"], "bladder": ["bladder", "urinary"],
    "falls-yes": ["has fallen", "have fallen", "fallen", "fell", "falls", "fall"], "falls-no": ["no falls", "denies falls", "hasn't fallen", "without falls"],
    "global": ["global", "whole head", "all over"], "frontal": ["frontal", "forehead", "front"], "occipital": ["occipital", "back of the head"],
    "diet": ["diet", "dietary", "foods"], "noise": ["noise", "loud"], "lights": ["lights", "light", "bright"],
    "norelief": ["no relieving"], "noexac": ["no exacerbating"], "sharp": ["sharp", "stabbing"], "dull": ["dull", "aching"],
    "throb": ["throbbing", "pounding", "pulsating"], "const": ["constant"], "pressure": ["pressure", "squeezing"],
    "scotomas": ["scotomas", "scotoma", "visual spots"], "aura": ["aura"], "hearing": ["hearing loss"],
    "nausea": ["nausea", "nauseous"], "emesis": ["vomiting", "emesis", "throwing up"], "photo": ["photophobia", "light sensitivity"],
    "phono": ["phonophobia", "sound sensitivity"], "water": ["hydration", "water"],
    "hypt": ["hypertension", "high blood pressure"], "migraine": ["migraine", "migraines"], "dyslip": ["dyslipidemia", "high cholesterol", "hyperlipidemia"],
    "cad": ["coronary artery disease", "cad", "heart disease"], "diab": ["diabetes", "diabetic"], "arrh": ["arrhythmia", "afib", "atrial fibrillation"],
    "stroke": ["stroke", "cva"], "cervical": ["cervical disease", "neck disease", "cervical"], "trauma": ["head trauma", "concussion", "loss of consciousness"],
    "ear": ["ear infections"], "ototox": ["ototoxic"], "tobacco": ["smokes", "smoker", "tobacco", "cigarettes"],
    "etoh": ["drinks", "alcohol", "etoh"], "drugs": ["drugs", "marijuana", "cannabis"], "denies": ["denies tobacco", "no tobacco alcohol or drugs"],
    "kerat": ["keratinous debris", "keratin"], "cerumen": ["cerumen", "wax", "earwax"],
    "balancelog": ["balance log"], "halog": ["headache log", "ha log"], "fall": ["fall precautions"],
    "continue": ["continue exercises", "home exercises", "vestibular exercises"], "prophy": ["birth control"],
    "adverse": ["adverse effects", "side effects", "discussed indications"], "horizontal": ["head above horizontal", "earth horizontal"],
    "sleep": ["sleep at 45", "45 degrees", "head elevated"], "return": ["return to clinic", "rtc"], "driving": ["not to drive", "no driving", "driving"],
    "torsion": ["torsional", "torsion"], "fatigue": ["fatigable", "fatigues"], "latency": ["latency"], "atyp": ["atypical"],
    "atypical": ["atypical"], "leftbeat": ["left beating", "left beat"], "rightbeat": ["right beating", "right beat"],
    "downbeat": ["downbeating", "down beat", "downbeat"], "upbeat": ["upbeating", "upbeat", "up beat"], "geo": ["geotropic"],
    "ageo": ["ageotropic"], "down": ["downbeating", "down beat"], "up": ["upbeating", "up beat"],
    "leftevoked": ["left evoked", "left gaze evoked"], "rightevoked": ["right evoked", "right gaze evoked"],
    "rightpost": ["right posterior canal"], "leftpost": ["left posterior canal"], "rightcupu": ["right cupulolithiasis"],
    "leftcupu": ["left cupulolithiasis"], "rightnyst": ["right sustained"], "leftnyst": ["left sustained"],
    "leftlateral": ["left lateral canal", "left horizontal canal"], "rightlateral": ["right lateral canal", "right horizontal canal"],
    "leftanterior": ["left anterior canal"], "rightanterior": ["right anterior canal"], "normal": ["normal"],
}


@dataclass
class Interpretation:
    set: dict = field(default_factory=dict)        # field -> value
    append: dict = field(default_factory=dict)     # text field -> text to add
    imaging: list[str] = field(default_factory=list)  # ordered imaging; the screen uses the next free PDF boxes
    studies: list[dict] = field(default_factory=list) # reviewed reports; the screen uses the next free study rows
    section: str = ""
    notes: list[str] = field(default_factory=list)  # what was understood, for the screen
    build: bool = False                             # "build the note": the screen writes the note out next

    def as_dict(self) -> dict:
        return {"set": self.set, "append": self.append, "imaging": self.imaging, "studies": self.studies,
                "section": self.section, "notes": self.notes, "build": self.build}


def _label_words(name: str) -> list[str]:
    spec = FIELDS[name]
    key = name
    for prefix in ("dixhall-right-", "dixhall-left-", "supine-center-", "supine-right-", "supine-left-", "gaze-right-",
                   "gaze-left-", "family-mother-", "family-father-", "hlcourse-", "hlbetter-", "hlworse-", "hlassoc-",
                   "hldur-", "t-course-", "t-better-", "t-worse-", "t-assoc-", "t-dur-", "t-adl-", "ga-assoc-",
                   "ga-better-", "ga-worse-", "ga-course-", "ga-falls-", "h-assoc-", "h-better-", "h-worse-",
                   "h-course-", "h-dur-", "h-adl-", "assoc-", "worse-", "course-", "occur-", "duration-", "better-",
                   "adl-", "hist-", "social-", "review-", "plan-", "debris-", "imp-", "hl-", "t-", "h-", "ga-"):
        if name.startswith(prefix):
            key = name[len(prefix):]
            break
    if name.startswith("ga-falls-"):
        key = name[len("ga-"):]                  # "falls-yes": its own words, not "yes"
    words = list(ALIASES.get(key, []))
    label = spec.get("label", "")
    if label and len(label) < 40 and label.lower() not in ("yes", "no", "other", "none"):
        words.append(label.lower())
    return words


def _candidates(section: str) -> list[str]:
    prefixes = SECTION_PREFIX.get(section, [])
    out = []
    for name, spec in FIELDS.items():
        if spec["type"] != "checkbox":
            continue
        if any(name.startswith(p) for p in prefixes) or (section == "goggles" and name == "see-epley"):
            out.append(name)
    return out


def _clear_exclusive(result: Interpretation, name: str) -> None:
    group = FIELDS[name].get("exclusive")
    if group is None:
        return
    for other, spec in FIELDS.items():
        if other != name and spec.get("exclusive") == group:
            result.set[other] = False


_KEYS = "|".join(re.escape(k) for k in sorted(SECTION_WORDS, key=len, reverse=True))
# Where a new part of the visit starts in the middle of a dictation. Plain mentions ("worse at the
# next exam", "dizziness") don't count: it takes a cue, a colon, or "impression one".
_CUE_RE = re.compile(rf"\b(?:(?:next|new)\s*,?\s*section|go to|switch to|moving on to|section)\s*,?\s*(?:the\s+)?({_KEYS})\b", re.I)
_COLON_RE = re.compile(rf"\b({_KEYS})\s*(?::|,?\s*colon\b)", re.I)
_IMPRESSION_START_RE = re.compile(r"\b(?:impressions?|assessment)\s+(?:number\s+)?(?:one|1)\b", re.I)
BUILD_RE = re.compile(r"\b(?:build|write(?:\s+up)?|compose|put together)\s+(?:the\s+|my\s+|this\s+)?note\b[.!]?", re.I)


def _parts(text: str) -> list[str]:
    """A dictation split where the provider moved to another part of the note. Each part after the
    first starts with its section word, so it switches section the ordinary way."""
    cuts = [(m.start(), m.start(1)) for rx in (_CUE_RE, _COLON_RE) for m in rx.finditer(text)]
    cuts += [(m.start(), m.start()) for m in _IMPRESSION_START_RE.finditer(text)]
    parts, pos = [], 0
    for start, key in sorted(cuts):
        if start < pos:
            continue
        parts.append(text[pos:start])
        pos = key
    parts.append(text[pos:])
    return [re.sub(rf"^(\s*(?:{_KEYS}))\s*,?\s*colon\b", r"\1:", p, flags=re.I) for p in parts if p.strip(" ,.;")]


def interpret(text: str, section: str = "hpi", focus_field: str = "") -> Interpretation:
    """Turn a dictation into field values. `section` is the part of the note on screen; `focus_field`
    the text box the provider was in (free text goes there)."""
    text = text or ""
    build = bool(BUILD_RE.search(text))
    text = BUILD_RE.sub(" ", text)
    parts = _parts(text)
    if len(parts) <= 1:
        res = _interpret_one(text, section, focus_field)
    else:
        res = Interpretation(section=section)
        for i, part in enumerate(parts):
            one = _interpret_one(part, res.section or section, focus_field if i == 0 else "")
            res.set.update(one.set)
            for k, val in one.append.items():
                res.append[k] = res.append[k] + "\n" + val if k in res.append else val
            res.imaging += one.imaging
            res.studies += one.studies
            res.notes += one.notes
            res.section = one.section or res.section
    if build:
        res.build = True
        res.notes.append("Build the note")
    return res


# The practice's exam wording, as it is heard: (pattern, findings field, wording to write, abnormal?).
_FRAGMENTS = {"findings5", "findings23", "findings25", "findings26"}   # patient-specific or one line of a longer sentence


def _spoken(s: str) -> str:
    s = s.lower().replace("&", " and ").replace("(-)", " negative ").replace("(+)", " positive ")
    s = re.sub(r"\bhertz\b", "hz", s)
    s = re.sub(r"(\d)\s+hz\b", r"\1hz", s)
    s = re.sub(r"\bout of\b", "/", s)
    s = re.sub(r"\btimes\b", "x", s)
    return re.sub(r"\b(" + "|".join(NUMBER_WORDS) + r")\b", lambda m: str(NUMBER_WORDS[m.group(1)]), s)


def _exam_wording() -> list[tuple]:
    rows = {r["findings"]: r for r in SCHEMA["exam_rows"] if r["findings"]}
    out = []
    for fld, items in phrases.EXAM_NORMALS.items():
        if fld in _FRAGMENTS or fld not in rows:
            continue
        for text in items:
            if "{" in text or "Cursory" in text:
                continue
            out.append((text, fld, text, text in phrases.EXAM_ABNORMAL.get(fld, [])))
    for heard, text in phrases.EXAM_SPOKEN.items():
        fld = next(f for f, items in phrases.EXAM_NORMALS.items() if text in items)
        out.append((heard, fld, text, text in phrases.EXAM_ABNORMAL.get(fld, [])))
    compiled = []
    for heard, fld, text, abnormal in out:
        words = re.findall(r"[a-z0-9]+", _spoken(heard))
        rx = re.compile(r"(?<![a-z0-9])" + r"[^a-z0-9]+".join(map(re.escape, words)) + r"(?![a-z0-9])")
        compiled.append((len(words), rx, fld, text, abnormal))
    return [c[1:] for c in sorted(compiled, key=lambda c: -c[0])]


EXAM_WORDING = _exam_wording()
_OTHERWISE_RE = re.compile(r"\b(?:otherwise|else)\s+(?:normal|unremarkable|wnl|within normal limits)\b|"
                           r"\b(?:rest|remainder) of (?:the )?exam (?:is |was )?(?:normal|unremarkable|wnl|within normal limits)\b")


# The exam rows words not in the practice's wording point to, for "otherwise normal" / "normal except ...": what is
# said besides "normal" is about these rows, so they are not signed off as normal.
ROW_WORDS = [
    ("normal40", r"romberg|gait|sway\w*|falls? to|wide[- ]based|tandem|ataxic|shuffl\w*|veer\w*"),
    ("normal24", r"nystagmus|gaze|saccad\w*|pursuits?|extraocular|eoms?|skew|diplopia"),
    ("normal27", r"head impulse|head thrust|hit\b|weber|rinne|tuning fork|finger (?:friction )?rub|hearing"),
    ("normal36", r"reflex\w*|dtrs?|hyporeflex\w*|hyperreflex\w*|areflex\w*"),
    ("normal37", r"babinski|toes?|upgoing|plantar"),
    ("normal33", r"vibrat\w*|propriocept\w*|position sense|joint position|light touch"),
    ("normal34", r"pin ?prick|monofilament|temperature sens\w*"),
    ("normal31", r"strength|weak\w*|drift|paresis|4/5|3/5"),
    ("normal32", r"tone|rigid\w*|spastic\w*|cogwheel\w*"),
    ("normal38", r"dysmetri\w*|finger to nose|ftn|heel to shin|hts|intention tremor"),
    ("normal39", r"rapid alternating|dysdiadochokines\w*|finger tap\w*"),
    ("normal3", r"papilledema|fundi|fundus"), ("normal4", r"visual fields?"), ("normal5", r"pupils?|perrla|anisocoria"),
    ("normal7", r"tympanic|\btms?\b|ear canals?|cerumen|effusion"), ("normal9", r"murmurs?|bruits?"),
    ("normal26", r"facial (?:droop|weakness|asymmetry)"),
]
ROW_RE = [(n, re.compile(rf"\b(?:{w})", re.I)) for n, w in ROW_WORDS]


def _exam_exceptions(text: str, res: Interpretation, done: set[str]) -> set[str]:
    """What is said besides "otherwise normal" ("positive head impulse to the right, Romberg positive"): the rows it is
    about are marked abnormal, with the words on the row's findings line (the first such row, when one phrase is about
    several). Returns the rows marked."""
    rows = {r["normal"]: r for r in SCHEMA["exam_rows"] if r["normal"]}
    marked: set[str] = set()
    text = _OTHERWISE_RE.sub(" ", text)
    for piece in re.split(r"[,;.]|\band\b", text):
        piece = re.sub(r"\b(?:exam|the exam|physical exam|otherwise|is|was)\b", " ", piece).strip(" ,.;")
        hit = [n for n, rx in ROW_RE if rx.search(piece) and rows[n]["findings"] not in done]
        if not hit or len(piece.split()) < 2:
            continue
        words = wording.clinical(piece).rstrip(".")
        for n in hit:
            row = rows[n]
            res.set[row["abnormal"]] = "X"
            res.set[n] = False
            marked.add(row["findings"] or n)
        first = next((rows[n] for n in hit if rows[n]["findings"]), None)
        if first is not None:
            have = res.set.get(first["findings"], "")
            res.set[first["findings"]] = (have + ". " + words) if have else words
        res.notes.append(f"Exam: abnormal ({', '.join(rows[n]['label'] for n in hit)}): {words}")
    return marked


def _exam_findings(t: str, res: Interpretation) -> tuple[str, int]:
    """Exam rows from the practice's own wording: a normal phrase marks its row normal, an abnormal one
    marks it abnormal (X), and the wording goes on the row's findings line. With "otherwise normal", the rows the
    rest of the words are about aren't signed off as normal."""
    t = _spoken(t)
    found: dict[str, list[tuple[int, str, bool]]] = {}
    for rx, fld, text, abnormal in EXAM_WORDING:
        for m in rx.finditer(t):
            found.setdefault(fld, []).append((m.start(), text, abnormal))
        t = rx.sub(" ", t)
    rows = {r["findings"]: r for r in SCHEMA["exam_rows"] if r["findings"]}
    abnormal_rows = set()
    for fld, hits in found.items():
        row = rows[fld]
        hits.sort()
        if any(a for _, _, a in hits):
            res.set[row["abnormal"]] = "X"
            if row["normal"]:
                res.set[row["normal"]] = False
            abnormal_rows.add(fld)
        elif row["normal"]:
            res.set[row["normal"]] = True
            res.set[row["abnormal"]] = ""
        res.set[fld] = ". ".join(dict.fromkeys(text for _, text, _ in hits))
        res.notes.append(f"Exam: {row['label']}: {res.set[fld]}")
    if _OTHERWISE_RE.search(t):
        marked = _exam_exceptions(t, res, set(found))
        for row in SCHEMA["exam_rows"]:
            if row["normal"] and row["findings"] not in abnormal_rows and row["findings"] not in found \
                    and (row["findings"] or row["normal"]) not in marked:
                res.set[row["normal"]] = True
                res.set[row["abnormal"]] = ""
        res.notes.append("Exam: other rows normal")
        t = _OTHERWISE_RE.sub(" ", t)
        if marked:
            t = " "
    return t, len(found)


# "64-year-old right-handed female": the age and sex the HPI opening needs, only from how the dictation starts ("this is
# a 68 year old ...") — never someone else's ("rear ended by a 19 year old male driver").
DEMOGRAPHIC_RE = re.compile(r"^\s*(?:(?:um+|uh+|so|okay|ok|and|alright)\b[\s,]*)*(?:(?:this is|the patient is|patient is|pt is|here is|she is|he is|she's|"
                            r"he's|we have|i have|i'm seeing|seeing)\s+)?(?:an?\s+)?(\d{1,3})[- ]?(?:year|yr)s?[- ]?old,?\s+(?:(right|left)[- ]?handed,?\s+)?"
                            r"(female|male|woman|man|gentleman|lady)\b(?!\s+(?:driver|son|daughter|husband|wife|friend|neighbor|brother|sister|"
                            r"coworker|child|grandson|granddaughter)\b)")
# Spoken punctuation: "period", "comma", "new paragraph" (not "a period of", "last menstrual period").
SPOKEN_STOP = re.compile(r"(?<!\bmenstrual)(?<!\ba)(?<!\bthe)(?<!\bthat)(?<!\bthis)(?<!\bgrace)(?<!\btrial)(?<!\bwaiting)(?<!\brecovery)"
                         r"(?<!\btime)(?<!\bsame)\s+(?:period|full stop)\b(?!\s+of\b)", re.I)
SPOKEN_COMMA = re.compile(r"\s+comma\b", re.I)
SPOKEN_PARAGRAPH = re.compile(r"\s*\b(?:new paragraph|new line|next line)\b[\s,.]*", re.I)
# A denial, as said: "no", "not", "never", "doesn't", "denies", "without", "neg". "No longer" is change over time, "can't
# hear" is hearing loss. "Stop / avoid / hold" a medication isn't taking it.
NEG_RE = re.compile(r"\b(?:denies|denied|deny|without|w/o|negative for|neg|no(?!\s+(?:longer|clear|other|current)\b)|not|never|none|nope)\b|"
                    r"\b(?:does|do|did|has|have|had|is|was|are|were|wo|would|could|should)n'?t\b|"
                    r"\b(?:stop|stopped|avoid|avoiding|discontinue|discontinued|hold|holding|taper|tapering|wean|weaning|d/c)\b(?=\s+\w)", re.I)
# Where what a denial covers ends: "no nausea or vomiting | worse with standing", "denies weakness | but has numbness".
SCOPE_END = re.compile(r"[,;.!?]|\b(?:but|however|although|though|except|whereas|worse|better|lasts?|lasting|comes and goes|uses?|using|takes?|"
                       r"taking|wears?|wearing|has|had|have|having|she|he|pt|now|did|does|is|was|just|only|more|mostly|rather|instead|"
                       r"starts?|started|starting|begins?|began|continue|try|add|increase|it'?s|since)\b", re.I)
# The words right after a denial that are part of what it denies: "never had", "doesn't use a", "not worse with", "never when".
SCOPE_LEAD = re.compile(r"^(?:\s*(?:had|has|have|been|having|got|gets|get|any|a|an|the|true|real|really|much|felt|feel|feels|feeling|"
                        r"experienced|experience|noticed|notice|reported|report|complained of|with|uses?|used|using|takes?|took|taking|"
                        r"wears?|wore|wearing|worse|better|when|whenever|while|on))+\b", re.I)
# Where one thought ends in a dictation said without punctuation.
CLAUSE_SPLIT = re.compile(r"(?<=[.;!?,])\s+|\s+(?=(?:but|however|although|though|except|whereas|not sure|unsure|not certain|maybe|possibly|"
                          r"probably|perhaps)\b)|"
                          r"\s+(?=(?:she|he|pt|patient|it'?s|it is)\s+(?:\w+\s+)?(?:has|had|have|is|was|did|does|reports?|says?|said|feels?|gets?|"
                          r"got|uses?|takes?|wears?|denies|endorses|states?|walks?)\b)|\s+(?=(?:no|denies|denied|never)\b)", re.I)
WORSE_CUE = re.compile(r"\b(?:worse|worsens?|worsened|aggravated|exacerbated|triggered|brought on|provoked|precipitated|bothered|when|whenever|"
                       r"upon|every time)\b", re.I)
OCCUR_CUE = re.compile(r"\b(?:occurs?|happens?|only|while|comes on)\b", re.I)
BETTER_CUE = re.compile(r"\b(?:better|improves?|improved|relieved|relief|helps?|helped|eases?|eased|alleviated)\b", re.I)
# Boxes that are a thing the patient uses, not a relieving factor said ("uses a walker", "hearing aids").
DEVICE = ("cane", "walker", "chair", "hearingaids")
SIDE_KEYS = {"right": r"right", "left": r"left", "bilat": r"bilateral|bilaterally|both ears|both sides|both"}
STABLE_SAID = re.compile(r"\b(?:no|not|without|denies|never|hasn'?t)\s+(?:any\s+|significant\s+|further\s+|much\s+)?(?:new|change|changes|"
                         r"worsening|improvement|increase|decrease|recurrence|progression|difference)\b", re.I)
CONNECTORS = set("""and with the a an of to or in on at for is are was were be been she he her his it its also associated assoc this that
    there they them pt patient reports reported says said states stated has have had having any some very really just exam normal except
    otherwise unremarkable wnl vitals""".split())
FALL_WORDS = re.compile(r"\b(?:falls?|fell|fallen|falling)\b", re.I)
SYMPTOM_TITLE = {"dizziness": "Dizziness", "hearing": "Hearing loss", "tinnitus": "Tinnitus", "gait": "Gait", "headache": "Headaches"}


def _spoken_punctuation(text: str) -> str:
    """Said punctuation as punctuation: "... dizziness period no hearing loss comma ..." -> "... dizziness. no hearing loss, ..."."""
    text = SPOKEN_PARAGRAPH.sub("\n", text)
    text = SPOKEN_STOP.sub(".", text)
    return SPOKEN_COMMA.sub(",", text)


def _segmented(said: str) -> str:
    """A long dictation said without punctuation, cut into sentences where a new thought plainly starts ("... lasts less
    than a minute | denies hearing loss and tinnitus | has not had any falls | takes lisinopril ..."), so each denial
    and each detail is read, and kept or dropped, as its own sentence. Only the punctuation changes."""
    out = []
    for line in said.split("\n"):
        if re.search(r"[.,;!?]", line) or len(line.split()) < 12:
            out.append(line)
            continue
        parts = re.split(r"\s+(?=(?:no|denies|denied|never|has not|hasn't|have not|takes|uses|lasts|started|she|he|pt|patient)\b)", line, flags=re.I)
        merged: list[str] = []
        for part in parts:
            prev = merged[-1] if merged else ""
            glue = r"a|an|the|of|to|in|on|for|with|and|or|but|when|while|if|that|which|who|is|was|than|from|at|by|since|after|before|until|" \
                   r"because|so|very|more|less|not|no|any|her|his|says?|said|reports?|states?|thinks?|feels?|notes?|mentions?|has|have|had|" \
                   r"been|be|did|does|do|it|this"
            if re.match(r"(?:takes|uses|lasts|started)\b", part, re.I):
                glue += r"|she|he|pt|patient|spinning|dizziness|vertigo|ringing|tinnitus|headaches?|symptoms?|sxs|episodes?|attacks?"
            if prev and not re.search(rf"\b(?:{glue})\s*$", prev, re.I) and len(prev.split()) >= 2:
                merged.append(part)
            elif merged:
                merged[-1] = prev + " " + part
            else:
                merged.append(part)
        out.append(". ".join(p.strip() for p in merged if p.strip()))
    return "\n".join(out)


def _interpret_one(text: str, section: str = "hpi", focus_field: str = "") -> Interpretation:
    res = Interpretation(section=section)
    text = _spoken_punctuation(text or "")
    t = " " + re.sub(r"\s+", " ", wording.spoken_numbers(text.strip().lower(), everywhere=True)).strip(" .") + " "
    orig = "\n".join(" ".join(ln.split()) for ln in text.strip().split("\n") if ln.strip())   # what was said, for the text box
    if not t.strip():
        return res
    t0 = t                                  # before the section word comes off

    # Section switch ("dizziness:", "now the exam", "impression and plan"): a cue, a colon or a comma after the word.
    # "Headaches have been worse ..." is a sentence about headaches, not a new section.
    for words, sec in sorted(SECTION_WORDS.items(), key=lambda kv: -len(kv[0])):
        m = re.match(rf"^\s*(?P<cue>now |next |go to |switch to |section )?{re.escape(words)}\b(?P<mark>\s*(?::|,|-|\bcolon\b))?", t)
        if not m:
            continue
        rest = t[m.end():]
        if not (m["cue"] or m["mark"] or not rest.strip()):
            break
        section = res.section = sec
        t = " " + re.sub(r"^\s*(?:is|was|has been|have been|are|feels|feel)\s+", "", rest)
        orig = re.sub(rf"^\s*(?:now |next |go to |switch to |section )?{re.escape(words)}\b\s*(?::|,?\s*colon\b|,|-)?\s*", "", orig, flags=re.I)
        orig = re.sub(r"^(?:is|was|has been|have been|are|feels|feel)\s+", "", orig, flags=re.I)
        res.notes.append(f"Section: {sec}")
        break

    m = DEMOGRAPHIC_RE.search(t0)
    if m:
        res.set["HPI-age"] = m.group(1)
        female = m.group(3) in ("female", "woman", "lady")
        res.set["hpi-female"], res.set["hpi-male"] = female, not female
        res.notes.append(f"{m.group(1)} y/o {'female' if female else 'male'}" + (f", {m.group(2)}-handed" if m.group(2) else ""))
    # Vitals, only where vitals are being said (the vitals or exam section, or after "vitals"): "lost weight 15 pounds"
    # in the history isn't today's weight.
    vit = re.search(r"\bvitals?\b", t)
    if section in ("vitals", "exam") or vit:
        v_from = vit.end() if vit and section not in ("vitals", "exam") else 0
        vt = t[v_from:]
        m = re.search(r"\b(?:blood pressure|bp)\s*(?:is|of|was)?\s*(\d{2,3})\s*(?:over|/)\s*(\d{2,3})", vt)
        if m:
            res.set["const-bp"] = f"{m.group(1)}/{m.group(2)}"
            t = t.replace(m.group(0), " ")
        for pat, fld in ((r"\b(?:heart rate|pulse|hr)\s*(?:is|of|was)?\s*(\d{2,3})", "const-hr"),
                         (r"\b(?:respiratory rate|respirations|rr)\s*(?:is|of|was)?\s*(\d{1,2})", "const-rr"),
                         (r"(?<!lost )(?<!gained )(?<!down )(?<!up )\bweight\s*(?:is|of|was)?\s*(\d{2,3})", "WEIGHT"),
                         (r"\bbmi\s*(?:is|of|was)?\s*(\d{2}(?:\.\d)?)", "BMI"),
                         (r"\b(?:temperature|temp)\s*(?:is|of|was)?\s*(\d{2,3}(?:\.\d)?)", "TEMP"),
                         (r"\bpain\s*(?:score|scale|level)\s*(?:is|of|was)?\s*(\d{1,2})(?:\s*(?:out of|/)\s*10)?|\bpain\s*(?:is|of|was)?\s*(\d{1,2})\s*(?:out of|/)\s*10", "const-pain")):
            m = re.search(pat, t[v_from:] if v_from else t)
            if m:
                res.set[fld] = next(g for g in m.groups() if g)
                t = t.replace(m.group(0), " ")
        m = re.search(r"\bheight\s*(?:is|of)?\s*(\d)\s*(?:feet|foot|')\s*(\d{1,2})?", t)
        if m:
            res.set["HEIGHT-ft"], res.set["HEIGHT-in"] = m.group(1), m.group(2) or "0"
            t = t.replace(m.group(0), " ")

    # Reflexes: "reflexes two plus throughout" / "DTRs 1 throughout".
    m = re.search(r"\b(?:reflexes|dtrs?)\s*(?:are|is)?\s*(\d)\s*(plus|minus|\+|-)?\s*(?:throughout|bilaterally|symmetric|all around)", t)
    if m:
        val = m.group(1) + ({"plus": "+", "minus": "-", "+": "+", "-": "-"}.get(m.group(2) or "", ""))
        for side in ("r", "l"):
            for k in ("bi", "tri", "br", "pat", "ach"):
                res.set[f"dtr-{side}-{k}"] = val
        res.notes.append(f"DTRs {val} throughout")
        t = t.replace(m.group(0), " ")

    # Exam: "normal exam" / "exam normal" sets every normal box; "normal except ..." marks the rows the exception is about.
    exam_hits = 0
    m = re.search(r"\b(?:normal exam|exam (?:is |was )?normal|all normal|unremarkable exam|exam (?:is |was )?unremarkable)\b"
                  r"(?:\s*,?\s*(?:except(?: for)?|other than|apart from|but)\s+(?P<x>.+))?", t0)
    if m:
        done: set[str] = set()
        if m["x"]:
            done = _exam_exceptions(m["x"], res, set())
            t = t.replace(m["x"].strip(" ."), " ")
        for row in SCHEMA["exam_rows"]:
            if row["normal"] and (row["findings"] or row["normal"]) not in done:
                res.set[row["normal"]] = True
                res.set[row["abnormal"]] = ""
        res.notes.append("Exam: all rows normal" + (" but the ones named" if done else ""))
        exam_hits += 1 + len(done)
    if section == "exam":
        t, hits = _exam_findings(t, res)
        exam_hits += hits

    # Time and plan on page 4.
    m = re.search(r"\btotal (?:encounter )?time\s*(?:of|was|is)?\s*(\d{1,3})\s*minutes?", t)
    if m:
        res.set["appt-time"] = m.group(1)
        t = t.replace(m.group(0), " ")
    if re.search(r"\b(oculography|epley chair|goggles? (?:testing|done|performed))\b", t) and section in ("plan", "impression", "time", "goggles"):
        res.set["time-proc1"] = True
        res.set["addnl-time"] = "15 minutes of separate time IR/oculography and/or Epley Chair testing"
        res.notes.append("Separate time: IR oculography")
    if re.search(r"\blongitudinal\b", t):
        slot = "addnl-time-2" if res.set.get("time-proc1") else "addnl-time"
        res.set[slot] = "Longitudinal services were provided to patient in clinic today"
        res.set["time-proc1" if slot == "addnl-time" else "time-proc2"] = True
        res.notes.append("G2211 longitudinal statement")
        t = re.sub(r"\blongitudinal( services?| care)?( (were|was) provided( to (the )?patient)?( in clinic)?( today)?)?\b", " ", t)
    # The next visit here, not someone else's ("she saw cardiology who will follow up in two weeks").
    for m in re.finditer(r"\b(?P<k>return|follow up|follow-up|rtc|see (?:him|her|them)? ?back|next appointment)\s*(?:in|to clinic in)?\s*"
                         r"(\d{1,2}(?:\s*(?:to|-)\s*\d{1,2})?)\s*(days?|weeks?|months?)", t):
        other = re.search(r"\b(?:cardiology|neurology|pcp|ent|audiology|primary(?: care)?|dr\.?\s+\w+|doctor|they|he|she|who|specialist|"
                          r"surgeon|therapist|pt|ophthalmology|optometry)\s+(?:will\s+|to\s+|would\s+|wants to\s+)?$", t[: m.start()])
        if other or (section not in ("plan", "impression", "time") and m["k"] in ("follow up", "follow-up")):
            continue
        n = re.sub(r"\s*(to|-)\s*", "-", m.group(2))
        res.set["next-appt"] = n
        unit = m.group(3).rstrip("s")
        for u in ("days", "weeks", "months"):
            res.set[f"next-appt-{u}"] = u.startswith(unit)
        res.notes.append(f"Next appointment: {n} {unit}s")
        t = t.replace(m.group(0), " ")
        break

    # Imaging ordered today and reports reviewed: the order boxes and the study rows, not a checkbox phrase. The words
    # stay in the narrative (the plan says what was ordered) except under Studies, where the rows are the narrative.
    t_said = t
    t, orig = _studies_and_orders(t, orig, res, section)

    # Numbered impressions: "impression one dizziness and lack of coordination monitor clinically two ...". A number
    # starts the next item only when it is the next number and not a count ("two to three attacks", "ten milligrams").
    if section == "impression" or re.search(r"\b(?:number|impression) 1\b", t):
        lines = _numbered(t_said, res)
        if lines:
            res.append["impression-plan-descr"] = "\n".join(lines)
            res.notes.append(f"{len(lines)} numbered impression(s)")
            return res

    # Checkbox phrases in the current section, thought by thought. A denial ("no", "not", "never", "doesn't",
    # "denies", "without") covers the words after it up to the next thought, never the whole sentence; it ticks a
    # "-denies" twin (or a symptom's "Patient denies"), never the box itself. Hedged, past and double-negative thoughts
    # tick nothing.
    t = re.sub(r"\bwithstanding\b", "with standing", t)
    cands = _candidates(section)
    if section == "impression":
        cands += [n for n in _candidates("goggles") if n.startswith("imp-")]
    affirmed: list[str] = []                 # labels ticked present
    untold: list[str] = []                   # denied words no box could take
    no_head: set[str] = set()                # symptoms said both ways or only in part: their headline isn't ticked
    denied: list[str] = []                   # labels ticked denied
    consumed: list[str] = []                 # words a tick used
    affirmed_text: list[str] = []
    head = {"dizziness": "dizziness", "hearing": "hearingloss", "tinnitus": "tinnitus", "gait": "gaitataxia", "headache": "headache"}.get(section)
    headline = {"dizziness": "dizziness", "dizzy": "dizziness", "lightheaded": "dizziness", "hearing loss": "hearingloss",
                "hearing": "hearingloss", "tinnitus": "tinnitus", "ringing": "tinnitus", "gait": "gaitataxia", "ataxia": "gaitataxia",
                "imbalance": "gaitataxia", "headache": "headache", "headaches": "headache"}

    def tick(name: str) -> bool:
        group = FIELDS[name].get("exclusive")
        if group is not None and any(res.set.get(o) is True for o, spec in FIELDS.items() if o != name and spec.get("exclusive") == group):
            return False                     # the first side said stays ("hearing loss on the right, not the left")
        res.set[name] = True
        _clear_exclusive(res, name)
        affirmed.append(FIELDS[name]["label"])
        return True

    def side_ok(clause: str, m: re.Match, key: str) -> bool:
        before, after = clause[: m.start()], clause[m.end():]
        if section in ("hearing", "tinnitus", "headache") and not before.strip(" ,"):
            return True                      # "Hearing loss, left, ...": the side is the first thing said
        if re.search(r"\b(?:not|no|than|other)\s+(?:the\s+)?$", before) or wording.NOT_SIDE_RE.search(clause[max(0, m.start() - 4): m.end() + 8]):
            return False
        if re.search(r"\b(?:aids?|cane|walker)\s+(?:\w+\s+){0,2}$", before):
            return False                     # "hearing aids in both ears": where the aids are
        if key == "bilat":
            return True
        sym = story.TOPIC_RE.get({"hearing": "hearing", "tinnitus": "tinnitus", "headache": "headache"}.get(section, "dizziness"))
        near = re.search(r"^(?:\W+\w+){0,3}?\W+(?:ears?|side|sided)\b", after) or re.search(r"\b(?:in|on|of)\s+(?:the|her|his|my)\s+$", before) \
            or (sym is not None and (sym.search(before[-25:]) or sym.search(after[:25])))
        return bool(near)

    def affirm(clause: str, cue: str | None) -> str:
        spans = story.hedged_spans(clause)
        said = clause
        for name in sorted(cands, key=lambda n: -max((len(w) for w in _label_words(n)), default=0)):
            if name.endswith("-denies") or name in no_head:
                continue
            key = name.split("-")[-1]
            for w in _label_words(name):
                if not w or len(w) < 3:
                    continue
                m = re.search(rf"(?<![a-z-])(?<!non-)(?<!non ){re.escape(w)}(?![a-z])", clause)
                if not m or story._in(spans, m.start(), m.end()):
                    continue
                before = clause[: m.start()]
                worse_cue = WORSE_CUE.search(before) if section != "gait" else re.search(r"\b(?:worse|aggravated|exacerbated|triggered|brought on)\b", before)
                if re.search(r"(?:^|-)worse-", "-" + name) and not (cue == "worse" or worse_cue):
                    continue                 # "walks with a walker" isn't "worse with walking"
                if name.startswith("occur-") and not OCCUR_CUE.search(before):
                    continue
                if re.search(r"(?:^|-)better-", "-" + name) and key not in DEVICE and not (cue == "better" or BETTER_CUE.search(before)):
                    continue                 # "worse since she started the new medication" isn't "better with medications"
                if key in ("right", "left") and section not in ("goggles", "impression") and \
                        re.match(rf"\s+(?:\w+\s+)?(?:worse|better|more|greater|louder)\s+than\s+(?:the\s+)?{'left' if key == 'right' else 'right'}\b", clause[m.end():]):
                    bilat = next((n for n in cands if n.endswith("-bilat")), None)
                    if bilat and tick(bilat):
                        consumed.append(w)
                        clause = clause[: m.start()] + " " * len(m.group(0)) + clause[m.end():]
                    break
                if section not in ("goggles", "impression") and key in SIDE_KEYS and not side_ok(said, m, key):
                    continue
                if tick(name):
                    consumed.append(w)
                    clause = clause[: m.start()] + " " * len(m.group(0)) + clause[m.end():]
                break
        affirmed_text.append(clause)
        return clause

    def deny(scope: str, after: str = "") -> str:
        for words, hd in sorted(headline.items(), key=lambda kv: -len(kv[0])):
            rx = rf"\b{re.escape(words)}\b" + (r"(?!\s+(?:aids?|tests?|testing))" if words == "hearing" else "")
            hit = re.search(rx, scope)
            if not hit:
                continue
            consumed.append(words)
            scope = re.sub(rx, " ", scope)
            if re.match(r"\s*(?:(?:in|on)\s+(?:the|her|his|my)\s+(?:right|left)\b|(?:ad|as)\b|(?:right|left)\s+(?:ear|side))", scope[hit.start():]) \
                    or story.EXCEPT.match(after.strip()) or re.match(r"\s*(?:except|other than|apart from|aside from|besides)\b", after):
                no_head.add(hd)              # "no hearing loss in the right ear", "denies tinnitus except ...": not a denial of it
                res.notes.append(f"Check: \u201c{' '.join((words + ' ' + scope + ' ' + after).split())}\u201d")
                continue
            res.set[hd + "-denies"] = True
            res.set[hd] = False
            denied.append(f"Denies {FIELDS[hd]['label'].lower()}")
        if section not in ("vitals", "exam", "ros") and FALL_WORDS.search(scope):     # a vitals pass reads vitals only
            res.set["ga-falls-no"], res.set["ga-falls-yes"] = True, False
            denied.append("Falls: no")
            consumed.append("falls")
            scope = FALL_WORDS.sub(" ", scope)
        for name in cands:
            if not name.endswith("-denies") or name.split("-")[0] in headline.values():
                continue
            base = name[: -len("-denies")]
            for w in _label_words(base) if base in FIELDS else []:
                if w and len(w) >= 3 and re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", scope):
                    res.set[name], res.set[base] = True, False
                    denied.append(FIELDS[name]["label"])
                    consumed.append(w)
                    scope = re.sub(rf"(?<![a-z]){re.escape(w)}(?![a-z])", " ", scope, count=1)
                    break
        for name in cands:                   # a denied box with no "-denies" twin is ticked neither way
            for w in _label_words(name):
                if w and len(w) >= 4 and re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", scope):
                    consumed.append(w)
                    scope = re.sub(rf"(?<![a-z]){re.escape(w)}(?![a-z])", " ", scope)
                    untold.append(w)         # said and denied, but no box says so: the words go in the text box
        return scope

    if section == "goggles":
        _goggles(t, res, affirmed)
    else:
        clauses = [c for c in CLAUSE_SPLIT.split(t) if c and c.strip(" ,.;")]
        cue = None
        for i, clause in enumerate(clauses):
            nxt = clauses[i + 1] if i + 1 < len(clauses) else ""
            if story._clause_doubt(clause) or re.fullmatch(r"\W*(?:i'?m\s+)?(?:not sure|unsure|not certain|uncertain)\W*", nxt or "x"):
                res.notes.append(f"Unsure: “{clause.strip(' ,.;')}”")
                cue = None
                continue
            if story.DOUBLE_NEG.search(clause):
                res.notes.append(f"Check: “{clause.strip(' ,.;')}”")
                continue
            if STABLE_SAID.search(clause):
                continue                     # "no new hearing loss since last visit": a change, not a denial
            clause, past = story._without_past(clause) if section not in ("pmh", "family", "social") else (clause, False)
            if past:
                res.notes.append("Past episode, not current")
                if not clause.strip():
                    continue
            if WORSE_CUE.search(clause):
                cue = "worse"
            elif BETTER_CUE.search(clause):
                cue = "better"
            elif len(clause.split()) > 4 or NEG_RE.search(clause):
                cue = None
            pos = 0
            for neg in NEG_RE.finditer(clause):
                if neg.start() < pos:
                    continue
                affirm(clause[pos: neg.start()], cue)
                rest = clause[neg.end():]
                lead = SCOPE_LEAD.match(rest)
                start = lead.end() if lead else 0
                end = SCOPE_END.search(rest, start)
                stop = end.start() if end else len(rest)
                deny(rest[:stop], rest[stop:] + " " + nxt)
                pos = neg.end() + stop
            affirm(clause[pos:], cue)

    said_text = " ".join(affirmed_text)
    topic = story.TOPIC_RE.get({"hearing": "hearing"}.get(section, section)) if head else None
    real = [a for a in affirmed if a not in ("Use cane", "Use walker", "Wheelchair", "Wear hearing aids", "Yes")]
    if head and head not in no_head and not res.set.get(head + "-denies") and (
            real or (topic is not None and topic.search(said_text)) or re.search(r"\b(yes|present|reports|complains|endorses)\b", said_text)):
        res.set[head] = True
        res.set[head + "-denies"] = False
    res.notes = list(dict.fromkeys(res.notes))
    if affirmed:
        res.notes.append("Checked: " + ", ".join(dict.fromkeys(affirmed)))
    if denied:
        res.notes.append("Checked: " + ", ".join(dict.fromkeys(denied)))

    # Narrative goes into the section's text box (or the focused one): everything that was said, in the clinic's
    # wording. It is left out only when the boxes took every word that carries meaning ("worse with standing, lasts
    # seconds"); a denial whose words no box took, a time of day, a qualifier, all keep the sentence.
    left = t
    for w in sorted(set(consumed), key=len, reverse=True):
        left = re.sub(rf"(?<![a-z]){re.escape(w)}(?![a-z])", " ", left)
    left = NEG_RE.sub(" ", left)
    left = WORSE_CUE.sub(" ", BETTER_CUE.sub(" ", left))
    if head and topic is not None:
        left = topic.sub(" ", left)
    content = [w for w in re.findall(r"[a-z][a-z']+", left) if w not in CONNECTORS and w not in ("lasts", "last", "lasting", "x")]
    said = orig.strip(" ,;:")
    if section == "plan":                    # the time and the next visit are boxes of their own
        said = re.sub(r"\b(?:total (?:encounter )?time\s*(?:of|was|is)?\s*[\w -]{1,20}?\s*minutes?|(?:return|follow up|follow-up|rtc)\s*(?:in|to clinic in)?"
                      r"\s*[\w -]{1,20}?\s*(?:days?|weeks?|months?))[,.;]?\s*", "", said, flags=re.I).strip(" ,;:.")
        content = content if said else []
    head_word = next((w for w, sec in SECTION_WORDS.items() if sec == section and re.match(rf"{re.escape(w)}\b[\s,:]*", said, re.I)), "")
    if head_word and section in ("meds", "allergies", "labs", "plan", "impression"):
        said = re.sub(rf"^{re.escape(head_word)}\b[\s,:]*", "", said, flags=re.I)        # "Medications meclizine ..." is a heading
    if section == "meds":                    # one medication to a line
        from . import medlist
        names = sorted({n for n in medlist.all_names() if len(n) >= 4 and re.search(rf"\b{re.escape(n)}\b", said, re.I)}, key=len, reverse=True)
        for n in names:
            said = re.sub(rf"(?<=\w)[,;]?\s+(?={re.escape(n)}\b)", "\n", said, flags=re.I)
    if said and (content or untold or section == "plan") and not ((affirmed or denied or exam_hits) and not content and not untold and section != "plan"):
        # Exam, vitals and ROS have no text box of their own: what's left there isn't put in the HPI, unless it is about
        # a symptom or a fall ("no falls since last visit" said with the vitals).
        target = focus_field or SECTION_TEXT.get(section, "ChiefComplaint-descr")
        if section == "goggles" and not focus_field and not re.search(r"\b(?:epley|semont|brandt|maneuver|crm|treat\w*|repositioning|gufoni|"
                                                                      r"barbecue|bbq|lempert)\b", said, re.I):
            target = ""                      # findings are the boxes; the treatment box is for the treatment
        if target and target == SECTION_TEXT.get(section) and section in SYMPTOM_TITLE and \
                (len(said.split()) > 6 or NEG_RE.search(said) or re.search(r"[.;]\s+\S", said)):
            # Narrative said in a symptom's section is part of the history: it goes in the HPI, where the builder keeps
            # what the boxes don't already say. Without its subject it would read as about anything; the section's
            # name goes in front ("Dizziness worse w/ standing, never when lying down.").
            target = "ChiefComplaint-descr"
            topic_rx = story.TOPIC_RE[{"hearing": "hearing"}.get(section, section)]
            head_box = {"dizziness": "dizziness", "hearing": "hearingloss", "tinnitus": "tinnitus", "gait": "gaitataxia", "headache": "headache"}[section]
            # The name goes in front only when this dictation said the patient has the symptom: "she has fallen twice"
            # said under Gait is about falls, not gait ataxia, and the name would make the story read it as present.
            if not topic_rx.search(said) and res.set.get(head_box) is True and not FALL_WORDS.search(said) \
                    and not re.search(r"\b(?:cane|walker|rollator|wheelchair|hearing aids?)\b", said, re.I):
                said = SYMPTOM_TITLE[section] + " " + said[:1].lower() + said[1:] if not re.match(r"[A-Z]{2}", said) else SYMPTOM_TITLE[section] + " " + said
        if target:
            res.append[target] = "\n".join(wording.clinical(ln) for ln in _segmented(said).split("\n")) if section == "meds" else wording.clinical(_segmented(said))
        else:
            kept = [p.strip() for p in re.split(r"[,.;]", said) if (story._topics(p) or FALL_WORDS.search(p))
                    and not re.search(r"\b(?:blood pressure|bp|pulse|heart rate|weight|temp\w*)\b", p, re.I)]
            if kept and section in ("vitals", "exam", "ros"):
                res.append["ChiefComplaint-descr"] = wording.clinical(", ".join(kept))
            else:
                res.notes.append(f"Not placed: \"{wording.clinical(said)}\"")
    return res


def _numbered(t: str, res: Interpretation) -> list[str]:
    """The numbered impressions in a dictation: "impression 1 bppv right posterior canal dash epley today 2 ...". Item n+1
    starts only at the number n+1 said as a marker: not a count ("2-3 attacks", "10 mg", "2 weeks"), not inside a range."""
    marks = []
    want = 1
    for m in re.finditer(r"(?:\b(?:number|impression|assessment|#)\s*)?(?<![\d.-])\b(\d{1,2})\b(?![\d.]|\s*-\s*\d)\s*[\)\.:]?\s+(?=[a-z])", t):
        n = int(m.group(1))
        after = t[m.end():]
        if n != want or re.match(r"(?:to|or|hundred|thousand|point|mg|milligrams?|mcg|units?|attacks?|times|x\b|days?|weeks?|months?|years?|"
                                 r"minutes?|hours?|seconds?|percent|episodes?|spells?|falls?|cups?|glasses?|drinks?|per|a\s+(?:day|week|month))\b",
                                 after):
            continue
        marks.append((m.start(), m.end(), n))
        want += 1
    lines = []
    for i, (a, b, n) in enumerate(marks):
        body = t[b: marks[i + 1][0] if i + 1 < len(marks) else len(t)].strip(" ,.")
        if not body:
            continue
        pieces = re.split(r"\s+(?:dash|hyphen|-)\s+|[,.]\s+(?=[a-z])", body, maxsplit=1)
        dx, plan = pieces[0].strip(), (pieces[1].strip() if len(pieces) > 1 else "")
        for name in _candidates("goggles"):
            if name.startswith("imp-") and any(w and len(w) > 6 and w in dx for w in _label_words(name)):
                res.set[name] = True
                _clear_exclusive(res, name)
        lines.append(f"{n}) {_cased(wording.dose_words(dx))}" + (f" - {_cased(wording.dose_words(plan))}" if plan else ""))
    return lines


CASED = {w.lower(): w for w in re.findall(r"[A-Za-z][\w'-]*", VOCABULARY) if re.search(r"[A-Z]", w[1:]) or w[:1].isupper() and w.lower() in (
    "epley", "dix-hallpike", "romberg", "meniere's", "hoffman's", "weber", "rinne", "nurtec")}
CASED.update({"bppv": "BPPV", "vng": "VNG", "vemp": "VEMP", "mri": "MRI", "ct": "CT", "cta": "CTA", "pt": "PT", "vrt": "VRT", "ent": "ENT",
              "semont": "Semont", "brandt-daroff": "Brandt-Daroff", "dix": "Dix", "hallpike": "Hallpike", "menieres": "Meniere's",
              "meniere's": "Meniere's", "snhl": "SNHL", "rtc": "RTC", "prn": "prn", "mg": "mg", "audiogram": "audiogram", "pcp": "PCP",
              "vor": "VOR", "pppd": "PPPD", "ssnhl": "SSNHL", "htn": "HTN"})


def _cased(s: str) -> str:
    """The first letter up, the rest as said, with the acronyms and eponyms the practice writes ("BPPV", "Epley")."""
    s = " ".join(s.split())
    s = re.sub(r"[A-Za-z][\w'-]*", lambda m: CASED.get(m.group(0).lower(), m.group(0)), s)
    return s[:1].upper() + s[1:]


# --------------------------------------------------------------------------- imaging orders and reviewed studies
# A study said with a result ("MRI brain from June was unremarkable", "VNG showed left caloric weakness", "Zeto EEG was
# normal") is a reviewed report: a study row. One said as something to get ("order an MRI brain with and without", "get an
# MRA neck", "let's image the IACs") is today's order: an imaging order box. Someone else's order ("PCP ordered an MRI")
# and a scan the patient had are history, not today's order.
_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_MODALITY = (r"zeto eeg|mri|mra|mr angiogram|cta|ct angiogram|ct|cat scan|x-?rays?|pet(?:[ -]?(?:ct|scan|brain))|fdg pet|dat ?scan|"
             r"carotid (?:duplex|ultrasound|dopplers?|us)|carotids|eeg|vng/vemp|vngs?|vemps?|cdp|posturography|audiogram|audiometry|"
             r"hearing test|emg(?:/ncv| and ncv| ncv| nerve conduction)?|ncv|nerve conduction stud(?:y|ies)|echocardiogram|echo|"
             r"home sleep (?:test|study)|hst|sleep study|imaging")
_PART = (r"brain|head|neck|iacs?|internal auditory canals?|orbits?|pituitary|sella|c[- ]?spine|t[- ]?spine|l[- ]?spine|cervical|thoracic|"
         r"lumbar|spine|temporal bones?|petrous|circle of willis|carotids?|upper|lower|bilateral|extremit(?:y|ies)|bue|ble|arms?|legs?")
_STUDY_WORDS = rf"(?:of|the|and|with|without|w/o|w/wo|wo|contrast|gad|gadolinium|3[- ]?d|reconstruction|protocol|screening|full|{_PART})"
_STUDY_RE = re.compile(rf"\b(?P<study>(?:{_MODALITY})\b(?:\s+{_STUDY_WORDS}\b)*)", re.I)
_STUDY_LEAD_RE = re.compile(rf"\b((?:{_PART})\b(?:\s+{_STUDY_WORDS}\b)*\s+)$", re.I)      # "cervical spine MRI", "lower extremity EMG"
_IMAGE_RE = re.compile(rf"\b(?:image|imaging of|scan)\s+(?:the\s+|his\s+|her\s+)?(?={_PART}\b)", re.I)
_ORDER_BEFORE = re.compile(r"\b(?:order(?:ed|ing)?|obtain(?:ed|ing)?|get(?:ting)?|let'?s (?:get|order|do|obtain)|we(?:'ll| will|'re going to| would like to)|"
                           r"i(?:'ll| will|'d like to| would like to| want to)|going to (?:get|order|obtain)|send (?:him|her|them|pt|patient) for|"
                           r"schedul(?:e|ed|ing)|proceed with|recommend(?:ed|ing)?|request(?:ed|ing)?|plan (?:for|to get|to obtain|on getting)|"
                           r"will need|needs?|arrange(?:d)? for|refer(?:red)? for|set up)\s+"
                           r"(?:an?\s+|the\s+|for\s+|another\s+|repeat\s+|new\s+|updated\s+|routine\s+|outpatient\s+|urgent\s+|stat\s+)*$", re.I)
_ORDER_AFTER = re.compile(r"^\s*(?:is |was |has been |to be |will be |being )?(?:ordered|requested|scheduled|placed|sent|arranged)\b", re.I)
_REVIEW_BEFORE = re.compile(r"\b(?:reviewed|review of|went over|looked at|discussed|results? of|per|read)\s+"
                            r"(?:the\s+|his\s+|her\s+|their\s+|today'?s\s+|recent\s+|outside\s+|prior\s+|previous\s+|last\s+|a\s+)*$", re.I)
_HISTORY_BEFORE = re.compile(r"\b(?:had|has had|got|underwent|went (?:in )?for|done at|completed|no|not|don'?t|doesn'?t|didn'?t|won'?t|without|"
                             r"hold off on|defer(?:red)?|declin(?:ed|es)|refus(?:ed|es)|rather than|instead of)\b(?:\s+\w+){0,2}\s*$", re.I)
_OTHER_ORDERED = re.compile(r"\b(?:pcp|primary(?: care)?|cardiology|cardiologist|neurology|neurologist|ent|neurosurgery|neurosurgeon|ortho\w*|er|ed|"
                            r"emergency(?: room| department)?|hospital|urgent care|dr\.?\s+\w+|doctor|who|outside|another provider)\s+(?:\w+\s+){0,3}$", re.I)
_DATE_AFTER = re.compile(rf"^\s*(?:,\s*)?(?:(?:from|on|dated|done|performed|obtained|back in|in|of)\s+)?"
                         rf"(?P<date>(?:(?:early|mid|late)[- ]?)?{_MONTH}(?:\s+\d{{1,2}}(?:st|nd|rd|th)?)?,?(?:\s+(?:of\s+)?(?:19|20)\d{{2}})?|"
                         rf"\d{{1,2}}/\d{{1,2}}(?:/\d{{2,4}})?|\d{{1,2}}/(?:19|20)\d{{2}}|(?:19|20)\d{{2}}|last (?:week|month|year)|\d{{1,2}} (?:days|weeks|months|years) ago)\b", re.I)
_RESULT_RE = re.compile(r"^\s*(?:,\s*|:\s*|-\s*|which\s+|that\s+|and\s+(?:it\s+)?)?"
                        r"(?:(?:was|is|were|showed|shows|showing|demonstrated|demonstrates|revealed|reveals|read as|came back|returned|resulted|"
                        r"reported(?: as)?|notable for|significant for|remarkable for|consistent with|positive for|negative for|results? (?:were|was|showed|came back|are|is))"
                        r"\b\s*(?:as\s+|to be\s+|to show\s+|with\s+)?|"
                        r"(?=(?:normal|unremarkable|negative|wnl|within normal limits|abnormal|no acute|positive|pending|still pending|"
                        r"not (?:yet )?(?:done|back|available|resulted|completed))\b))(?P<result>[^.;]*)", re.I)
_WHERE_AFTER = re.compile(r"^\s*(?:at|through|via|done at|performed at)\s+(?:the\s+|an?\s+|our\s+)?(?P<where>[a-z]+(?:\s+(?:hospital|clinic|imaging|radiology|center))?)", re.I)
_ANOTHER_ORDER = re.compile(r"studyref\s*,?\s*(?:and|plus|as well as|also|&)?\s*(?:an?|the)?\s*$")   # "..., and a carotid duplex"
_RESULT_CUT = re.compile(rf"[,;]?\s*\b(?:and\s+)?(?:the\s+|his\s+|her\s+)?(?:{_MODALITY})\b|,\s*(?:and\s+)?(?:she|he|pt|patient|we|i|they|will|plan|also|then)\b", re.I)
_NO_IMAGING = re.compile(r"\bno (?:further |additional |new |more )?imaging (?:is |was )?(?:needed|required|indicated|necessary|warranted|at this time)\b")
_NO_ORDERS_IN = {"pmh", "family", "social", "ros", "vitals", "exam", "goggles", "meds", "allergies", "labs"}
_NO_STUDIES_IN = {"meds", "allergies"}


def _study_bits(phrase: str) -> tuple[str, set[str], str]:
    """(modality, regions, contrast) of a study as said, or as an order option is written."""
    p = " " + re.sub(r"\s+", " ", re.sub(r"[^a-z0-9/ ]+", " ", phrase.lower())) + " "
    mod = next((m for m, rx in (("zeto eeg", r"\bzeto\b"), ("cta", r"\b(?:cta|ct angiogram)\b"), ("ct", r"\b(?:ct|cat scan)\b"),
                                ("mra", r"\b(?:mra|mr angiogram)\b"), ("mri", r"\bmri\b"), ("x-ray", r"\bx ?rays?\b"), ("pet", r"\b(?:pet|fdg)\b"),
                                ("datscan", r"\bdat ?scan\b"), ("carotid", r"\bcarotid"), ("eeg", r"\beeg\b"),
                                ("vng/vemp", r"\bvng/vemp\b|\bvngs?\b.*\bvemps?\b"), ("vng", r"\bvngs?\b"), ("vemp", r"\bvemps?\b"),
                                ("cdp", r"\b(?:cdp|posturography)\b"), ("audiogram", r"\b(?:audiogram|audiometry|hearing test)\b"),
                                ("emg", r"\b(?:emg|ncv|nerve conduction)\b"), ("echo", r"\becho"), ("hst", r"\b(?:hst|sleep)\b"),
                                ("imaging", r"\bimaging\b")) if re.search(rx, p)), "")
    regions = {key for key, rx in (("brain", r"\b(?:brain|head|circle of willis|cow)\b"), ("neck", r"\bneck\b"), ("iac", r"\b(?:iacs?|internal auditory canals?)\b"),
                                   ("orbits", r"\borbits?\b"), ("pituitary", r"\b(?:pituitary|sella)\b"), ("cspine", r"\b(?:c ?spine|cervical)\b"),
                                   ("tspine", r"\b(?:t ?spine|thoracic)\b"), ("lspine", r"\b(?:l ?spine|lumbar)\b"), ("temporal", r"\b(?:temporal bones?|petrous)\b"),
                                   ("bue", r"\b(?:bue|upper|arms?)\b"), ("ble", r"\b(?:ble|lower|legs?)\b")) if re.search(rx, p)}
    if re.search(r"\b(?:with and without|with and w/o|w/wo|with/without|with or without)\b", p):
        contrast = "wwo"
    elif re.search(r"\b(?:without|w/o|no contrast|noncontrast|non contrast)\b", p):
        contrast = "wo"
    elif re.search(r"\bwith\b", p) and re.search(r"\b(?:contrast|gad\w*)\b", p):
        contrast = "w"
    else:
        contrast = ""
    return mod, regions, contrast


def _imaging_order(phrase: str) -> str:
    """The order box's own wording for a study as said ("MRI brain with and without" -> "MRI Brain with and w/o contrast
    70553"), or the words as said when the list has nothing like it ("CT neck")."""
    mod, regions, contrast = _study_bits(phrase)
    if not regions and mod in ("mri", "ct", "cta", "mra"):
        regions = {"brain"}                        # "get an MRI": the brain, in this clinic
    best, best_score = "", None
    for opt in phrases.IMAGING_ORDERS:
        omod, oreg, ocon = _study_bits(opt)
        if not mod or omod != mod or (regions and not regions & oreg):
            continue
        score = 3 + 2 * len(regions & oreg) - len(oreg - regions) - 2 * len(regions - oreg)
        score += (2 if ocon == contrast else -3) if contrast else (1 if ocon in ("", "wo") else 0)
        if best_score is None or score > best_score:
            best, best_score = opt, score
    return best or _cased(phrase)


def _study_name(phrase: str) -> str:
    """A reviewed study's name for its row: "MRI Brain w/wo", "CTA Head/Neck", "EMG/NCV BLE", "Zeto EEG"."""
    mod, regions, contrast = _study_bits(phrase)
    label = {"zeto eeg": "Zeto EEG", "eeg": "EEG", "vng": "VNG", "vemp": "VEMP", "vng/vemp": "VNG/VEMP", "cdp": "CDP", "audiogram": "Audiogram",
             "emg": "EMG/NCV", "echo": "Echo", "hst": "HST", "datscan": "DaT Scan", "carotid": "Carotid ultrasound", "pet": "PET Brain",
             "mri": "MRI", "mra": "MRA", "ct": "CT", "cta": "CTA", "x-ray": "X-ray", "imaging": "Imaging"}.get(mod)
    if not label:
        return _cased(phrase)
    if mod == "emg":
        return label + (" BUE" if "bue" in regions else " BLE" if "ble" in regions else "")
    if mod not in ("mri", "mra", "ct", "cta", "x-ray"):
        return label
    if not regions and mod != "x-ray":
        regions = {"brain"}
    head = ["Brain" if mod == "mri" else "Head"] if "brain" in regions else []
    if "neck" in regions:
        head = ["/".join(head + ["Neck"])]
    names = {"iac": "IAC", "orbits": "Orbits", "pituitary": "Pituitary", "cspine": "C-spine", "tspine": "T-spine", "lspine": "L-spine", "temporal": "Temporal bone"}
    parts = head + [names[r] for r in ("iac", "orbits", "pituitary", "cspine", "tspine", "lspine", "temporal") if r in regions]
    return " ".join([label] + parts) + {"wwo": " w/wo", "wo": " w/o", "w": " w/"}.get(contrast, "")


def _result_text(raw: str) -> str:
    """What a report showed, as the result box says it: "Normal", "Unremarkable.", "Left caloric weakness."."""
    raw = re.sub(r"^\s*(?:a|an|the|some|to be|essentially|basically|completely|entirely|otherwise|overall|again)\s+", "", raw.strip(" ,:-"))
    low = raw.lower().rstrip(".")
    if not low:
        return ""
    if low in ("normal", "wnl", "within normal limits", "fine", "okay", "ok", "good", "normal study"):
        return "Normal"
    if low in ("unremarkable", "negative", "clean", "clear", "benign", "stable"):
        return low.capitalize() + "."
    text = wording.clinical(raw)
    text = text[:1].upper() + text[1:]
    return text if text.endswith((".", "!", "?")) else text + "."


def _date_text(d: str) -> str:
    d = " ".join(d.split()).rstrip(",")
    if not d or re.search(r"\b(?:last|ago)\b", d):
        return ""                              # relative to today: the date box takes a date
    return re.sub(rf"\b{_MONTH}", lambda m: m.group(0).capitalize(), d, flags=re.I)


def _studies_and_orders(t: str, orig: str, res: Interpretation, section: str) -> tuple[str, str]:
    """Reviewed reports into res.studies and today's orders into res.imaging. In `t` (the words the checkbox pass reads)
    each one becomes a placeholder, so "normal" or "left" in a study's result ticks nothing; under Studies it comes out of
    the narrative too, since the rows are what was said. Returns (t, orig)."""
    orders_ok, studies_ok = section not in _NO_ORDERS_IN, section not in _NO_STUDIES_IN
    if not (orders_ok or studies_ok):
        return t, orig
    t = _IMAGE_RE.sub("order mri ", t)         # "let's image the IACs": an MRI, in this clinic
    m = _NO_IMAGING.search(t)
    if m and orders_ok:
        res.imaging.append("No Imaging Required.")
        res.notes.append("Order: No Imaging Required.")
        t = t[: m.start()] + " studyref " + t[m.end():]
    pos, last = 0, ""
    while True:
        m = _STUDY_RE.search(t, pos)
        if not m:
            break
        study, start, end = m.group("study"), m.start(), m.end()
        while re.search(r"\s(?:of|the|and)$", study) or (study.endswith(" with") and not study.endswith(" and with")):
            study = study.rsplit(None, 1)[0]
        end = start + len(study)
        lead = _STUDY_LEAD_RE.search(t[:start])
        if lead:
            study, start = lead.group(1) + study, lead.start()
        before, after = t[:start], t[end:]
        # "from June", "at the ER", "6/12/26": where and when, in either order, then what it showed
        date = where = ""
        skip = 0
        for _ in range(2):
            d = _DATE_AFTER.match(after[skip:])
            if d and not date:
                date, skip = _date_text(d.group("date")), skip + d.end()
                continue
            w = _WHERE_AFTER.match(after[skip:])
            if w and not where and not _RESULT_RE.match(after[skip:]):
                where, skip = w.group("where"), skip + w.end()
        after2 = after[skip:]
        review = _REVIEW_BEFORE.search(before)
        cue = review or _ORDER_BEFORE.search(before)
        r = _RESULT_RE.match(after2)
        result, result_at = (r.group("result"), r.start("result")) if r else ("", 0)
        if not result.strip() and review and re.match(r"^\s*[:,-]\s*\S", after2):     # "reviewed the CDP from 3/2: composite 54"
            m2 = re.match(r"^\s*[:,-]\s*(?P<result>[^.;]*)", after2)
            result, result_at = m2.group("result"), m2.start("result")
        ordered = bool(_ORDER_BEFORE.search(before) or _ORDER_AFTER.match(after2) or (last == "order" and _ANOTHER_ORDER.search(before))) \
            and not _HISTORY_BEFORE.search(before) and not _OTHER_ORDERED.search(before)
        span_start, span_end = (cue.start() if cue else start), end
        if result.strip() and not _ORDER_AFTER.match(after2) and studies_ok:
            cut = _RESULT_CUT.search(result)
            if cut:
                result = result[: cut.start()]
            name = _study_name(study) + (f" @ {where.upper() if len(where) <= 4 else where.title()}" if where else "")
            text = _result_text(result)
            if not any(s["name"] == name and s["date"] == date for s in res.studies):
                res.studies.append({"name": name, "date": date, "result": text})
                res.notes.append(f"Study: {name}" + (f" ({date})" if date else "") + (f": {text}" if text else ""))
            span_end, last = end + skip + result_at + len(result), "study"
        elif ordered and orders_ok:
            name = _imaging_order(study)
            if name not in res.imaging:
                res.imaging.append(name)
                res.notes.append(f"Order: {name}")
                if section not in ("plan", "impression"):      # the plan says what was ordered; here the words are elsewhere
                    line = f"- Ordered: {name}"
                    res.append["plan-descr"] = (res.append["plan-descr"] + "\n" + line) if res.append.get("plan-descr") else line
            a = _ORDER_AFTER.match(after2)
            span_end, last = end + skip + (a.end() if a else 0), "order"
        else:
            pos, last = end, ""
            continue
        removed = t[span_start:span_end]
        if section == "studies":                 # the rows are the narrative here
            rx = r"\s+".join(re.escape(w) for w in removed.split())
            orig = re.sub(rx, " ", orig, count=1, flags=re.I)
        hole = " studyref " if section != "studies" else " "
        t = t[:span_start] + hole + t[span_end:]
        pos = span_start + len(hole)
    if section == "studies":
        orig = " ".join(orig.split()).strip(" .,;")
    return t, orig


GOGGLE_TEST = re.compile(r"\b(?P<dix>dix[- ]?hallpike|hallpike|dix)\b|\b(?P<sup>supine|roll test|head roll|supine roll|geotropic|ageotropic)\b|"
                         r"\b(?P<gaze>gaze)\b", re.I)


def _goggles(t: str, res: Interpretation, affirmed: list[str]) -> None:
    """Positional testing as said: each part takes its row from the test named (Dix-Hallpike, supine roll, gaze) and its
    side from the nearest left / right / center word, so "left Dix-Hallpike positive with torsional nystagmus, right side
    normal" ticks the left row's boxes and the right row's Normal."""
    test, parts = "", re.split(r"[,;.]|\s+(?=(?:right|left|center)\s+(?:side|dix|hallpike|supine|roll|gaze|ear)\b)", t)
    for part in parts:
        if not part.strip():
            continue
        m = GOGGLE_TEST.search(part)
        if m:
            test = "dixhall" if m["dix"] else ("supine" if m["sup"] else "gaze")
        sides = [(x.start(), x.group(1).lower()) for x in re.finditer(r"\b(right|left|center|middle)\b", part, re.I)
                 if not re.search(r"\b(?:than|not|no)\s+(?:the\s+)?$", part[: x.start()], re.I)]
        if not test or not sides:
            continue
        # the side the test is on: "left dix hallpike", "positive on the right"; a "left beating" side is a finding
        side = next((s for pos, s in sides if not re.match(r"[\s-]*(?:beat\w*|evoked)", part[pos + len(s):])), sides[0][1])
        side = "center" if side == "middle" else side
        prefix = f"{test}-{side}-"
        for name in _candidates("goggles"):
            if not name.startswith(prefix):
                continue
            for w in _label_words(name):
                if w and len(w) >= 3 and re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", part) and not re.search(
                        rf"\b(?:no|not|without|negative for)\s+(?:\w+\s+)?{re.escape(w)}", part):
                    res.set[name] = True
                    _clear_exclusive(res, name)
                    affirmed.append(FIELDS[name]["label"])
                    break
