"""Affected distributions: which dermatomes, nerve territories and ear a note's complaints point at.

The SnapShot body map is coloured from here. Marks come from three places: the note's own tick
boxes (hearing loss on the left, right posterior canal BPPV), the note's text (chief complaint,
HPI, impressions, symptom descriptions, exam findings, study results), and what the provider
marked by hand on the map. Hand marks live in the note's values under "distributions" as a
short string ("L5-L:pain ear-R:hearing -C6-R") and, because the form has no box for them, in
a small sidecar next to the filed PDF.

The region catalog (ids, labels, colours) is web/dermatomes.json, shared with the drawing.
"""
from __future__ import annotations

import json
import os
import re

from . import note

HERE = os.path.dirname(os.path.abspath(__file__))
CATALOG_PATH = os.path.join(HERE, "web", "dermatomes.json")
with open(CATALOG_PATH, encoding="utf-8") as _f:
    CATALOG: dict = json.load(_f)
CATEGORIES: list[str] = [c["id"] for c in CATALOG["categories"]]
REGIONS: dict[str, dict] = {r["id"]: r for r in CATALOG["regions"]}
SIDES = ("L", "R")
SIDE_WORD = {"L": "left", "R": "right"}

# the spine, top to bottom, for ranges like "L4-S1"
SPINE = [f"C{i}" for i in range(2, 9)] + [f"T{i}" for i in range(1, 13)] + [f"L{i}" for i in range(1, 6)] + ["S1", "S2", "S345"]
DERMATOMES = list(SPINE)
ARM = ["C5", "C6", "C7", "C8", "T1", "T2"]
LEG = ["L2", "L3", "L4", "L5", "S1", "S2"]
HANDS = ["C6", "C7", "C8"]
FEET = ["L4", "L5", "S1"]
FACE = ["V1", "V2", "V3"]


# --------------------------------------------------------------------------- ids

def region_id(base: str, side: str) -> str:
    return f"{base}-{side}"


def split_id(full: str) -> tuple[str, str] | None:
    m = re.fullmatch(r"([A-Za-z0-9]+)-([LR])", (full or "").strip())
    if not m or m.group(1) not in REGIONS:
        return None
    return m.group(1), m.group(2)


def label(full: str) -> str:
    parts = split_id(full)
    if not parts:
        return full
    base, side = parts
    spec = REGIONS[base]
    if base == "ear":
        return f"{SIDE_WORD[side].capitalize()} ear"
    return f"{spec['label']} {SIDE_WORD[side]}"


def _spine_base(letter: str, n: int) -> str | None:
    letter = letter.upper()
    if letter == "S" and n in (3, 4, 5):
        return "S345"
    base = f"{letter}{n}"
    return base if base in REGIONS else None


def _spine_range(a: str, b: str) -> list[str]:
    i, j = SPINE.index(a), SPINE.index(b)
    if i > j:
        i, j = j, i
    return SPINE[i:j + 1]


# --------------------------------------------------------------------------- the words

# What the note says is wrong there. Headache subsumes pain ("occipital neuralgia" is a headache).
CUES = {
    "numbness": r"numb\w*|tingl\w*|paresthes\w*|pins and needles|hypo?esthes\w*|hypesthes\w*|dysesthes\w*|an?esthesia|"
                r"(decreased|diminished|reduced|absent|loss of|impaired|altered) (\w+ ){0,3}sensat\w*|sensory (loss|deficit|change|disturbance|level)|"
                r"sensation (is |was )?(decreased|diminished|reduced|absent|impaired)|\bsensory\b|neuropath\w*|"
                r"(decreased|diminished|reduced|absent) (\w+ ){0,2}(vibration|proprioception|pinprick|light touch|monofilament|temperature)",
    "pain": r"\bpain\w*|\bach(e|es|ing|y)\b|burning|sciatica|neuralgia|allodynia|radicul\w*|radiat\w*|shooting|electric|\bsore\b|tender\w*|neuropathic",
    "weakness": r"\bweak\w*|paresis|paralys\w*|plegi\w*|palsy|\bdrop\b|droop\w*|\b[0-4](?:\+|-)?/5\b|(decreased|reduced|diminished) (\w+ ){0,2}strength|pronator drift|atroph\w*|cannot lift|unable to lift|give[s]?[- ]way",
    "headache": r"headache\w*|\bHA\b|\bHAs\b|migrain\w*|cephalg\w*|cephalal\w*|head pain|neuralgia",
    "hearing": r"hearing loss|decreased hearing|hearing (is |was )?(decreased|reduced|diminished|muffled|worse)|\bhearing\b|\bHL\b|SNHL|\bdeaf\w*|hard of hearing|hypoacusis|presbycusis|"
               r"tuning fork|finger (friction )?rub|aural fullness|\bfullness\b|hearing aid",
    "tinnitus": r"tinnitus|ringing|buzzing|hissing|roaring|pulsatile",
    "vertigo": r"vertig\w*|\bBPPV\b|canalith\w*|cupulolith\w*|labyrinth\w*|m[eé]ni[eè]re\w*|vestibul\w*|spinning|nystagmus|epley|otolith\w*|neuritis|neuronitis|hypofunction|caloric|canal\b",
}
CUE_RX = {k: re.compile(v, re.I) for k, v in CUES.items()}
EAR_CATEGORIES = ("hearing", "tinnitus", "vertigo")
# a spine level named with its distribution (not a disc or a fusion)
DISTRIBUTION_WORD = re.compile(r"radicul\w*|dermatom\w*|distribution|\broot\b|myotom\w*|radiat\w*|sensory|motor|numb\w*|weak\w*|pain\w*|tingl\w*|paresthes\w*|territory", re.I)
STRUCTURAL = re.compile(r"\s*(?:-|–)?\s*(?:disc|disk|herniat\w*|protru\w*|bulg\w*|extru\w*|stenos\w*|foramin\w*|fusion|fused|laminect\w*|discect\w*|ACDF|spondylo\w*|listhesis|level|interspace|facet|vertebra\w*|fracture|compression|degenerat\w*|DDD|DJD|spine|injection|ESI|epidural|block|facetectomy|hardware|weighted|FLAIR|hyperintens\w*|signal)", re.I)
# "no / denies / without X" before the mention takes it back; "normal / intact" does unless the clause also says what is wrong
NEGATION_BEFORE = re.compile(r"\b(?:no|not|denies|denied|deny|without|negative for|free of|w/o|rules? out|ruled out|r/o|resolved|resolution of)\b|\(-\)", re.I)
NORMAL_WORD = re.compile(r"\b(?:normal|intact|WNL|unremarkable|symmetric\w*|nonfocal|non-focal|5/5|grossly intact)\b", re.I)
ABNORMAL_WORD = re.compile(r"\b(?:decreased|reduced|diminished|absent|abnormal|deficit\w*|loss|impaired|weak\w*|numb\w*|tingl\w*|pain\w*|paresthes\w*|radicul\w*|neuropath\w*|palsy|drop|droop\w*|[0-4](?:\+|-)?/5|hypo\w*|dys\w*|positive|\(\+\)|lateraliz\w*|asymmetr\w*|worse|worsen\w*|present|hearing loss|tinnitus|vertigo|BPPV)\b", re.I)
# a side that is a direction, not a side of the body ("vertigo turning to the right")
DIRECTION = re.compile(r"turn\w*|roll\w*|look\w*|gaz\w*|beat\w*|veer\w*|lean\w*|fall\w*|lying|side-?lying|rotat\w*|tilt\w*|drift\w*|deviat\w*|lateraliz\w*|toward|to the$", re.I)
PLURAL_BOTH = re.compile(r"(feet|hands|arms|legs|toes|fingers|thumbs|knees|ankles|heels|shoulders|thighs|calves|hips|ears|soles|buttocks|extremities|forearms|wrists|shins|eyes|cheeks|temples|digits|\bBUE\b|\bBLE\b|\bAU\b|bilateral\w*|\bb/l\b|both)", re.I)

# Named roots, nerves and the ear: a bare mention is enough (its category is the region's default).
EXPLICIT: list[tuple[re.Pattern, list[str]]] = [(re.compile(rx, re.I), bases) for rx, bases in [
    (r"\bV\s?1\b|\bophthalmic\b", ["V1"]), (r"\bV\s?2\b|\bmaxillary\b", ["V2"]), (r"\bV\s?3\b|\bmandibular\b", ["V3"]),
    (r"\btrigeminal\b", FACE),
    (r"greater occipital|\bGON\b|occipital neuralgia", ["GON"]), (r"lesser occipital|\bLON\b", ["LON"]),
    (r"\bmedian (nerve|neuropath\w*|sensory|motor|distribution|entrapment)|\bmedian\b|carpal tunnel|\bCTS\b", ["median"]),
    (r"\bulnar\b|cubital tunnel|guyon", ["ulnar"]),
    (r"\bradial (nerve|neuropath\w*|palsy|sensory|motor|distribution)|wrist ?drop|saturday night palsy|posterior interosseous", ["radial"]),
    (r"lateral femoral cutaneous|\bLFCN\b|meralgia( paresthetica)?", ["LFCN", "L2"]),
    (r"\bperoneal\b|\bfibular (nerve|neuropath\w*|palsy|head)|foot ?drop", ["peroneal"]), (r"\bsural\b", ["sural"]), (r"\bsaphenous\b", ["saphenous"]),
]]
# Body parts: need a cue in the same clause (so "5/5 strength x 4 extremities" marks nothing).
PARTS: list[tuple[re.Pattern, list[str], bool]] = [(re.compile(rx, re.I), bases, both) for rx, bases, both in [
    (r"stocking[\s-]*(and[\s-]*)?glove|glove[\s-]*(and[\s-]*)?stocking", HANDS + FEET, True),
    (r"stocking", FEET, True), (r"glove", HANDS, True),
    (r"length[\s-]dependent|polyneuropathy|peripheral neuropathy|distal symmetric", FEET, True),
    (r"hemi-?body|hemi-?sensory|hemi-?an?esthesia|half of (the|her|his) body", DERMATOMES + FACE, False),
    (r"hemi-?paresis|hemi-?plegia|hemi-?paretic|hemi-?plegic", ARM + LEG, False),
    (r"hemi-?facial|half of (the|her|his) face", FACE, False),
    (r"\bface\b|\bfacial\b", FACE, False),
    (r"forehead|\bbrow\b|periorbital|retro-?orbital|supraorbital|\borbit(al)?\b|\bvertex\b|top of (the )?head|\bfrontal\b|\beyes?\b", ["V1"], False),
    (r"\bcheeks?\b|\bmalar\b|upper lip|side of (the )?nose|\bnose\b|infraorbital|zygoma\w*", ["V2"], False),
    (r"\bjaw\b|\bchin\b|lower lip|mandible|\btemples?\b|\btemporal\b|preauricular|auriculotemporal", ["V3"], False),
    (r"\boccipital\b|\bocciput\b|back of (the )?head|suboccipital|\bnuchal\b", ["GON", "LON"], False),
    (r"\bglobal\b|holocephalic|whole head|entire head|all over (the )?head|band-?like|bifrontal|bitemporal|holocranial", ["V1", "GON"], True),
    (r"\bneck\b", ["C3", "C4"], False),
    (r"\bshoulders?\b|\bdeltoid\b|supraclavicular", ["C4", "C5"], False),
    (r"upper extremit(y|ies)|\bUEs?\b|\bBUE\b|\barms?\b", ARM, False),
    (r"\bforearms?\b", ["C6", "C7", "C8", "T1"], False),
    (r"\bhands?\b|\bwrists?\b", HANDS, False), (r"\bfingers?\b(?!\s*(?:friction|rub))|\bdigits?\b", HANDS, False),
    (r"\bthumbs?\b", ["C6"], False), (r"index finger|2nd digit|second (finger|digit)", ["C6"], False),
    (r"middle finger|3rd digit|third (finger|digit)|long finger", ["C7"], False),
    (r"ring finger|4th digit|fourth (finger|digit)", ["C8"], False), (r"little finger|small finger|pinky|5th digit|fifth (finger|digit)", ["C8"], False),
    (r"\bchest\b|\bthorax\b|\bbreasts?\b", ["T2", "T3", "T4", "T5", "T6"], False), (r"\bnipples?\b", ["T4"], False),
    (r"\bxiphoid\b|epigastr\w*", ["T6", "T7"], False), (r"umbilic\w*|\bnavel\b|belly button", ["T10"], False),
    (r"\babdom\w*|\bbelly\b", ["T7", "T8", "T9", "T10", "T11", "T12"], False), (r"\bflanks?\b|\bloin\b", ["T10", "T11", "T12"], False),
    (r"\btrunk\b|\btorso\b", [f"T{i}" for i in range(2, 13)], False),
    (r"\bgroin\b|\binguinal\b", ["L1"], False), (r"\bhips?\b", ["L1", "L2"], False),
    (r"lower extremit(y|ies)|\bLEs?\b|\bBLE\b|\blegs?\b", LEG, False),
    (r"anterior thigh|front of (the )?thigh", ["L2", "L3"], False), (r"lateral thigh|outer thigh", ["L2", "LFCN"], False),
    (r"posterior thigh|back of (the )?thigh|hamstring", ["S2"], False), (r"medial thigh|inner thigh", ["L3"], False),
    (r"\bthighs?\b", ["L2", "L3"], False), (r"\bknees?\b", ["L3", "L4"], False),
    (r"\bcalf\b|\bcalves\b", ["S1", "S2", "sural"], False), (r"\bshins?\b|anterior (lower )?leg|pretibial", ["L4", "L5"], False),
    (r"lateral (lower )?leg|outer (lower )?leg", ["L5", "peroneal"], False), (r"medial (lower )?leg|inner (lower )?leg", ["L4", "saphenous"], False),
    (r"\bankles?\b", FEET, False), (r"\bfeet\b|\bfoot\b", FEET, False), (r"\btoes?\b", ["L5", "S1"], False),
    (r"great toe|big toe|first toe|1st toe|hallux", ["L5"], False), (r"little toe|small toe|fifth toe|5th toe|pinky toe", ["S1"], False),
    (r"\bheels?\b", ["S1"], False), (r"\bsoles?\b|\bplantar\b", ["S1"], False),
    (r"dorsum of (the )?foot|dorsal foot|top of (the )?foot", ["L5", "peroneal"], False),
    (r"lateral (border of (the )?)?foot|outer foot", ["S1", "sural"], False), (r"medial (border of (the )?)?foot|inner foot", ["L4", "saphenous"], False),
    (r"first web ?space|1st web ?space", ["L5", "peroneal"], False),
    (r"\bbuttocks?\b|\bgluteal\b|\bglutes\b", ["S2"], False),
    (r"\bsaddle\b|\bperine\w*|peri-?anal|\bgenital\w*|\brectal\b|\bscrotal\b|\blabial\b|\bpenile\b", ["S345"], False),
    (r"\bsciatica?\b", ["L5", "S1", "S2"], False),
    (r"\bears?\b|\botalgia\b|\baural\b|\bauricular\b|\bcochlea\w*", ["ear"], False),
]]
# roots are written in capitals ("l5" is not a root); the words around them are not
RANGE = re.compile(r"\b([CTLS])\s?-?(\d{1,2})\s?(?:-|–|—|/|(?i:to|through|thru))\s?(?:([CTLS])\s?-?)?(\d{1,2})\b")
SINGLE = re.compile(r"\b([CTLS])\s?-?(\d{1,2})\b")
SIDE = re.compile(r"\b(?:((?i:left|lt\.?|sinistra)|L(?!-?spine))|((?i:right|rt\.?|dextra)|R(?![/-]))|((?i:bilateral\w*|b/l|bilat\.?|both)|AU))(?:(?i:-?sided))?\b")
EAR_SIDE = re.compile(r"\b(AD|AS)\b")        # auris dextra / sinistra (upper case only)
WORD = re.compile(r"\S+")


class _Token:
    __slots__ = ("kind", "start", "end", "text", "bases", "side", "both", "explicit")

    def __init__(self, kind, start, end, text, bases=(), side="", both=False, explicit=False):
        self.kind, self.start, self.end, self.text = kind, start, end, text
        self.bases, self.side, self.both, self.explicit = list(bases), side, both, explicit


def _clauses(text: str) -> list[str]:
    """Sentences and semicolon / newline parts; a comma before a negation also starts a new clause."""
    t = str(text or "").replace("\r", "\n")
    t = re.sub(r",\s*(?=(?:no|not|denies|without|negative)\b)", ". ", t, flags=re.I)
    parts = re.split(r"(?<=[.;!?])\s+|\n+|;|\s+[-–—]\s+", t)
    return [p.strip() for p in parts if p and p.strip()]


def _scan(clause: str) -> list[_Token]:
    tokens: list[_Token] = []
    i, n = 0, len(clause)
    while i < n:
        if not clause[i].isalnum():
            i += 1
            continue
        if i and clause[i - 1].isalnum():          # inside a word
            i += 1
            continue
        best: _Token | None = None
        m = RANGE.match(clause, i)
        if m:
            a, b = _spine_base(m.group(1), int(m.group(2))), _spine_base(m.group(3) or m.group(1), int(m.group(4)))
            if a and b:
                if STRUCTURAL.match(clause, m.end()):      # "L4-5 disc": a level, not a distribution
                    i = m.end()
                    continue
                best = _Token("spine", m.start(), m.end(), m.group(0), _spine_range(a, b), explicit=True)
        if best is None:
            m = SINGLE.match(clause, i)
            if m:
                base = _spine_base(m.group(1), int(m.group(2)))
                if base:
                    if STRUCTURAL.match(clause, m.end()):
                        i = m.end()
                        continue
                    best = _Token("spine", m.start(), m.end(), m.group(0), [base], explicit=True)
        for rx, bases in EXPLICIT:
            m = rx.match(clause, i)
            if m and (best is None or m.end() > best.end):
                best = _Token("explicit", m.start(), m.end(), m.group(0), bases, explicit=True)
        for rx, bases, both in PARTS:
            m = rx.match(clause, i)
            if m and (best is None or m.end() > best.end):
                best = _Token("part", m.start(), m.end(), m.group(0), bases, both=both or bool(PLURAL_BOTH.search(m.group(0))))
        m = EAR_SIDE.match(clause, i)
        if m and (best is None or m.end() > best.end):
            best = _Token("earside", m.start(), m.end(), m.group(0), ["ear"], side="R" if m.group(1) == "AD" else "L")
        m = SIDE.match(clause, i)
        if m and (best is None or m.end() > best.end):
            before, after = clause[max(0, m.start() - 28):m.start()].rstrip(), clause[m.end():m.end() + 18]
            if not (DIRECTION.search(before) or DIRECTION.match(after.lstrip())):
                side = "L" if m.group(1) else ("R" if m.group(2) else "B")
                best = _Token("side", m.start(), m.end(), m.group(0), side=side)
        if best is None:
            w = WORD.match(clause, i)
            i = w.end() if w else i + 1
            continue
        tokens.append(best)
        i = best.end
    return tokens


def _categories(clause: str) -> list[str]:
    found = [c for c in CATEGORIES if CUE_RX[c].search(clause)]
    if "headache" in found and "pain" in found:
        found.remove("pain")
    return found


def _negated(clause: str, first_token_start: int) -> bool:
    if NEGATION_BEFORE.search(clause[:first_token_start]):
        return True
    return bool(NORMAL_WORD.search(clause)) and not ABNORMAL_WORD.search(clause)


def _side_for(tok: _Token, tokens: list[_Token]) -> tuple[list[str], bool]:
    """Sides a region mention gets: the nearest side word (before it, by preference), else both."""
    if tok.kind == "earside":
        return [tok.side], False
    best, dist = None, None
    for s in tokens:
        if s.kind not in ("side", "earside"):
            continue
        if not tok.end:                                   # a region implied by the clause as a whole ("right SNHL")
            d = 0
        else:
            d = (tok.start - s.end) if s.end <= tok.start else (s.start - tok.end) * 2 + 1
        if d < 0:
            continue
        if dist is None or d < dist:
            best, dist = s, d
    if best is None or dist > 60:
        return (["L", "R"], not tok.both)
    if best.side == "B":
        return (["L", "R"], False)
    return ([best.side], False)


def parse_text(text: str, source: str, default_category: str = "", default_regions: list[str] | None = None) -> list[dict]:
    """Marks named in free text. default_category: what a body part means in this field when the clause has no cue
    (the gait form's "Sensory loss" box). default_regions: where a side alone points in this field (CN VII: the face)."""
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for clause in _clauses(text):
        tokens = _scan(clause)
        regions = [t for t in tokens if t.kind in ("spine", "explicit", "part", "earside")]
        # a named root or nerve is the distribution; the body part next to it ("right hand in a C6 distribution") is
        # not, unless the two are separate findings ("numbness in both feet and left L5 radicular pain")
        named = [t for t in regions if t.kind in ("spine", "explicit")]
        if named:
            def near(part):
                for e in named:
                    gap = clause[min(part.end, e.end):max(part.start, e.start)]
                    if len(gap) <= 40 and not re.search(r"\band\b|\bwith\b|\bplus\b|&", gap):
                        return True
                return False
            regions = [t for t in regions if t.kind != "part" or "ear" in t.bases or not near(t)]
        cats = _categories(clause)
        has_side = any(t.kind in ("side", "earside") for t in tokens)
        if not regions and has_side:
            ear = [c for c in cats if c in EAR_CATEGORIES]
            if ear:
                regions = [_Token("part", 0, 0, "", ["ear"], both=False)]
                cats = ear
            elif "headache" in cats:                       # "left-sided headaches": that side of the head
                regions = [_Token("explicit", 0, 0, "", ["V1", "GON"], explicit=True)]
            elif default_regions and (cats or default_category):
                regions = [_Token("explicit", 0, 0, "", default_regions, explicit=True)]
        if not regions:
            continue
        if _negated(clause, min((t.start for t in regions if t.end), default=len(clause))):
            continue
        if any(t.kind == "spine" for t in regions) and not (cats or has_side or DISTRIBUTION_WORD.search(clause)):
            regions = [t for t in regions if t.kind != "spine"]
            if not regions:
                continue
        # "numbness in both feet and left L5 radicular pain": each word for what is wrong goes with the nearest place
        nearest: dict[int, list[str]] = {}
        placed = [t for t in regions if t.end]
        if len(placed) > 1:
            for cat in cats:
                for m in CUE_RX[cat].finditer(clause):
                    tok = min(placed, key=lambda t: min(abs(t.start - m.end()), abs(m.start() - t.end)))
                    nearest.setdefault(id(tok), [])
                    if cat not in nearest[id(tok)]:
                        nearest[id(tok)].append(cat)
        for tok in regions:
            own = nearest.get(id(tok)) or cats
            if "ear" in tok.bases and tok.kind in ("part", "earside"):
                tok_cats = [c for c in own if c in EAR_CATEGORIES] or [c for c in own if c == "pain"]
                if not tok_cats and default_category in EAR_CATEGORIES:
                    tok_cats = [default_category]
            else:
                tok_cats = [c for c in own if c not in EAR_CATEGORIES] or ([default_category] if default_category else [])
                if not tok_cats and tok.explicit:
                    tok_cats = sorted({REGIONS[b]["default"] for b in tok.bases})
            if not tok_cats:
                continue
            sides, unsided = _side_for(tok, tokens)
            for base in tok.bases:
                for side in sides:
                    for cat in tok_cats:
                        key = (region_id(base, side), cat)
                        if key in seen:
                            continue
                        seen.add(key)
                        out.append({"region": key[0], "category": cat, "source": source, "text": clause[:160], "unsided": unsided})
    return out


# --------------------------------------------------------------------------- the provider's own marks

def parse_manual(text: str) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """"L5-L:pain ear-R -C6-R:numbness -T4-L" -> (added, removed); a removal with no category removes every mark there."""
    added: list[tuple[str, str]] = []
    removed: list[tuple[str, str]] = []
    for raw in re.split(r"[\s,;]+", str(text or "").strip()):
        if not raw:
            continue
        minus = raw.startswith("-")
        body = raw[1:] if minus else raw
        region, _, cat = body.partition(":")
        parts = split_id(region)
        if not parts:
            continue
        full = region_id(*parts)
        if cat and cat not in CATEGORIES:
            continue
        if minus:
            if (full, cat) not in removed:
                removed.append((full, cat))
        else:
            cat = cat or REGIONS[parts[0]]["default"]
            if (full, cat) not in added:
                added.append((full, cat))
    return added, removed


def format_manual(added: list[tuple[str, str]], removed: list[tuple[str, str]]) -> str:
    return " ".join([f"{r}:{c}" for r, c in added] + [f"-{r}" + (f":{c}" if c else "") for r, c in removed])


# --------------------------------------------------------------------------- the note

SYMPTOMS = [
    # id, label, present box, denies box, [(text field, default category)], side boxes -> ear category
    ("dizziness", "Dizziness", "dizziness", "dizziness-denies", [("DizzyDesc", "")], None),
    ("hearing", "Hearing loss", "hearingloss", "hearingloss-denies", [("HLdesc", "hearing")], ("hl-", "hearing")),
    ("tinnitus", "Tinnitus", "tinnitus", "tinnitus-denies", [("tinnitus-descr", "tinnitus"), ("t-other-descr", "")], ("t-", "tinnitus")),
    ("gait", "Gait ataxia", "gaitataxia", "gaitataxia-denies", [("gaitataxia-descr", ""), ("ga-sens-descr", "numbness"), ("ga-weak-descr", "weakness"), ("ga-other-descr", "")], None),
    ("headache", "Headache", "headache", "headache-denies", [("headache-descr", "headache")], None),
]
# exam rows whose side alone points somewhere, and what a bare finding there means
EXAM_DEFAULTS = {"findings25": (FACE, "numbness"), "findings26": (FACE, "weakness"), "findings27": (["ear"], "hearing"),
                 "findings31": ([], "weakness"), "findings32": ([], "weakness"), "findings33": ([], "numbness"), "findings34": ([], "numbness")}
EXAM_SKIP = {"findings1", "findings2", "findings6", "findings7", "findings8", "findings9", "findings10", "findings11", "findings12", "findings13",
             "findings14", "findings15", "findings17-20", "findings21", "findings22", "findings24", "findings36", "findings37", "findings38", "findings39"}


def _text(v: dict, name: str) -> str:
    return str(v.get(name, "") or "").replace("\r", "\n")


def _side_marks(v: dict, prefix: str, category: str, source: str) -> list[dict]:
    sides = []
    if v.get(prefix + "bilat"):
        sides = ["L", "R"]
    else:
        if v.get(prefix + "right"):
            sides.append("R")
        if v.get(prefix + "left"):
            sides.append("L")
    return [{"region": region_id("ear", s), "category": category, "source": source, "text": f"{source}: {'bilateral' if len(sides) == 2 else SIDE_WORD[s]}", "unsided": False} for s in sides]


def _headache_marks(v: dict) -> list[dict]:
    sides = [s for s, k in (("R", "h-right"), ("L", "h-left")) if v.get(k)]
    bases: list[str] = []
    if v.get("h-frontal"):
        bases += ["V1"]
    if v.get("h-occipital"):
        bases += ["GON", "LON"]
    if v.get("h-global"):
        bases += ["V1", "GON"]
        sides = ["L", "R"]
    if not bases and sides:
        bases = ["V1", "GON"]
    if not bases:
        return []
    sides = sides or ["L", "R"]
    where = ", ".join(w for w, k in (("frontal", "h-frontal"), ("occipital", "h-occipital"), ("global", "h-global")) if v.get(k)) or "unilateral"
    return [{"region": region_id(b, s), "category": "headache", "source": "Headache", "text": f"Headache: {where}", "unsided": False}
            for b in dict.fromkeys(bases) for s in sides]


def _oculography_marks(v: dict) -> list[dict]:
    out = []
    for name, spec in note.FIELDS.items():
        if not name.startswith("imp-") or name in ("imp-mp", "imp-normal", "imp-atyp") or "nyst" in name or not v.get(name):
            continue
        side = "R" if "right" in name else ("L" if "left" in name else "")
        if side:
            out.append({"region": region_id("ear", side), "category": "vertigo", "source": "IR oculography", "text": spec["label"], "unsided": False})
    return out


def _dedupe(marks: list[dict]) -> list[dict]:
    seen, out = set(), []
    for m in marks:
        key = (m["region"], m["category"])
        if key not in seen:
            seen.add(key)
            out.append(m)
    return out


def from_values(values: dict) -> dict:
    """Every complaint on the note with the regions it points at, the regions rolled up, and the provider's own marks."""
    v = values or {}
    complaints: list[dict] = []
    denied: list[str] = []

    cc = str(v.get("ChiefComplaint", "") or "").strip()
    hpi = _text(v, "ChiefComplaint-descr")
    marks = parse_text(cc, "Chief complaint") + parse_text(hpi, "HPI")
    if cc or hpi.strip():
        complaints.append({"id": "cc", "kind": "cc", "label": cc or "Chief complaint", "detail": "", "marks": _dedupe(marks)})

    for sid, title, on, deny, texts, sides in SYMPTOMS:
        present = bool(v.get(on))
        marks = []
        if sides:
            marks += _side_marks(v, sides[0], sides[1], title)
        if sid == "headache":
            marks += _headache_marks(v)
        if sid == "dizziness":
            for key, side in (("assoc-ad", "R"), ("assoc-as", "L")):
                if v.get(key) or v.get("assoc-au"):
                    marks.append({"region": region_id("ear", side), "category": "vertigo", "source": "Dizziness", "text": "Aural symptoms " + ("AU" if v.get("assoc-au") else key[-2:].upper()), "unsided": False})
        for field_name, default in texts:
            marks += parse_text(_text(v, field_name), title, default_category=default)
        if present or marks:
            complaints.append({"id": "sym-" + sid, "kind": "symptom", "label": title, "detail": "" if present else "not marked present", "marks": _dedupe(marks)})
        elif v.get(deny):
            denied.append(title)

    for imp in note.impressions(_text(v, "impression-plan-descr")):
        marks = parse_text(imp.diagnosis, f"Impression {imp.number}") + parse_text(imp.plan, f"Impression {imp.number} plan")
        complaints.append({"id": f"dx{imp.number}", "kind": "impression", "label": f"{imp.number}) {imp.diagnosis}", "detail": imp.plan, "marks": _dedupe(marks)})

    exam_marks = []
    for row in note.SCHEMA.get("exam_rows", []):
        fname = row.get("findings")
        if not fname or fname in EXAM_SKIP:
            continue
        text = _text(v, fname).strip()
        abnormal = bool(str(v.get(row.get("abnormal") or "", "") or "").strip())
        if not text or (not abnormal and v.get(row.get("normal") or "", False)):
            continue
        regions, default = EXAM_DEFAULTS.get(fname, ([], ""))
        where = row["label"] if row.get("group") == "Cranial nerves" else row.get("group", "")
        exam_marks += parse_text(text, "Exam: " + where, default_category=default, default_regions=regions)
    exam_marks += _oculography_marks(v)
    if exam_marks:
        complaints.append({"id": "exam", "kind": "exam", "label": "Exam findings", "detail": "", "marks": _dedupe(exam_marks)})

    study_marks = []
    for i in range(1, 6):
        name = str(v.get(f"x-study{i}", "") or "").strip()
        if name:
            study_marks += parse_text(_text(v, f"x-study{i}-result"), name)
    if study_marks:
        complaints.append({"id": "studies", "kind": "study", "label": "Studies", "detail": "", "marks": _dedupe(study_marks)})

    manual_text = str(v.get("distributions", "") or "")
    added, removed = parse_manual(manual_text)
    if added:
        complaints.append({"id": "manual", "kind": "manual", "label": "Marked on the map", "detail": "",
                           "marks": [{"region": r, "category": c, "source": "Provider", "text": "Marked on the map", "unsided": False} for r, c in added]})

    removed_all = {r for r, c in removed if not c}
    removed_one = {(r, c) for r, c in removed if c}
    index: dict[str, dict] = {}
    for c in complaints:
        for m in c["marks"]:
            gone = m["region"] in removed_all or (m["region"], m["category"]) in removed_one
            m["removed"] = gone
            if gone:
                continue
            slot = index.setdefault(m["region"], {"id": m["region"], "label": label(m["region"]), "categories": [], "sources": [], "complaints": [], "unsided": False})
            if m["category"] not in slot["categories"]:
                slot["categories"].append(m["category"])
            if m["source"] not in slot["sources"]:
                slot["sources"].append(m["source"])
            if c["id"] not in slot["complaints"]:
                slot["complaints"].append(c["id"])
            slot["unsided"] = slot["unsided"] or bool(m.get("unsided"))
    order = {b: i for i, b in enumerate(REGIONS)}
    regions = sorted(index.values(), key=lambda r: (order.get(split_id(r["id"])[0], 99), split_id(r["id"])[1]))
    return {"complaints": complaints, "denied": denied, "regions": regions, "manual": manual_text.strip(),
            "removed": [{"region": r, "category": c} for r, c in removed]}


# --------------------------------------------------------------------------- the sidecar

def sidecar(pdf_path: str) -> str:
    """Where a filed note keeps marks the form has no box for."""
    root = pdf_path[:-4] if pdf_path.lower().endswith(".pdf") else pdf_path
    return root + ".snapshot.json"


def save_sidecar(pdf_path: str, values: dict) -> None:
    text = str((values or {}).get("distributions", "") or "").strip()
    path = sidecar(pdf_path)
    try:
        if text:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"distributions": text}, f)
        elif os.path.isfile(path):
            os.remove(path)
    except OSError:
        pass


def read_sidecar(pdf_path: str) -> dict:
    path = sidecar(pdf_path)
    if not pdf_path or not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    text = str(data.get("distributions", "") or "") if isinstance(data, dict) else ""
    return {"distributions": text} if text.strip() else {}
