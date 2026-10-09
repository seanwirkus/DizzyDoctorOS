"""A recorded or pasted visit into the note.

Two kinds of speech reach the note. Dictation is the provider talking to the note (dictation.interpret reads it). A
visit recording is the whole visit, provider and patient talking, and it is read differently: keyword rules can't
read a conversation as if it were the provider's notes ("Any ringing in your ears?" "No." mentions tinnitus without
the patient having it). So the conversation is first turned into plain statements about the patient (answered
questions, the patient's and a companion's own words, the provider's summaries back to the patient) and then read like
typed notes (story.read_notes). Only what the conversation makes plain is proposed; nothing is applied without the
provider, and the note's wording comes from the practice's own sentences (compose.py), never from a language model.

The audio never leaves this computer: it is transcribed here in 30-second parts while the visit goes on, and each
part is deleted once it is transcribed.
"""
from __future__ import annotations

import os
import queue
import re
import threading
import time

from . import dictation, story
from .compose import box_text, boxes_proposal
from .note import FIELDS

# ---------------------------------------------------------------------------------------------------- transcribing

_visits: dict[str, dict] = {}
_lock = threading.Condition()
_jobs: queue.Queue = queue.Queue()
_worker: threading.Thread | None = None


def _work(model: str, data_dir: str) -> None:
    while True:
        visit_id, seq, path = _jobs.get()
        try:
            text = dictation.transcribe(path, model, data_dir)
        except Exception as ex:  # noqa: BLE001 - one bad part shouldn't lose the visit
            text = f"[part {seq + 1} could not be transcribed: {ex}]"
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
        with _lock:
            visit = _visits.get(visit_id)
            if visit is not None:
                visit["parts"][seq] = text
                visit["pending"].discard(seq)
            _lock.notify_all()


def add_part(visit_id: str, seq: int, wav_path: str, model: str = "small.en", data_dir: str = "") -> None:
    """A 30-second part of a visit recording, transcribed in the background in the order it arrived."""
    global _worker
    with _lock:
        visit = _visits.setdefault(visit_id, {"parts": {}, "pending": set(), "started": time.time()})
        visit["pending"].add(seq)
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_work, args=(model, data_dir), daemon=True, name="visit-transcriber")
            _worker.start()
    _jobs.put((visit_id, seq, wav_path))


def progress(visit_id: str) -> dict:
    with _lock:
        visit = _visits.get(visit_id) or {"parts": {}, "pending": set()}
        return {"done": len(visit["parts"]), "waiting": len(visit["pending"])}


def finish(visit_id: str, timeout: float = 900) -> str:
    """The whole transcript once every part is transcribed (the visit is then forgotten here)."""
    end = time.time() + timeout
    with _lock:
        while _visits.get(visit_id, {}).get("pending") and time.time() < end:
            _lock.wait(timeout=1.0)
        visit = _visits.pop(visit_id, None) or {"parts": {}}
    return " ".join(visit["parts"][k].strip() for k in sorted(visit["parts"]) if visit["parts"][k].strip())


# ---------------------------------------------------------------------------------------------------- reading it here

# What the patient may say for each headline symptom on the form (and falls).
TERMS = {
    "dizziness": ("dizzy", "dizziness", "vertigo", "spinning", "spins", "lightheaded", "light-headed", "woozy"),
    "hearingloss": ("hearing", "hard of hearing", "hear as well", "hear well"),
    "tinnitus": ("ringing", "tinnitus", "buzzing in", "humming in"),
    "headache": ("headache", "headaches", "migraine", "migraines"),
    "gaitataxia": ("balance", "unsteady", "off balance", "imbalance", "wobbly"),
    "falls": ("fall", "falls", "fallen", "fell"),
}
YES = re.compile(r"^\W*(yes|yeah|yep|yup|sometimes|a little|a bit|kind of|sort of|i do|it does|i have|i did|definitely|"
                 r"mm-?hmm|uh-?huh|often|all the time|every day|occasionally)\b", re.I)
NO = re.compile(r"^\W*(no|nope|nah|not really|never|not at all|i don'?t|it doesn'?t|i haven'?t|i didn'?t|none|nothing like that)\b", re.I)
FIRST_PERSON = re.compile(r"\b(i(?:'ve| have| get| got| feel| felt| am|'m| keep| started| noticed| had)|my)\b", re.I)
NEGATION = re.compile(r"\b(no|not|never|don'?t|doesn'?t|haven'?t|hasn'?t|didn'?t|without|denies)\b", re.I)


def _mentions(sentence: str) -> list[str]:
    s = sentence.lower()
    return [k for k, words in TERMS.items() if any(re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", s) for w in words)]


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.?!])\s+", text or "") if s.strip()]


PROVIDER_ROLES = re.compile(r"^(?:dr\.?(?:\s+\w+)?|doctor|provider|physician|clinician|pa|np|nurse|ma|interviewer)$", re.I)
PATIENT_ROLES = re.compile(r"^(?:patient|pt|interviewee)$", re.I)
SPEAKER_NUM = re.compile(r"^(?:speaker\s*\d+|spk\s*\d+|s\d)$", re.I)
INLINE_SPEAKER = re.compile(r"(?:(?<=^)|(?<=[.?!\n]))\s*(?=(?:dr\.?(?:\s+[A-Z]\w+)?|doctor|provider|physician|clinician|PA|NP|nurse|MA|"
                            r"patient|pt|speaker\s*\d+|wife|husband|spouse|partner|daughter|son|mother|mom|father|dad|caregiver|companion|"
                            r"family|friend|sister|brother)\s*(?:\(\w+\))?\s*:)", re.I)
# Provider sentences that aren't the patient's history: plans, instructions, tests about to be done.
CONDITIONAL = re.compile(r"\b(?:if|whether|to see|let'?s|let me|going to|gonna|i'?ll|we'?ll|will you|would you|should|try to|can you|could you|"
                         r"i want you|i'd like|we can|we could|i'm going|i am going|follow my finger|look at my|squeeze|push against)\b", re.I)
SUMMARY = re.compile(r"^(?:so|okay so|ok so|alright so|all right so|it sounds like|sounds like|so it sounds like|so basically)\b[, ]*(?:.*?\b)?"
                     r"you(?:'ve| have| had| get| got| are|'re| said| mentioned| told me| feel| notice)(?:\s+that)?(?:\s+been)?(?:\s+having)?\s+", re.I)
WH = [(re.compile(r"^how long (?:does|do|did|will) (?:it|they|that|this|the \w+|each \w+) (?:usually )?last", re.I), "Lasts {}"),
      (re.compile(r"^what (?:makes|made) (?:it|them|that|the \w+) worse", re.I), "Worse with {}"),
      (re.compile(r"^(?:what (?:makes|made) (?:it|them|that|the \w+) better|does anything (?:help|make it better)|what helps)", re.I), "Better with {}"),
      (re.compile(r"^(?:which|what) (?:ear|side)", re.I), "{} ear"),
      (re.compile(r"^(?:how often|how frequently)", re.I), "Episodes {}"),
      (re.compile(r"^(?:what brings you|what can i do for you|how can i help|tell me (?:about|what)|what'?s going on|what seems to be)", re.I), "{}")]


# A question asking about several things at once ("blood pressure, diabetes?", "ringing or hearing loss?").
LIST_QUESTION = re.compile(r",\s*\w|\w\s+or\s+\w", re.I)
# A companion's sentence that is about the patient (she / he / mom ...), not about the companion.
ABOUT_PATIENT = re.compile(r"\b(?:she|he|her|him|his|hers|they|them|their|mom|mother|dad|father|husband|wife|grandma|grandpa)\b", re.I)
SOMEONE_ELSE = re.compile(r"\bi'?m the one\b|\bi am the one\b|,?\s*not (?:him|her|them)\b", re.I)


def _turns(transcript: str) -> list[tuple[str, str]]:
    """The transcript as (speaker, words). Without speaker labels it is one turn with no speaker."""
    text = (transcript or "").replace("\r\n", "\n").replace("\r", "\n")
    pieces = [p for p in INLINE_SPEAKER.split(text) if p.strip()]
    turns: list[list[str]] = []
    for p in pieces:
        m = story.SPEAKER.match(p)
        if m:
            turns.append([m.group("who").strip().lower(), p[m.end():].strip()])
        elif turns:
            turns[-1][1] += " " + p.strip()
        else:
            turns.append(["", p.strip()])
    if not any(who for who, _ in turns):
        return [("", " ".join(t for _, t in turns))]
    # who is who: named roles, else the numbered speaker asking the most questions is the provider and the one
    # saying "I" the most is the patient
    asks: dict[str, int] = {}
    me: dict[str, int] = {}
    for who, words in turns:
        asks[who] = asks.get(who, 0) + words.count("?")
        me[who] = me.get(who, 0) + len(re.findall(r"\b(?:i|i'm|i've|my|me)\b", words, re.I))
    numbered = [w for w in asks if SPEAKER_NUM.match(w) or not w]
    provider = max(numbered, key=lambda w: asks[w], default="") if numbered and not any(PROVIDER_ROLES.match(w) for w in asks) else ""
    patient = ""
    if not any(PATIENT_ROLES.match(w) for w in asks):
        rest = [w for w in numbered if w != provider]
        patient = max(rest, key=lambda w: me[w], default="")

    def role(who: str) -> str:
        if PROVIDER_ROLES.match(who) or who == provider:
            return "provider"
        if PATIENT_ROLES.match(who) or who == patient:
            return "patient"
        return "companion"
    return [(role(who), words) for who, words in turns]


def _as_statement(question: str) -> str:
    """ "Is it worse when you stand up?" -> "worse when you stand up" """
    q = question.strip().rstrip("?").strip()
    q = re.sub(r"^(?:(?:so|and|okay|ok|now|alright|well|um|uh|also|then|and then)[,\s]+)+", "", q, flags=re.I)
    q = re.sub(r"^(?:do|does|did|are|is|was|were|have|has|had|can|could|would|will)\s+(?:you|it|they|she|he|there|that|this|things)?\s*"
               r"(?:ever\s+|still\s+|also\s+|usually\s+|ever\s+had\s+)?(?:been\s+|had\s+|have\s+|get\s+|got\s+|feel\s+|notice\s+|noticed\s+|"
               r"experience\s+|experienced\s+|having\s+|getting\s+)?", "", q, flags=re.I)
    q = re.sub(r"\b(?:your|you)\b\s*", "", q, flags=re.I)
    return " ".join(q.split())


def visit_statements(transcript: str, sources: dict | None = None) -> list[str]:
    """What the conversation says about the patient, as plain statements: answered questions ("Any ringing?" "No."
    -> "No any ringing."), the patient's own words, a companion's, and the provider's summaries back to the patient."""
    turns = _turns(transcript)
    labelled = any(role for role, _ in turns)
    seq = [(role, s) for role, words in turns for s in _sentences(words)]
    out: list[str] = []
    used: set[int] = set()
    for i, (role, s) in enumerate(seq):
        if i in used:
            continue
        asker = role in ("provider", "") and s.endswith("?")
        if asker:
            j = i + 1
            if labelled:
                while j < len(seq) and seq[j][0] == "provider" and j - i < 3:
                    j += 1
            if j >= len(seq) or (labelled and seq[j][0] == "provider"):
                continue
            answer = seq[j][1]
            q = _as_statement(s)
            rest = re.sub(r"^\W*(?:yes|yeah|yep|yup|no|nope|nah|mm-?hmm|uh-?huh)\b[\s,.!]*", "", answer, flags=re.I)
            wh = next((tpl for rx, tpl in WH if rx.match(s)), None)
            said = f"{s} \u2014 {answer}"
            if wh and not NO.search(answer) and not YES.search(answer):
                out.append(wh.format(answer.rstrip(".")))
            elif NO.search(answer) and q:
                out.append(f"No {q}." + (f" {rest}" if rest and not NO.search(rest) else ""))
            elif YES.search(answer) and q and LIST_QUESTION.search(q):
                # "Any medical problems, blood pressure, diabetes?" "Yes, just the blood pressure": the yes covers only
                # what the answer names, not every item asked about
                if not rest or NO.search(rest):
                    continue
                out.append(rest)
            elif YES.search(answer) and q:
                out.append(f"{q[:1].upper() + q[1:]}" + (f", {rest}" if rest else "."))
            else:
                continue
            used.add(j)
            if sources is not None:
                sources[out[-1]] = said
            continue
        if role == "provider":
            m = SUMMARY.match(s)
            if m and not CONDITIONAL.search(s):
                out.append(s[m.end():])
                if sources is not None:
                    sources[out[-1]] = s
            continue
        if role in ("patient", "companion") or (not labelled and FIRST_PERSON.search(s)):
            if CONDITIONAL.search(s) and role != "patient":
                continue
            if role == "companion" and FIRST_PERSON.search(s) and not ABOUT_PATIENT.search(s):
                continue                       # a relative talking about themselves, not the patient
            if SOMEONE_ELSE.search(s):
                continue                       # "I'm the one with the ringing, not him": the speaker isn't the patient
            if not labelled and re.match(r"^\s*i\s+(?:think|suspect|believe|would|recommend|want)\b", s, re.I):
                continue                       # the provider thinking aloud
            out.append(s)
    return out


def visit_reading(transcript: str, values: dict | None = None) -> dict:
    """The visit read on this computer, as Build proposals and points to check."""
    values = values or {}
    sources: dict[str, str] = {}
    statements = visit_statements(transcript, sources)
    r = story.read_notes("\n".join(statements), topics=story.topics_of(values))
    # quote the conversation, not the statement it was turned into
    short = {story._short(k, 90): v for k, v in sources.items()}
    longer = {story._short(k): v for k, v in sources.items()}
    r.why = {k: story._short(short.get(w, w), 120) for k, w in r.why.items()}
    for i, line in enumerate(r.unclear):
        for stmt, said in longer.items():
            if f"\u201c{stmt}\u201d" in line:
                r.unclear[i] = line.replace(f"\u201c{stmt}\u201d", f"\u201c{story._short(said)}\u201d")
    aloud = dictation.interpret(transcript, "vitals")
    wanted = dict(r.set)
    wanted.update({k: v for k, v in aloud.set.items() if not k.startswith(HEAD_PREFIXES) and k not in wanted})
    todo: list[str] = []
    prop = boxes_proposal("visit", "Boxes from the visit",
                          "What the conversation makes plain: answered questions and what the patient said. Untick anything that's wrong.",
                          wanted, values, r.why, todo)
    meds = story.find_meds(transcript)
    notes = [n for n in aloud.notes if not n.startswith(("Not placed", "Section"))]
    return {"proposals": [prop.as_dict()] if prop else [], "unclear": r.unclear, "notes": notes, "meds": meds,
            "med_notes": r.med_notes, "todo": todo, "story": "\n".join(statements),
            "append": aloud.append, "imaging": aloud.imaging, "studies": aloud.studies}


HEAD_PREFIXES = ("dizziness", "hearingloss", "tinnitus", "gaitataxia", "headache")


def read_visit(transcript: str) -> dictation.Interpretation:
    """What a visit conversation says, read on this computer, as field values. Only what the conversation makes plain
    is ticked; a symptom the patient both has and denies is left alone."""
    res = dictation.Interpretation(section="hpi")
    r = story.read_notes("\n".join(visit_statements(transcript)))
    res.set.update(r.set)
    aloud = dictation.interpret(transcript, "vitals")
    res.set.update({k: v for k, v in aloud.set.items() if not k.startswith(HEAD_PREFIXES) and k not in res.set})
    res.notes += [n for n in aloud.notes if not n.startswith(("Not placed", "Section"))]
    ticked = [box_text(k) for k, v in r.set.items() if v is True and FIELDS[k]["type"] == "checkbox"]
    if ticked:
        res.notes.insert(0, "From the conversation: " + ", ".join(ticked))
    res.notes += r.unclear
    return res
