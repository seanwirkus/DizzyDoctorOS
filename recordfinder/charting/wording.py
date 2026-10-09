"""Rough notes and dictation in the clinic's own wording.

clinical() rewrites what a provider typed between rooms or said into the microphone the way this clinic's notes
read: "Patient says she has been dizzy for about two months, maybe hearing loss" becomes "Pt reports she has been dizzy
for ~2 months, possible hearing loss." It is a fixed list of word rules (no language model, nothing leaves the
computer), so the same words always come out the same way and running it twice changes nothing.

Numbers said in full are written in digits where the meaning is plain: a dose ("twelve point five milligrams as
needed" -> "12.5 mg prn"), a year, a blood pressure ("one twenty eight over seventy six" -> "128/76").

It may never change what the note says. Negations (no, not, denies, without, never), hedges (maybe -> possible /
possibly, not sure -> uncertain), numbers, units, sides and drug names come through every rule: each sentence is
checked after it is rewritten, and one that lost or gained any of them is kept in the provider's own words, only
tidied. A sentence that trails off ("Symptoms last seconds to...") ends in *** so the provider sees it still needs
finishing.
"""
from __future__ import annotations

import re
from collections import Counter

BLANK = "***"

# ---------------------------------------------------------------------------------------------------- numbers

SMALL = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
         "seventeen eighteen nineteen").split()
TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
NUM_WORD = (r"(?:(?:twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)(?:[- ](?:one|two|three|four|five|six|seven|"
            r"eight|nine)\b)?|" + "|".join(sorted(SMALL, key=len, reverse=True)) + r")")
NUM_ANY = rf"(?:\d+(?:\.\d+)?|{NUM_WORD})"
COUNT_UNIT = (r"(?:seconds?|secs?|minutes?|mins?|hours?|hrs?|days?|nights?|weeks?|wks?|months?|mos?|years?|yrs?|times|"
              r"episodes?|spells?|attacks?|falls?|cups?|drinks?|beers?|glasses?|cigarettes?|packs?|pills?|tablets?|"
              r"pounds?|lbs?)")
MONTHS = ("january february march april may june july august september october november december "
          "jan feb mar apr jun jul aug sep sept oct nov dec").split()

# Numbers said in full: "twelve point five milligrams", "three hundred mg", "March of twenty twenty three", "blood
# pressure one twenty eight over seventy six". A run of number words is read as one number where that is plain.
_NUM_TOKEN = (r"(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|"
              r"eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|point|oh)")
NUMBER_RUN = re.compile(rf"\b{_NUM_TOKEN}(?:(?:[\s-]+|\s+and\s+){_NUM_TOKEN})*\b", re.I)
DOSE_UNIT = re.compile(r"\s*(?:mg|milligrams?|mcg|micrograms?|units?|percent|%|ml|milliliters?|grams?|g\b|meq|iu)\b", re.I)
YEAR_LEAD = re.compile(rf"(?:\b(?:{'|'.join(MONTHS)})\.?(?:\s+of)?|\b(?:in|since|by|until|of|from))\s+$", re.I)
BP_LEAD = re.compile(r"\b(?:blood pressure|bp|b\.p\.)\s+(?:is\s+|was\s+|of\s+|today\s+|reading\s+)*$", re.I)

COUNT_RE = re.compile(rf"(?<![\w-])(?P<nums>{NUM_ANY}(?:\s*(?:-|to|or|,)\s*{NUM_ANY})*)(?=\s+{COUNT_UNIT}\b)", re.I)
# "one" alone is only a count after a duration word ("for one day"); "One day she fell" is a story.
ONE_CONTEXT = re.compile(r"(?:\b(?:for|about|around|approximately|roughly|in|over|within|past|last|than|after|every|each|x)|~)\s*$", re.I)
_APPROX_AHEAD = rf"(?=\d+(?:\.\d+)?\+?(?:\s*(?:-|to|or)\s*\d+(?:\.\d+)?)?\+?\s*(?:{COUNT_UNIT}\b|x\b|x/|\d*x/|%|percent\b))"
APPROX_RE = re.compile(rf"\b(?:about|around|approximately|approx\.?|roughly|like)\s+{_APPROX_AHEAD}", re.I)
# "concerned about 3 falls", "would like 2 weeks", "felt like 10 minutes": the word belongs to the verb, it isn't "~".
NOT_APPROX_AFTER = re.compile(r"\b(?:would|'d|should|could|felt|feels?|feeling|looks?|looked|seems?|seemed|sounds?|sounded|concerned|worried|"
                              r"worry|worries|asks?|asked|asking|talked|talks?|talking|called|calls?|thinking|thinks?|thought|know|knew|care|"
                              r"cares|cared|unsure|sure|forgot|forget|told|tell|tells|spoke|speak|learned|read|heard|hear|wrote|complained|"
                              r"complains)\s+$", re.I)
APPROX_ONE_RE = re.compile(r"\b(?:about|around|approximately|roughly)\s+an?\s+(?P<u>second|minute|hour|day|night|week|month|year)\s+or\s+(?:two|2)\b", re.I)
APPROX_A_RE = re.compile(r"\b(?:about|around|approximately|approx\.?|roughly)\s+an?\s+(?=(?:second|minute|hour|day|night|week|month|year)\b)", re.I)
FREQ_RE = re.compile(r"(?P<n>\d+(?:\s*(?:-|to|or)\s*\d+)?)\s*(?:x|times)\s*(?:a|an|per|each|every|/)\s*(?P<u>day|night|week|month|year)\b", re.I)
FREQ_ONCE_RE = re.compile(r"\b(?P<w>once|twice)\s+(?:a|an|per|each|every)\s+(?P<u>day|night|week|month|year)\b", re.I)
FREQ_LY_RE = re.compile(r"(?P<n>\d+(?:\s*(?:-|to|or)\s*\d+)?)\s*(?:x|times)\s+(?P<u>daily|nightly|weekly|monthly|yearly)\b", re.I)
FREQ_UNIT = {"daily": "day", "nightly": "night", "weekly": "week", "monthly": "month", "yearly": "year"}

# ---------------------------------------------------------------------------------------------------- rules

# Spoken filler. "like", "just" and "really" only where they are plainly filler ("like, um", "just feels").
LEAD_FILLER_RE = re.compile(r"^(?:(?:so|okay|ok|alright|well|and so|um+|uh+|erm|hmm+)\s*,\s*|(?:um+|uh+|erm|hmm+)\s+|okay so\s+|so basically\s+|"
                            r"(?:so|okay|ok)\s+(?=(?:she|he|they|pt|the patient|patient|this is|we|i|her|his)\b))+", re.I)
# "basically" softens a finding ("basically normal" is "essentially normal"); elsewhere it is filler. "Actually"
# after "no" / "or" / "well" corrects what came before, so it stays.
FILLER_RE = re.compile(r"(?:,\s*)?\b(?:um+|uh+|erm|hmm+)\b,?\s*|(?<!\bdo )(?<!\bdid )(?<!\bif )\byou know\b,?\s*|"
                       r"\b(?:i mean|basically(?!\s+(?:normal|fine|unremarkable|negative|resolved|the same|unchanged|intact|stable))|"
                       r"(?<!\bno )(?<!\bor )(?<!\bwell )(?<!\bno, )actually|literally|honestly|to be honest)\b,?\s*", re.I)
BASICALLY_RE = re.compile(r"\bbasically(?=\s+(?:normal|fine|unremarkable|negative|resolved|the same|unchanged|intact|stable)\b)", re.I)
LIKE_FILLER_RE = re.compile(r"(?:^|(?<=,\s))like,\s*|,\s*like,\s*", re.I)
LIKE_VERB_RE = re.compile(r"\b(?P<v>is|was|are|were|it's)\s+like\s+(?=(?:kind of|sort of)\b)", re.I)      # "is like kind of a rocking"
# filler between two commas inside a verb phrase leaves the phrase whole: "he's had, um, like three falls"
GAP_COMMA_RE = re.compile(r"\b(had|has|have|is|was|are|were|got|gets|takes|took|feels|felt|says|said|reports|the|a|an|of|for|with|about)\s*,\s+", re.I)
JUST_RE = re.compile(r"\bjust\s+(?=(?:feel|feels|felt|kind of|sort of|like)\b)", re.I)
REALLY_RE = re.compile(r"\breally\s+(?=really\b)", re.I)
# "pretty much" softens what follows; it is kept as a softener, not dropped.
PRETTY_MUCH_RE = re.compile(r"\bpretty much\s+(?P<q>all|every|always|constant\w*|daily|the whole|everything|everyday)?(?P<n>(?<=\s)(?=(?:never|no|none|nothing|nobody)\b))?", re.I)

# "5 years old" is an age only when it is the patient's: at the start, after "a" / "is a" / "pt", or before M / F / with
# ("the house is 100 years old" stays as it is).
AGE_RE = re.compile(r"(?:^|(?<=\ban\s)|(?<=\ba\s)|(?<=\bpt\s)|(?<=\bpatient\s)|(?<=,\s))(\d{1,3})\s*-?\s*(?:years?|yrs?)\s*-?\s*old\b|"
                    r"\b(\d{1,3})\s*-?\s*(?:years?|yrs?)\s*-?\s*old\b(?=,?\s+(?:m|f|male|female|man|woman|gentleman|lady|boy|girl|right|left|"
                    r"with|w/|who|presents?|presenting|here|referred|returns?|seen|c/o|complain\w*|is|has|hx|pmh)\b)", re.I)
AGE_YO_RE = re.compile(r"\b(\d{1,3})\s*(?:[yY]\.?[oO]\.?|[yY]/[oO])(?![a-z])")
HAND_RE = re.compile(r"\b(right|left)[- ]?handed\b", re.I)
# "in the left ear" is AS; "behind the left ear", "to the right ear", "out of the left ear", "right ear canal" aren't.
_EAR_NOUN_AFTER = r"(?!\s+(?:canal|canals|drum|drums|wax|tube|tubes|infection|infections|surgery|lobe|pain|pressure equaliz\w*))"
EAR_RE = re.compile(rf"(?<!\bout )\b(?:in|of)\s+(?:the\s+|his\s+|her\s+|my\s+|their\s+|your\s+)?(?P<side>right|left|R|L)\s+ear\b{_EAR_NOUN_AFTER}", re.I)
BOTH_EARS_RE = re.compile(rf"(?:(?<!\bout )\b(?:in|of)\s+(?:the\s+|his\s+|her\s+|my\s+|their\s+|your\s+)?\bboth\s+ears\b|"
                          rf"(?P<noun>\b(?:loss|tinnitus|ringing|fullness|aids|buzzing|roaring|pressure|hissing)\s+)both\s+ears\b){_EAR_NOUN_AFTER}", re.I)
# "ringing in her R ear and L ear": both ears
EARS_PAIR_RE = re.compile(r"(?<!\bout )\b(?:in|of)\s+(?:the\s+|his\s+|her\s+|my\s+|their\s+)?(?:R|right)\s+(?:ear\s+)?(?:and|&)\s+(?:the\s+|his\s+|her\s+|my\s+)?"
                          r"(?:L|left)\s+ears?\b|(?<!\bout )\b(?:in|of)\s+(?:the\s+|his\s+|her\s+|my\s+|their\s+)?(?:L|left)\s+(?:ear\s+)?(?:and|&)\s+"
                          r"(?:the\s+|his\s+|her\s+|my\s+)?(?:R|right)\s+ears?\b", re.I)

UNSURE_BE_RE = re.compile(r"\b(?P<be>is|was|am|are|were)(?:n't|\s+not)\s+(?:too\s+|really\s+|quite\s+|entirely\s+|completely\s+|"
                          r"totally\s+)?(?:sure|certain)(?![-\w])", re.I)
UNSURE_RE = re.compile(r"\bnot\s+(?:too\s+|really\s+|quite\s+|entirely\s+|completely\s+|totally\s+)?(?:sure|certain)(?![-\w])|\bunsure\b", re.I)
MAYBE_RE = re.compile(r"\b(?:maybe|perhaps)\b(?P<gap>\s*)(?P<next>[\w~'’/-]*)", re.I)
# What follows "maybe" when it names a thing ("maybe hearing loss" -> "possible hearing loss"); anything else takes
# "possibly" ("started maybe January", "maybe less than a minute", "mood maybe depressed").
HEDGE_NOUNS = {
    "hearing", "tinnitus", "ringing", "buzzing", "vertigo", "dizziness", "imbalance", "disequilibrium", "migraine", "migraines",
    "headache", "headaches", "ha", "stroke", "tia", "seizure", "seizures", "infection", "infections", "crystals", "ear", "ears",
    "fluid", "wax", "cerumen", "neuropathy", "depression", "anxiety", "stress", "numbness", "tingling", "weakness", "tremor",
    "tremors", "bppv", "concussion", "loc", "fall", "falls", "nausea", "vomiting", "fullness", "pressure", "aura", "auras",
    "neuritis", "labyrinthitis", "meniere's", "menieres", "ms", "dehydration", "palpitations", "syncope", "presyncope",
    "fracture", "arthritis", "cataracts", "glaucoma", "diabetes", "hypertension", "htn", "dm", "afib", "osa", "apnea",
}
MONTH_SET = set(MONTHS) | {"spring", "summer", "fall", "autumn", "winter", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday",
                           "sunday", "today", "yesterday", "tonight", "tomorrow", "last", "next", "this", "once", "twice"}
# After a verb, "maybe" is "possibly" ("took maybe meclizine", "started maybe Tuesday"); so it is when a verb follows
# what it hedges ("maybe hearing aids help").
VERB_BEFORE = re.compile(r"\b(?:started|starts|start|began|begins|took|takes|take|had|has|have|was|is|were|are|fell|falls|lasted|lasts|last|"
                         r"happened|happens|occurs|occurred|gets|got|felt|feels|tried|tries|used|uses|stopped|came|comes)\s+$", re.I)
VERB_AFTER = re.compile(r"^(?:\s+[\w'-]+){0,2}?\s+(?:help|helps|helped|is|are|was|were|has|have|had|does|do|did|makes|made|gets|got|feels|felt|"
                        r"seems|seemed|works|worked|caused|causes|started|starts|began|comes|came|goes|went)\b", re.I)

WORD_RULES = [   # (pattern, replacement); a replacement keeps a capital first letter where the match had one
    (re.compile(r"\bcomplain(?:s|ed|ing)?\s+of\b", re.I), "c/o"),
    (re.compile(r"\bpast medical history\b", re.I), "PMHx"),
    (re.compile(r"\b(?:history of|h/o)\b", re.I), "hx of"),
    (re.compile(r"\bfamily history\b", re.I), "family hx"),
    (re.compile(r"\bsocial history\b", re.I), "social hx"),
    (re.compile(r"\bsymptoms\b", re.I), "sxs"),
    (re.compile(r"\bsymptom\b", re.I), "sx"),
    (re.compile(r"\b(?:the\s+)?patient's\b", re.I), "pt's"),
    (re.compile(r"\b(?:the\s+)?patient\b", re.I), "pt"),
    (re.compile(r"\bblood pressure\b", re.I), "BP"),
    (re.compile(r"\bloss of consciousness\b", re.I), "LOC"),
]
KEEP_UPPER = {"PMHx", "BP", "LOC"}
# A dose said in full, in the way it is written: "12.5 milligrams as needed" -> "12.5 mg prn".
UNIT_RULES = [
    (re.compile(r"(?<=\d)\s*milligrams?\b", re.I), " mg"),
    (re.compile(r"(?<=\d)\s*micrograms?\b", re.I), " mcg"),
    (re.compile(r"(?<=\d)\s*percent\b", re.I), "%"),
    (re.compile(r"\b(?P<u>mg|mcg|ml|units?|tablets?|pills?)\s+as needed\b", re.I), r"\g<u> prn"),
]
# Reported speech: "Patient says ..." -> "Pt reports ...", "... but says ..." -> "... but reports ...".
_SAY = r"(?:says|states|mentions|tells me|said|stated|mentioned|told me)"
_ADV = r"(?:(?:also|now|later|then|still)\s+)?"
_THAT = r"(?:\s+that(?![\w']))?"
SAY_PT_RE = re.compile(rf"\bpt\s+(?P<adv>{_ADV})(?P<v>{_SAY}){_THAT}\b(?!\s+(?:to\s+\w+|no\s+to|yes\s+to)\b)", re.I)
SAY_START_RE = re.compile(rf"^(?P<adv>{_ADV})(?P<v>{_SAY}){_THAT}\b(?!\s+(?:to\s+\w+|no\s+to|yes\s+to)\b)", re.I)
# "told me" is a report only from the patient or family ("she told me ..."), not a clinician ("PCP told me to ...")
TOLD_ME_BY = re.compile(r"\b(?:pt|she|he|they|patient|wife|husband|daughter|son|mother|father|mom|dad|family|caregiver)\s+(?:also\s+|later\s+)?$", re.I)
# "PCP told me to stop the meclizine", "ENT said to use Flonase", "he said no to VRT": an instruction or a decision,
# not a report; left as said.
_NOT_REPORT = r"(?!\s+(?:to\s+\w+|no\s+to|yes\s+to|ok(?:ay)?\s+to)\b)"
SAY_RE = re.compile(rf"(?<!\bunited )(?<!\bmental )\b(?P<v>{_SAY}){_THAT}\b{_NOT_REPORT}", re.I)
WITHOUT_RE = re.compile(r"(?<![\w/])without\b")
WITH_RE = re.compile(r"(?<![\w/])with\b")

APOSTROPHE_RE = re.compile(r"\b(could|would|should|was|were|does|did|do|ca|is|are|has|have|had|wo)nt\b|\b(that|she|what|there)s\b", re.I)
SPLIT_RE = re.compile(r"(?<=[.!?])(?<![\s,(]\?)\s+(?=\S)|(?<=\s\*\*\*)\s+(?=\S)")
ABBREVIATIONS = {"mr", "ms", "mrs", "dr", "st", "vs", "approx", "etc", "jr", "sr", "e.g", "i.e", "cf", "fig", "mr./ms"}
CUT_END_RE = re.compile(r"\s*(?:\.\.\.|…|(?<!\*)\*\*\*)\s*$")
DANGLING_RE = re.compile(r"\b(?:when|about|to|and|or|but|with|w/|for|of|the|a|an|because|since|until|after|before|than|like|which|"
                         r"that|who|from|at|in|if|then|so|very|more|less)\s*$", re.I)
# "maybe more", "or less", "nauseous since": the thought is complete
COMPLETE_END_RE = re.compile(r"\b(?:maybe|possibly|probably|perhaps|or|no|much|even|any|a bit|a little|little|bit|and so)\s+(?:more|less|so|then)\s*$|"
                             r"\b(?:has|have|had|was|were|is|been|never|ever|not)\b[^,;.]*\s(?:since|before|after)\s*$", re.I)

# ---------------------------------------------------------------------------------------------------- the check

# "n't", and the same typed without the apostrophe ("doesnt", "wasnt")
_NT = "(?:n't|" + "|".join(f"(?<=\\b{w})nt" for w in ("could", "would", "should", "does", "were", "have", "was", "did", "are", "has",
                                                      "had", "is", "do", "ca", "wo")) + ")"
NEGATION_RE = re.compile(rf"\b(?:no|not|never|none|nor|nothing|denies|denied|deny|denying|without|negative|neg|cannot)\b|w/o|{_NT}\b", re.I)
NOT_SURE_RE = re.compile(rf"(?:\bnot|{_NT})\s+(?:too\s+|really\s+|quite\s+|entirely\s+|completely\s+|totally\s+)?(?:sure|certain)(?![-\w])", re.I)
HEDGE_RE = re.compile(rf"\b(?:maybe|perhaps|possibly|possible|probably|might|may|could|likely|unsure|uncertain|unclear|questionabl[ey]|"
                      rf"seems?|thinks?|sort of|kind of)\b|\?|(?:\bnot|{_NT})\s+(?:too\s+|really\s+|quite\s+|entirely\s+|completely\s+|"
                      r"totally\s+)?(?:sure|certain)(?![-\w])", re.I)
SIDE_WORD_RE = re.compile(r"\b(?:right|left|both|bilateral(?:ly)?|bilat)\b", re.I)
SIDE_ABBR_RE = re.compile(r"\b(?:AD|AS|AU|RHD|LHD|R|L)\b")
NOT_SIDE_RE = re.compile(r"\ball right\b|\bright (?:now|away|after|before|then|here)\b|\bleft (?:over|alone|out|behind|work|the)\b", re.I)
UNIT_RE = re.compile(r"\b(?:seconds?|secs?|minutes?|mins?|hours?|hrs?|days?|daily|nights?|nightly|weeks?|wks?|weekly|months?|mos?|"
                     r"monthly|years?|yrs?|yearly)\b", re.I)
AGE_ANY_RE = re.compile(r"\b\d{1,3}\s*(?:-?\s*(?:years?|yrs?)\s*-?\s*old|y/o|yo|y\.o\.?)", re.I)
_drug_words: set[str] | None = None


def _drugs() -> set[str]:
    global _drug_words
    if _drug_words is None:
        try:
            from . import medlist
            _drug_words = {n.lower() for n in medlist.all_names() if len(n) >= 4 and " " not in n}
        except Exception:  # noqa: BLE001 - the check still runs on everything else without the drug list
            _drug_words = set()
    return _drug_words


def _cardinal(words: list[str]) -> str | None:
    """["three", "hundred"] -> "300", ["twelve", "point", "five"] -> "12.5"; None when the words aren't one number."""
    if "point" in words:
        i = words.index("point")
        whole, frac = _cardinal(words[:i]) if i else "0", [word_number(w) for w in words[i + 1:]]
        if whole is None or not frac or any(f is None or f > 9 for f in frac):
            return None
        return whole + "." + "".join(str(f) for f in frac)
    total, cur, last = 0, 0, ""
    for w in words:
        if w == "hundred":
            if last in ("", "hundred", "thousand"):
                return None
            cur = (cur or 1) * 100
        elif w == "thousand":
            total, cur = total + (cur or 1) * 1000, 0
        else:
            v = word_number(w)
            if v is None:
                return None
            # "twenty three" is one number; "twenty twenty" or "one forty" isn't
            if last and last not in ("hundred", "thousand") and not (last in TENS and v < 10):
                return None
            cur += v
        last = w
    return str(total + cur)


def _paired(words: list[str]) -> str | None:
    """The way years and blood pressures are said: "nineteen eighty five" -> 1985, "one twenty eight" -> 128."""
    for i in range(1, len(words)):
        a, b = _cardinal(words[:i]), _cardinal(words[i:])
        if a is not None and b is not None and int(a) < 100 and (10 <= int(b) < 100 or words[i] == "oh"):
            return str(int(a) * 100 + int(b))
    return None


def spoken_numbers(s: str, everywhere: bool = False) -> str:
    """Number words to digits where the meaning is plain: before a dose ("twelve point five milligrams" -> "12.5
    milligrams"), a decimal ("seven point two"), a year after a month or "in" ("twenty twenty three"), a blood pressure
    ("one twenty eight over seventy six" -> "128/76"). With `everywhere`, every run that reads as one number (for
    dictation's own reading, not for the note's words)."""
    def conv(m: re.Match) -> str:
        words = [w for w in re.split(r"[\s-]+", m.group(0).lower()) if w and w != "and"]
        words = ["zero" if w == "oh" and i else w for i, w in enumerate(words)]
        if words and words[0] in ("point", "hundred", "thousand", "oh"):
            return m.group(0)
        before, after = s[: m.start()], s[m.end():]
        if YEAR_LEAD.search(before):
            year = _paired(words)
            if year and 1900 <= int(year) < 2100:
                return year
        if BP_LEAD.search(before) or re.match(r"\s+over\s+", after, re.I) or re.search(r"\bover\s+$", before, re.I):
            bp = _cardinal(words) or _paired(words)
            if bp is not None:
                return bp
        n = _cardinal(words)
        if n is None:
            return m.group(0)
        if "point" in words or DOSE_UNIT.match(after) or everywhere or "hundred" in words or "thousand" in words:
            return n
        return m.group(0)
    s = NUMBER_RUN.sub(conv, s)
    return re.sub(r"\b(\d{2,3})\s+over\s+(\d{2,3})\b", r"\1/\2", s) if re.search(r"\b(?:blood pressure|bp)\b", s, re.I) else s


def word_number(word: str) -> int | None:
    """"seventy-three" -> 73; None for anything that isn't a number word."""
    total = 0
    for part in re.split(r"[- ]+", (word or "").lower().strip()):
        if part in TENS:
            total += TENS[part]
        elif part in SMALL:
            total += SMALL.index(part)
        elif part.isdigit():
            total += int(part)
        else:
            return None
    return total


def _numbers(text: str) -> set[str]:
    t = spoken_numbers(text)
    t = re.sub(rf"\b{NUM_WORD}\b", lambda m: str(word_number(m.group(0))), t, flags=re.I)
    t = re.sub(r"\bonce\b", "1", t, flags=re.I)
    t = re.sub(r"\btwice\b", "2", t, flags=re.I)
    return set(re.findall(r"\d+(?:\.\d+)?", t))


def _sides(text: str) -> Counter:
    """Which sides the text names; "both" counts as right and left, so "R ear and L ear" -> "AU" says the same."""
    t = NOT_SIDE_RE.sub(" ", text)
    out: Counter = Counter()
    for m in SIDE_WORD_RE.finditer(t):
        w = m.group(0).lower()
        for side in (("right", "left") if w.startswith(("both", "bilat")) else (w,)):
            out[side] += 1
    for m in SIDE_ABBR_RE.finditer(t):
        w = m.group(0)
        for side in ({"AD": ("right",), "RHD": ("hand-r",), "R": ("right",), "AS": ("left",), "LHD": ("hand-l",), "L": ("left",)}.get(w, ("right", "left"))):
            out[side] += 1
    for m in re.finditer(r"\b(right|left)[- ]?handed\b", t, re.I):     # handedness is its own fact
        out[m.group(1).lower()] -= 1
        out["hand-" + m.group(1)[0].lower()] += 1
    return +out


def _units(text: str) -> Counter:
    t = re.sub(rf"\b{NUM_WORD}\b", lambda m: str(word_number(m.group(0))), text, flags=re.I)
    t = AGE_ANY_RE.sub(" ", t)
    out: Counter = Counter()
    for m in UNIT_RE.finditer(t):
        w = {"daily": "day", "nightly": "night", "weekly": "week", "monthly": "month", "yearly": "year"}.get(m.group(0).lower(), m.group(0).lower())
        for key in ("sec", "min", "hour", "hr", "day", "night", "week", "wk", "month", "mo", "year", "yr"):
            if w.startswith(key):
                out[{"hr": "hour", "wk": "week", "mo": "month", "yr": "year"}.get(key, key)] += 1
                break
    return out


def keeps_meaning(before: str, after: str) -> bool:
    """The rewrite says the same thing: every negation, hedge, number, unit, side and drug name is still there."""
    if len(NEGATION_RE.findall(NOT_SURE_RE.sub(" ", before))) != len(NEGATION_RE.findall(NOT_SURE_RE.sub(" ", after))):
        return False
    if len(HEDGE_RE.findall(before)) != len(HEDGE_RE.findall(after)):
        return False
    nb, na = _numbers(before), _numbers(after)
    if not nb <= na or not na <= nb | {"1"}:
        return False
    if _sides(before) != _sides(after) or _units(before) != _units(after):
        return False
    low_after = after.lower()
    return all(w in low_after for w in set(re.findall(r"[a-z][a-z\-]{3,}", before.lower())) & _drugs())


# ---------------------------------------------------------------------------------------------------- rewriting

def _keep_case(match: str, repl: str) -> str:
    if repl in KEEP_UPPER or not match[:1].isupper():
        return repl
    return repl[:1].upper() + repl[1:]


def _capital(s: str) -> str:
    """The first letter up, unless the first word is shorthand that stays lower ("w/", "s/p", "x3", "f/u")."""
    if not s or not s[0].islower():
        return s
    first = s.split(" ", 1)[0]
    if first.lower() in ("w/", "w/o", "s/p", "f/u", "b/l", "h/o") or re.match(r"x\d", first):
        return s
    return s[0].upper() + s[1:]


MONTH_NAME_RE = re.compile(r"\b(january|february|april|june|july|august|september|october|november|december)\b|"
                           r"(?:(?<=\bsince )|(?<=\bin )|(?<=\bon )|(?<=\buntil )|(?<=\bby )|(?<=\bof )|(?<=\bearly )|(?<=\blate )|(?<=\bmid )|"
                           r"(?<=\blast )|(?<=\bthis )|(?<=\bnext )|(?<=\bfrom ))(march|may)\b|\b(march|may)(?=\s+\d)", re.I)
EPONYM_RE = re.compile(r"\bmeniere'?s?\b|\bepley\b|\bdix[- ]hallpike\b|\bsemont\b|\bbrandt[- ]daroff\b|\bromberg\b|\bparkinson'?s\b", re.I)
EPONYMS = {"menieres": "Meniere's", "meniere's": "Meniere's", "meniere": "Meniere", "epley": "Epley", "semont": "Semont", "romberg": "Romberg",
           "parkinson's": "Parkinson's", "parkinsons": "Parkinson's", "parkinson": "Parkinson"}


def _tidy(s: str) -> str:
    """Cosmetic only: spacing, punctuation, apostrophes a quick typist left out, capitals on months and eponyms."""
    s = APOSTROPHE_RE.sub(lambda m: (m.group(1) + "n't") if m.group(1) else (m.group(2) + "'s"), s)
    s = MONTH_NAME_RE.sub(lambda m: m.group(0).capitalize(), s)
    s = EPONYM_RE.sub(lambda m: EPONYMS.get(m.group(0).lower(), "-".join(w.capitalize() for w in re.split(r"[- ]", m.group(0)))
                                            if re.search(r"[- ]", m.group(0)) else m.group(0)), s)
    s = re.sub(r"\bi(?=\s|'m\b|'ve\b|'d\b|'ll\b)", "I", s)
    s = re.sub(r"(?<=\s)w(?=\s+[A-Za-z])", "w/", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"\s+([,;:])", r"\1", s)
    s = re.sub(r",(?:\s*,)+", ",", s)
    s = re.sub(r"([,;])(?=[A-Za-z(])", r"\1 ", s)
    s = re.sub(r"\(\s+", "(", s)
    s = re.sub(r"\s+\)", ")", s)
    return _capital(s.strip(" ,;"))


def dose_words(s: str) -> str:
    """A dose or plan as said, written the clinic's way: "400 milligrams" -> "400 mg", "as needed" -> "prn"."""
    s = spoken_numbers(s)
    for rx, repl in UNIT_RULES:
        s = rx.sub(repl, s)
    return s


def _numbers_to_digits(s: str) -> str:
    def conv(m):
        nums = m.group("nums")
        if re.fullmatch(r"one", nums, re.I) and not ONE_CONTEXT.search(s[: m.start()]):
            return nums
        return re.sub(NUM_WORD, lambda w: str(word_number(w.group(0))), nums, flags=re.I)
    return COUNT_RE.sub(conv, s)


def _maybe(m: re.Match) -> str:
    word = m.group("next")
    low = word.lower().strip("'’")
    noun = low in HEDGE_NOUNS or (word[:1].isupper() and low not in MONTH_SET)
    if not word or low in MONTH_SET or re.match(r"[\d~]", word) or (low.endswith(("ly", "ed", "ing")) and low not in HEDGE_NOUNS):
        noun = False
    if VERB_BEFORE.search(m.string[: m.start()]) or VERB_AFTER.match(m.string[m.end():]):
        noun = False
    hedge = "possible" if noun else "possibly"
    if m.group(0)[:1].isupper():
        hedge = hedge.capitalize()
    return hedge + m.group("gap") + word


def _approx(rx: re.Pattern, repl: str, s: str) -> str:
    """"about" / "like" before a number as "~", where it isn't part of the verb before it."""
    return rx.sub(lambda m: m.group(0) if NOT_APPROX_AFTER.search(s[: m.start()]) else repl, s)


def _freq(n: str, unit: str) -> str:
    return re.sub(r"\s*(?:-|to|or)\s*", "-", n) + "x/" + unit.lower()


def rewrite(s: str) -> str:
    """One sentence (no end punctuation) in the clinic's wording. No checking here; see clinical()."""
    s = LEAD_FILLER_RE.sub("", s)
    s = BASICALLY_RE.sub(lambda m: _keep_case(m.group(0), "essentially"), s)
    s = FILLER_RE.sub(lambda m: ", " if "," in m.group(0) else " ", s)      # "two months, um, maybe" keeps its comma
    s = LIKE_FILLER_RE.sub(lambda m: " " if m.group(0).startswith(",") else "", s)      # "she, like, feels" -> "she feels"
    s = LIKE_VERB_RE.sub(lambda m: m.group("v") + " ", s)
    s = GAP_COMMA_RE.sub(lambda m: m.group(1) + " ", s)
    s = JUST_RE.sub("", s)
    s = REALLY_RE.sub("", s)
    s = _tidy(s)
    s = spoken_numbers(s)
    for rx, repl in UNIT_RULES:
        s = rx.sub(repl, s)
    s = _numbers_to_digits(s)
    s = AGE_RE.sub(lambda m: (m.group(1) or m.group(2)) + " y/o", s)
    s = AGE_YO_RE.sub(r"\1 y/o", s)
    s = re.sub(r"y/o(?=[A-Za-z])", "y/o ", s)
    s = HAND_RE.sub(lambda m: m.group(1)[0].upper() + "HD", s)
    s = UNSURE_BE_RE.sub(lambda m: m.group("be") + " uncertain", s)
    s = UNSURE_RE.sub(lambda m: _keep_case(m.group(0), "uncertain"), s)
    s = MAYBE_RE.sub(_maybe, s)
    s = PRETTY_MUCH_RE.sub(lambda m: ("nearly " + m.group("q")) if m.group("q") else ("almost " if m.group("n") is not None else "mostly "), s)
    s = FREQ_RE.sub(lambda m: _freq(m.group("n"), m.group("u")), s)
    s = FREQ_ONCE_RE.sub(lambda m: ("1" if m.group("w").lower() == "once" else "2") + "x/" + m.group("u").lower(), s)
    s = FREQ_LY_RE.sub(lambda m: _freq(m.group("n"), FREQ_UNIT[m.group("u").lower()]), s)
    s = APPROX_ONE_RE.sub(lambda m: "~1-2 " + m.group("u").lower() + "s", s)        # "about a minute or two"
    s = re.sub(r"\ban?\s+(second|minute|hour|day|week|month|year)\s+or\s+(?:two|2)\b", lambda m: "1-2 " + m.group(1).lower() + "s", s, flags=re.I)
    s = _approx(APPROX_A_RE, "~1 ", s)
    s = _approx(APPROX_RE, "~", s)
    s = EARS_PAIR_RE.sub("AU", s)
    s = EAR_RE.sub(lambda m: "AD" if m.group("side").lower().startswith("r") else "AS", s)
    s = BOTH_EARS_RE.sub(lambda m: (m.group("noun") or "") + "AU", s)
    for rx, repl in WORD_RULES:
        s = rx.sub(lambda m, r=repl: _keep_case(m.group(0), r), s)
    s = SAY_PT_RE.sub(lambda m: m.group(0)[:2] + " " + m.group("adv") + ("reported" if m.group("v").lower().endswith(("d", "told me")) else "reports"), s)
    s = SAY_START_RE.sub(lambda m: _keep_case(m.group(0), m.group("adv") + ("reported" if m.group("v").lower().endswith(("d", "told me")) else "reports")), s)
    s = SAY_RE.sub(lambda m: m.group(0) if m.group("v").lower().startswith("tell") or (m.group("v").lower() == "told me" and not TOLD_ME_BY.search(m.string[: m.start()]))
                   else ("reported" if m.group("v").lower().endswith(("d", "told me")) else "reports"), s)
    s = WITHOUT_RE.sub("w/o", s)
    s = WITH_RE.sub("w/", s)
    return _tidy(s)


def _split(line: str) -> list[str]:
    out, start = [], 0
    for m in SPLIT_RE.finditer(line):
        before = line[start:m.start()]
        token = re.search(r"(\S+)$", before)
        word = token.group(1).lower().rstrip(".") if token else ""
        if line[m.start() - 1] == "." and word in ABBREVIATIONS:
            continue
        out.append(before)
        start = m.end()
    out.append(line[start:])
    return [s for s in out if s.strip()]


def _sentence(raw: str, last: bool) -> str:
    s = raw.strip()
    cut = bool(CUT_END_RE.search(s))
    s = CUT_END_RE.sub("", s).rstrip()
    end = ""
    m = re.search(r"[.!?;:,]+$", s)
    if m:
        marks = m.group(0)
        end = "?" if "?" in marks else (":" if marks == ":" else ".")
        s = s[: m.start()].rstrip()
    elif last and not cut and DANGLING_RE.search(s) and not COMPLETE_END_RE.search(s):
        cut = True
    tidy = _tidy(s)
    new = rewrite(tidy)
    if not keeps_meaning(tidy, new):
        new = tidy
    new = new.rstrip(" ,;:-")
    if cut:
        return (new + " " + BLANK) if new else BLANK
    if not new:
        return ""
    return new + (end or ".")


def _paragraph(line: str) -> str:
    line = re.sub(r"\s+", " ", line.replace("…", "...").replace(". . .", "...")).strip()
    line = re.sub(r"\b(\d{1,3})\s*[yY]\.[oO]\.", r"\1 y/o", line)      # before splitting, so "52 y.o. F" stays one sentence
    parts = _split(line)
    done = [_sentence(p, i == len(parts) - 1) for i, p in enumerate(parts)]
    return " ".join(d for d in done if d)


def clinical(text: str) -> str:
    """Rough notes or dictation in the clinic's wording, meaning unchanged. Lines stay lines; a blank line between
    paragraphs stays one blank line."""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n").replace(" ", " ")
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    out: list[str] = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            if out and out[-1]:
                out.append("")
            continue
        para = _paragraph(line)
        if para:
            out.append(para)
    while out and not out[-1]:
        out.pop()
    return "\n".join(out)
