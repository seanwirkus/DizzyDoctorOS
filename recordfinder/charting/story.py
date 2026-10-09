"""Typed or pasted notes into the note's boxes.

The provider doesn't have to dictate: quick notes typed into the story, half-finished sentences, a paragraph pasted
from somewhere else are read the way a careful scribe reads them.

- A symptom is ticked present or denied only where the text says so plainly. Anything hedged ("maybe hearing loss",
  "not sure", "R/O", "? BPPV", "migraine vs Meniere's"), contradicted ("denies weakness but later says grip feels
  weaker") or cut off mid-sentence is not ticked; it is listed for the provider to check. A denial covers what it
  is about: "no vertigo" isn't "denies dizziness", "no hearing aids", "no new headaches", "no dizziness today" and
  "denies headache with the attacks" deny nothing, and two negatives ("never free of the ringing") are listed.
- Details (worse with standing, AS, paroxysmal) are read only inside the part of a sentence about that symptom, so
  "left arm felt heavy" never becomes a left-sided headache and "intermittent dizziness, HA daily" is daily headaches;
  a dose ("ASA 81 mg daily") is never how often a symptom comes.
- What belongs to someone else ("father had tremor") is never the patient's history.
- Something that happened before and is over ("had vertigo years ago that resolved", "used to get migraines") is not
  a current symptom, and a companion the patient contradicts ("husband says she's unsteady but she says she's fine")
  ticks neither side; both are listed to check.
- The onset, description and frequency the text gives for a symptom go into that section's text boxes ("~3 weeks
  ago", "possibly January", "lightheadedness w/ room moving at times", "3"), hedges kept, nothing guessed.
- Medications named and medication changes are listed, for the medication review.

compose.py writes the HPI from the boxes in the practice's sentences and keeps whatever else the text says, in the
clinic's wording (wording.py).
"""
from __future__ import annotations

import functools
import re
from dataclasses import dataclass, field

from . import dictation, wording
from .note import FIELDS

# ---------------------------------------------------------------------------------------------------- vocabulary

# What makes a sentence about one of the form's symptom sections. "present" words say the patient has it; the
# others only put the sentence on that topic (a cane is about gait, a fall is not gait ataxia).
TOPICS = {
    "dizziness": r"dizz\w*|vertig\w*|spinning|spins|(?:room|world|things?)\s+(?:is\s+|was\s+|are\s+)?(?:moving|spinning|spins|spin|moves|move|tilting|tilts|tilt)|"
                 r"light[- ]?headed\w*|woozy|presyncop\w*|rocking|swaying|disequilibrium",
    # "hearing aids" and "hearing test" are things, not hearing loss said; HL / SNHL are the clinic's shorthand
    "hearing": r"hearing|deaf\w*|hard of hearing|can'?t hear|cannot hear|muffled|(?-i:SS?NHL|CHL|HL)",
    "tinnitus": r"tinnitus|ringing|buzzing|roaring|whoosh\w*|hissing|crickets|hears?\s+(?:my|her|his|their|the|a)\s+heartbeat",
    "gait": r"gait|imbalance|unsteady|unsteadiness|off[- ]balance|balance|wobbly|veer\w*|shuffl\w*|legs?\s+(?:feel|feels|felt)\s+(?:weak|unsteady|wobbly)",
    "headache": r"headaches?|\bha\b|head\s*pain|migraines?",
}
# "non-spinning", "non-pulsatile": a word with "non" in front never says the patient has it.
NOT_NON = r"(?<![a-z])(?<!non-)(?<!non )"
# A symptom word that only names something else ("hearing aids", "tinnitus meds", "headache relief", "migraine
# medication") says nothing about whether the patient has the symptom.
MODIFIES = (r"(?!\s+(?:aids?|tests?|testing|tested|evaluations?|evals?|exams?|meds?|medications?|medicines?|pills?|relief|"
            r"treatments?|specialists?|doctors?|clinics?|work-?ups?|logs?|diar(?:y|ies)|protheses?)\b)")
TOPIC_RE = {k: re.compile(rf"{NOT_NON}(?:{v})(?![a-z]){MODIFIES}", re.I) for k, v in TOPICS.items()}
# The dizziness words that are "dizziness" itself; vertigo, spinning, rocking are kinds of it. Denying a kind isn't
# denying dizziness ("no vertigo, more lightheaded").
DIZZ_WORD = re.compile(r"dizz|light[- ]?headed|woozy|presyncop", re.I)
SECTION_HEAD = {"dizziness": "dizziness", "hearing": "hearingloss", "tinnitus": "tinnitus", "gait": "gaitataxia", "headache": "headache"}
SECTION_TITLE = {"dizziness": "Dizziness", "hearing": "Hearing loss", "tinnitus": "Tinnitus", "gait": "Gait", "headache": "Headache"}

# Box groups per section and the prefix their boxes share.
GROUPS = {
    "dizziness": {"assoc": "assoc-", "worse": "worse-", "course": "course-", "occur": "occur-", "dur": "duration-", "better": "better-"},
    "hearing": {"assoc": "hlassoc-", "course": "hlcourse-", "better": "hlbetter-", "worse": "hlworse-", "dur": "hldur-", "side": "hl-"},
    "tinnitus": {"assoc": "t-assoc-", "course": "t-course-", "better": "t-better-", "worse": "t-worse-", "dur": "t-dur-", "side": "t-"},
    "gait": {"assoc": "ga-assoc-", "course": "ga-course-", "better": "ga-better-", "worse": "ga-worse-"},
    "headache": {"assoc": "h-assoc-", "course": "h-course-", "better": "h-better-", "worse": "h-worse-", "dur": "h-dur-", "side": "h-"},
}
# Words for the boxes as people write them (on top of dictation.ALIASES and the printed labels).
WORDS = {
    "vertigo": ["vertigo", "spinning", "spins", "room moving", "room spinning", "room spins", "world spinning", "things spinning",
                "room is spinning", "room moves", "room was spinning"],
    "diseq": ["disequilibrium", "off balance", "off-balance", "unsteady", "unsteadiness", "imbalance", "rocking", "swaying"],
    "cbd": ["chronic baseline disequilibrium", "baseline disequilibrium", "baseline imbalance"],
    "standing": ["standing", "stands up", "standing up", "stand up", "getting up", "gets up", "upright"],
    "transitioning": ["transitioning", "sit to stand", "transitions", "changing position", "changing positions", "position changes"],
    "supine": ["lying down", "lying supine", "lies down", "lie down", "in bed", "rolling over", "rolls over", "roll over", "turning in bed"],
    "yaw": ["turning head", "turning the head", "turns head", "turns her head", "turns his head", "turning her head", "turning his head",
            "head turns", "head turning", "looking side to side", "turning quickly"],
    "pitching": ["looking up", "looking down", "bending over", "bends over", "bending down", "tilting head", "head up", "head down"],
    "walking-in-dark": ["in the dark", "at night", "dark", "low light"],
    "walking": ["walking", "walks"],
    "stress": ["stress", "stressed", "fatigue", "tired", "fatigued"],
    "opto": ["busy visual", "screens", "grocery store", "crowds", "scrolling", "busy places", "visual motion", "store aisles"],
    "highgain": ["quick head movement", "quick head movements", "fast head", "moving head quickly", "head movements"],
    "parox": ["paroxysmal", "episodic", "episodes", "comes and goes", "come and go", "on and off", "off and on", "intermittent", "intermittently", "spells"],
    "chronic": ["chronic", "constant", "constantly", "all the time", "continuous"],      # "daily" alone is how often, not a course
    "const": ["constant", "constantly", "all the time", "continuous"],
    "worsening": ["worsening", "getting worse", "has worsened", "progressively worse", "more frequent", "increasing"],
    "worse": ["worsening", "getting worse", "has worsened", "progressively worse", "more frequent", "increasing"],
    "improving": ["improving", "getting better", "has improved", "less frequent", "improved"],
    "improve": ["improving", "getting better", "has improved", "less frequent", "improved"],
    "stable": ["stable", "unchanged", "no change", "about the same"],
    "seconds": ["seconds", "few seconds", "a second", "under a minute", "less than a minute", "a few seconds", "<1 min", "< 1 min",
                "<1 minute", "< 1 minute", "under 1 min", "less than 1 min", "secs", "sec"], "minutes": ["minutes", "a minute", "mins", "min"], "hours": ["hours", "an hour", "hrs", "hr"], "days": ["days", "a day"],
    "sit": ["sitting down", "sits down", "sit down", "sitting"], "headstill": ["head still", "holding head still", "holds head still", "keeping still", "staying still"],
    "resolves": ["resolves on its own", "resolves", "goes away on its own", "goes away by itself", "resolves on own"],
    "meds": ["medication", "medications", "meds", "meclizine", "ibuprofen", "tylenol", "advil", "excedrin", "pills", "aspirin", "triptan", "sumatriptan", "nurtec"],
    "water": ["hydration", "water", "fluids", "drinking water"],
    "nausea": ["nausea", "nauseous", "nauseated", "queasy", "n/v"], "emesis": ["vomiting", "emesis", "throwing up", "threw up", "vomited", "n/v"],
    "photo": ["photophobia", "light sensitivity", "sensitive to light", "bothered by light", "light bothers"],
    "phono": ["phonophobia", "sound sensitivity", "sensitive to sound", "sensitive to noise", "bothered by noise", "noise bothers"],
    "aura": ["aura", "auras"], "scotomas": ["scotomas", "scotoma", "visual spots", "zigzag", "zig-zag", "flashing lights"],
    "lights": ["bright lights", "lights", "light", "sunlight"], "noise": ["loud noise", "noise", "loud"],      # screens are visual motion
    "diet": ["diet", "dietary", "foods", "food", "chocolate", "red wine", "skipping meals", "missed meals"],
    "pressure": ["pressure", "pressure-like", "squeezing", "tight", "tightness", "band-like"], "sharp": ["sharp", "stabbing"],
    "dull": ["dull", "aching", "achy"], "throb": ["throbbing", "pounding", "pulsating", "pulsing"],
    "global": ["global", "whole head", "all over", "entire head", "holocephalic"], "frontal": ["frontal", "forehead", "front of the head", "behind the eyes", "behind eyes"],
    "occipital": ["occipital", "back of the head", "back of head", "base of the skull"],
    "headache": ["headache", "headaches", "head pain"], "tinnitus": ["tinnitus", "ringing", "buzzing"],
    "hearingloss": ["hearing loss", "decreased hearing", "hard of hearing"], "hearing": ["hearing loss", "decreased hearing"],
    "auralfullness": ["aural fullness", "ear fullness", "fullness in the ear", "fullness", "plugged", "clogged"],
    "aural": ["aural fullness", "ear fullness", "fullness in the ear", "fullness", "plugged", "clogged"],
    "hearingfluc": ["fluctuating hearing", "fluctuation", "fluctuates", "fluctuating"], "fluc": ["fluctuating hearing", "fluctuation", "fluctuates", "fluctuating"],
    # a whooshing sound isn't pulsatile until the heartbeat is said
    "pulsatile": ["pulsatile", "pulsing", "heartbeat", "with my heartbeat", "with her heartbeat", "with his heartbeat", "hear my heartbeat",
                  "hears her heartbeat", "hears his heartbeat"],
    "nonpulsatile": ["non-pulsatile", "nonpulsatile", "non pulsatile", "steady"],
    "hearingaids": ["hearing aids", "hearing aid"], "loudbg": ["background noise", "loud background", "noisy", "restaurants", "crowded rooms"],
    "cane": ["cane"], "walker": ["walker", "rollator"], "chair": ["wheelchair"], "sitrest": ["sitting or resting", "resting", "rest", "sitting"],
    "walk": ["walking", "walks", "walk"], "corners": ["turning corners", "corners", "quick turns", "turning quickly"],
    "orthostasis": ["orthostasis", "orthostatic"], "veerleft": ["veers left", "veering left", "veer to the left", "veers to the left", "drifts left"],
    "veerright": ["veers right", "veering right", "veer to the right", "veers to the right", "drifts right"],
    "bradykinesia": ["bradykinesia", "slow movements", "moving slower", "feels slower", "slowness"],
    "mental": ["confusion", "cognitive decline", "memory decline", "mental status"],
    "weakness": ["leg weakness", "weakness in the legs", "weak legs", "legs feel weak", "legs weak"],
    "sensory": ["numbness in the legs", "numb legs", "legs numb", "leg numbness", "sensory loss"],
}
# The clinic's ear shorthand (AD / AS / AU) and R / L count only in capitals: "as" and "ad" are ordinary words.
SIDES = {"right": "right|(?-i:AD|R(?!/))", "left": "left|(?-i:AS|L(?!/))", "bilat": "both|bilateral|bilaterally|bilat|both ears|both sides|(?-i:AU)"}
SIDE_CONTEXT = r"(?:ear|ears|side|sided|eye|eyes|temple|temples|head|forehead)"
NEAR_WORDS = 4      # a side word counts when it is this close to the symptom or an ear / side / eye word

# Cues that start the part of a sentence a box group reads ("worse with standing", "better lying still").
CUES = {
    "worse": re.compile(r"\b(?:worse|worsens?|worsened|aggravated|exacerbated|triggered|brought on|provoked|precipitated|"
                        r"bothered|especially)\b(?:\s+(?:with|when|by|on|after|if|while|during|upon|w/|in|at))?", re.I),
    "better": re.compile(r"\b(?:better|improves?|improved|relieved|helps?|helped|eases?|eased|alleviated|relief)\b"
                         r"(?:\s+(?:with|when|by|on|after|if|while|from|w/))?", re.I),
    "when": re.compile(r"(?:%s)(?:\W+(?:\w+\W+){0,2}?(?:when|whenever|every time|if|upon|on)\b|\s+(?:w/|with)(?=\s))" % "|".join(v for v in TOPICS.values()), re.I),
    "occur": re.compile(r"\b(?:occurs?|happens?|comes on|starts?|only)\s+(?:when|while|with|during|on)\b", re.I),
    "dur": re.compile(r"\b(?:last(?:s|ed|ing)?|lasting|duration|for\s+(?:a\s+few|several|\d+)\s+(?=seconds|minutes|hours))\b|"
                      r"(?=\b(?:seconds|minutes|hours|days)\s+at\s+a\s+time\b)", re.I),
}
CUE_END = re.compile(r"\b(?:worse|better|helps?|relieved|occurs?|happens?|lasts?|lasting|also|and then|then|associated|with associated)\b|[;,]\s+(?=[a-z])", re.I)

DENIAL = re.compile(r"\b(?:denies|denied|deny|denying|no|not|without|w/o|negative for|negative|neg|never|none|nothing|absent|free of|"
                    r"resolved|don'?t|doesn'?t|haven'?t|hasn'?t|didn'?t|isn'?t|aren'?t|wasn'?t|nope)\b"
                    r"(?!\s+(?:clear|other|current|change|longer\b|sure|only|significant change))|^\s*-(?=\s*(?:[A-Za-z]|$))|\(-\)", re.I)
EXCEPT = re.compile(r"\b(?:except|other than|besides|apart from|aside from|but for)\b", re.I)
NORMAL_WORDS = re.compile(r"\b(?:is|are|seems|seem)?\s*(?:fine|normal|ok|okay|good|intact|unchanged at baseline)\b", re.I)
# The other ear (or side) said to be fine: "L ear normal", "left side spared". Not a side of the symptom.
SIDE_NORMAL = re.compile(r"\b(?:fine|normal|ok|okay|good|unaffected|spared|intact|unremarkable|clear)\b", re.I)
# Two negatives cancel ("never free of the ringing", "not a day goes by without dizziness", "hasn't been without
# tinnitus"): the patient has it, but the reader doesn't tick it either way.
DOUBLE_NEG = re.compile(r"\b(?:not|never|can'?t|cannot|isn'?t|wasn'?t|hasn'?t|haven'?t|doesn'?t|don'?t|didn'?t)\b[^,;.]*?"
                        r"\b(?:without|w/o|free of)\b", re.I)
# A denial of a change, not of the symptom: "no improvement in the dizziness", "no new headaches", "no change in hearing".
STABLE_NEG = re.compile(r"\b(?:no|not|without|denies|never|hasn'?t|has not|haven'?t)\s+(?:been\s+|had\s+)?(?:any\s+|significant\s+|further\s+|much\s+|real\s+)?"
                        r"(?:improvement|change|changes|relief|resolution|help|new|worsening|increase|decrease|better|worse|recurrence|"
                        r"progression|difference)\b(?:\s+(?:in|of|with|from|for|to))?(?:\s+(?:the|her|his|my|their|its))?\s*$", re.I)
# A denial that is about a moment, not the history: "no dizziness today", "no tinnitus right now".
NOT_NOW = re.compile(r"^\s*(?:today|right now|at the moment|at this time|currently|this morning|at present|now)\b", re.I)
# A denial about something narrower than the symptom: "denies headache with them / during the attacks", "no dizziness
# when sitting", "no hearing loss in the right ear". It isn't "denies headache".
SCOPED = re.compile(r"^\s*(?:w/(?=\s)|(?:with|during|between|after|before|when|while|whenever|if|upon)\b|(?:in|on)\s+(?:the|her|his|my)\s+"
                    r"(?:right|left)\b|(?-i:AD|AS|AU)\b)", re.I)
# A negation after the word: "hearing loss AS ruled out by audiogram", "MRI for HL negative".
TRAILING_NEG = re.compile(r"^[^,;]{0,30}?\b(?:ruled out|r/o'?d|excluded|not found|not seen|negative|neg)\b", re.I)
# A verbless "not the X" leans on the clause before it ("sumatriptan helps the headache not the dizziness").
ELLIPTIC_BEFORE = re.compile(r"\S[^,;]*?\s(?:but\s+)?not\s+(?:the|my|her|his|their|for\s+the|with\s+the|w/\s+the)\s+$", re.I)
# A hedge covers the next few words ("maybe hearing loss"); "not sure" covers its whole clause. A differential ("R/O
# vestibular migraine", "? BPPV", "migraine vs Meniere's") is a hedge too.
HEDGE_AHEAD = re.compile(r"\b(?:maybe|possibly|possible|perhaps|probably|might|may\s+(?:be|have)|could\s+be|questionabl[ey]|"
                         r"seems?|seemed|sort of|kind of|likely|thinks?|wonders?|suspects?|r/o|rule[- ]out|ruling out|concern for|"
                         r"rarely|seldom|only once|hardly ever|almost never)\b|\+/-|(?:(?<=[\s,(])|^)\?(?=\s*[A-Za-z])", re.I)
HEDGE_CLAUSE = re.compile(r"\b(?:not sure|unsure|uncertain|unclear|not certain|doesn'?t (?:know|remember|recall)|does not (?:know|remember|recall)|"
                          r"can'?t (?:remember|recall)|cannot (?:remember|recall)|vague|questionable|(?:he|she|they|pt|i) (?:thinks|think|believes|believe|guess))\b|"
                          r"(?<![\s,(])\?|^\?(?!\s*[A-Za-z])", re.I)
VERSUS = re.compile(r"\b(?:vs\.?|versus)(?=\s)", re.I)
# "no headaches usually, well occasionally": a denial that is only mostly true is a hedge, not a denial.
MOSTLY_DENIED = re.compile(r"\b(?:no|not|denies|never)\b(?:\W+\w+){0,3}?\W+(?:usually|generally|typically|mostly)\b|"
                           r"\b(?:usually|generally|typically|mostly)\W+(?:\w+\W+)?(?:no|not|denies|never)\b", re.I)
CONTRAST = re.compile(r"(?:,\s*|\s+)(?=\b(?:but|although|though|however|whereas|yet|while)\b)", re.I)
REPORTED = re.compile(r"\b(?:says?|said|states?|stated|reports?|reported|insists?|admits?|claims?|later|then|now|per)\b", re.I)
AFFIRM_START = re.compile(r",\s*(?=(?:has|have|had|reports?|endorses?|complains?|notes?|feels?|felt|states?|admits?|describes?|"
                          r"also|and has|now has|with|more|mostly|mainly|rather|instead|just)\b|w/\s)", re.I)
# The patient answering a companion in the same sentence: "wife says he's unsteady, he says he's fine".
PATIENT_TURN = re.compile(r"[,;]\s*(?=(?:and\s+)?(?:she|he|pt|patient)\s+(?:says?|said|states?|stated|reports?|reported|denies|denied|"
                          r"disagrees|disputes|thinks?|feels?|insists?)\b)", re.I)
# Subjects that two halves of a sentence can contradict each other about. Vertigo is a kind of dizziness: "denies
# vertigo but endorses lightheadedness" says two different things, not one thing both ways.
SUBJECTS = {"weak": r"weak\w*|heav(?:y|iness)|strength|grip", "numb": r"numb\w*|tingl\w*|paresthes\w*|pins and needles|sensation",
            "fall": r"falls?|fell|fallen|falling", "vision": r"vision|visual|blurr\w*|double", "memory": r"memory|forget\w*|repeat\w*",
            "tremor": r"tremor\w*|shak\w*", "pain": r"pain\w*|ache\w*|hurts?",
            "vertigo": r"vertig\w*|spinning|spins|(?:room|world|things?)\s+(?:is\s+|was\s+)?(?:moving|spinning|spins|spin)"}
SUBJECT_RE = {k: re.compile(rf"(?<![a-z])(?:{v})(?![a-z])", re.I) for k, v in SUBJECTS.items()}
MAIN_SUBJECT = re.compile(r"^\s*(?:the\s+)?(?:symptoms?|sxs?|episodes?|attacks?|spells?)\b", re.I)
GENERIC_SUBJECT = re.compile(r"^\s*(?:(?:the\s+)?(?:symptoms?|sxs?|it|they|these|this|episodes?|attacks?|spells?|events?)\b|"
                             r"(?:worse|better|lasts?|lasting|started|starts|began|occurs?|happens?|comes|associated|assoc|w/|with)\b)", re.I)
# The end of a typed note that stops mid-thought.
DANGLING = re.compile(r"(?:\.\.\.|…|(?<!\*)\*\*\*)\s*$|\b(?:when|about|to|and|or|but|with|w/|for|of|the|a|an|because|since|until|after|before|than|like|"
                      r"really|which|that|who|from|at|in|on|is|was|were|are|if|then|so|very|more|less)\s*$", re.I)
# A thought that stops: "...", "…", or the *** clinical() puts there.
CUT_END = re.compile(r"(?:\.\.\.|…|(?<!\*)\*\*\*)\s*$")
MID_PHRASE = re.compile(r"\b(?:when|about|to|and|or|but|with|w/|for|of|the|a|an|because|since|until|after|before|than|like|which|that|who|"
                        r"from|at|in|on|is|was|were|are|if|then|so|very|more|less)\W*$", re.I)
# "maybe more", "or less", "even more": a whole thought, not one cut off.
COMPLETE_END = re.compile(r"\b(?:maybe|possibly|probably|perhaps|or|no|much|even|any|a bit|a little|little|bit|or so|and so|any)\s+"
                          r"(?:more|less|so|then|longer|later)\W*$", re.I)
# A verb phrase the last word finishes: "she has been nauseous since", "never before".
COMPLETE_ADVERB = re.compile(r"\b(?:has|have|had|was|were|is|been|never|ever|not)\b[^,;.]*\s(?:since|before|after)\W*$", re.I)


def _dangles(s: str) -> bool:
    """The text stops mid-thought (and not on "maybe more" or "nauseous since", which are complete)."""
    if CUT_END.search(s):
        return True
    return bool(DANGLING.search(s)) and not COMPLETE_END.search(s) and not COMPLETE_ADVERB.search(s)

# Past medical history said in the text, by box. Only the patient's, only when not hedged or denied.
PMH = {
    "hist-hypt": r"hypertension|htn|high blood pressure|elevated blood pressure|high bp|elevated bp",
    "hist-migraine": r"(?:history of|hx of|hx|h/o|known|prior|previous|past|diagnosed with|dx(?:'d)? with|dx of)\s+(?:\w+\s+)?migraines?|"
                     r"migraines?\s+(?:history|hx)|migraineur",
    "hist-dyslip": r"dyslipidemia|hyperlipidemia|high cholesterol|hypercholesterolemia|\bhld\b",
    "hist-cad": r"coronary artery disease|\bcad\b|heart attack|myocardial infarction|\bcabg\b|coronary stents?",
    "hist-diab": r"(?<!pre)(?<!pre-)diabet\w*|\bdm\s*(?:2|ii|type 2)?\b|\bt2dm\b|\biddm\b|\bniddm\b",
    "hist-arrh": r"arrhythmias?|\bafib\b|\ba-fib\b|atrial fibrillation|atrial flutter|\bsvt\b|pacemaker",
    "hist-stroke": r"(?<!mini[- ])(?<!mini )stroke|\bcva\b|cerebrovascular accident",
    "hist-cervical": r"cervical (?:spondylosis|stenosis|disc disease|disk disease|radiculopathy|myelopathy|disease|spine disease|ddd)|"
                     r"(?:ddd|degenerative disc disease) of the (?:c-?spine|neck|cervical spine)|neck (?:arthritis|surgery|fusion)|\bacdf\b",
    "hist-trauma": r"(?:head (?:injury|trauma)|concussion|tbi)\s+(?:with|w/)\s+(?:loc|loss of consciousness)",
}
PMH_RE = {k: re.compile(rf"(?<![a-z])(?:{v})(?![a-z])", re.I) for k, v in PMH.items()}
PMH_LABEL = {k: FIELDS[k]["label"] for k in PMH}
# Someone else's health ("Dad had hearing aids", "mother has migraines", "Sister w/ migraines"): from there to the end
# of the sentence (or a "; " / ", but" / ", pt") nothing is the patient's.
FAMILY_MEMBER = (r"(?:mother|mom|father|dad|brother|sister|sibling|son|daughter|grandmother|grandfather|grandma|grandpa|aunt|uncle|"
                 r"cousin|parents?|twin|husband|wife|spouse)")
FAMILY = re.compile(rf"\b{FAMILY_MEMBER}\b(?:'s)?\s+(?:also\s+|too\s+)?(?:(?:had|has|have|with|history of|hx of|died|passed|diagnosed|"
                    r"of|gets|got|suffers|suffered|wears|wore|uses|used|needs|needed|smokes|smoked|drinks|drank|vapes)\b|w/)|"
                    r"\b(?:family history|family hx|fhx|fh)\b", re.I)
FAMILY_END = re.compile(r";|,\s*(?=(?:but|pt|patient|denies|reports)\b)", re.I)
# "Daughter states mom gets dizzy": the mother is the patient, as the daughter calls her.
TOLD_BY = re.compile(r"\b(?:says?|said|states?|stated|reports?|reported|thinks?|notes?|noticed|feels?|worries|worried)\s+(?:that\s+)?"
                     r"(?:her\s+|his\s+|their\s+)?$", re.I)
# Over and done with: "had vertigo years ago", "(that one resolved in a few days)", "used to get migraines", "mild vertigo
# first day only", "before that daily vertigo". "It never went away" is the opposite.
PAST = re.compile(r"\bused to\b|\b(?:had|got|was|were)\b[^,;()]*?\b(?:years?|yrs?|decades?|long)\s+ago\b|\bresolved\b|\bwent away\b|"
                  r"\bgone away\b|\bno longer\b(?!\s+than)|\bin the past\b|\bas a (?:kid|child|teen(?:ager)?|baby)\b|\bpreviously\b|"
                  r"\bfirst day only\b|\b(?:on|the) first day\b|\binitially only\b|\bonly (?:at|on) onset\b|\bat onset only\b|"
                  r"\bfor the first \w+ only\b|\bbefore that\b|\bprior to (?:that|this)\b|\buntil (?:last|this) \w+\b|"
                  r"\bbefore (?:starting|stopping|taking) \w+", re.I)
NOT_PAST = re.compile(r"\b(?:never|not|hasn'?t|has not|haven'?t|have not|didn'?t|did not|isn'?t|wasn'?t|won'?t)\s+(?:\w+\s+)?$", re.I)
PAST_BACK = re.compile(r"^\W*(?:(?:but|and|then|now|since then|since)\s+)*(?:(?:that|which|it|this|these|those|they|she|he|sxs?|symptoms?)\s+"
                       r"(?:one\s+|episode\s+)?(?:has\s+|have\s+|had\s+)?(?:since\s+)?(?:all\s+)?(?:resolved|went away|gone away|cleared)\b|"
                       r"(?:has\s+|have\s+)?(?:since\s+)?(?:all\s+)?(?:resolved|gone|went away|gone away|cleared|stopped)\W*$)", re.I)
SEGMENT_CUT = re.compile(r"[,;()]|\s(?=(?:but|although|though|however|now)\b)", re.I)
# A companion's report the patient contradicts in the same sentence ("husband says she's unsteady but she says no").
COMPANION = re.compile(r"\b(?:husband|wife|spouse|partner|daughter|son|mother|mom|father|dad|sister|brother|family|caregiver|friend|niece|"
                       r"nephew|grand(?:daughter|son|child))\b[^,;]*?\b(?:says?|said|states?|stated|reports?|reported|thinks?|noticed|notices|"
                       r"feels?|believes?|watches|sees)\b", re.I)
PATIENT_SAYS = re.compile(r"\b(?:she|he|pt|patient)\s+(?:says?|said|states?|stated|reports?|reported|thinks?|feels?|insists?|denies|denied|"
                          r"disagrees|disputes)\b", re.I)
PATIENT_DISPUTES = re.compile(r"\b(?:she|he|pt|patient)\s+(?:disagrees|disputes|denies|denied|says?\s+(?:no|not|otherwise)\b|"
                              r"(?:says?|feels?|thinks?|insists?)\s+(?:that\s+)?(?:she|he)(?:'s|\s+is)\s+(?:fine|ok|okay|normal|good))", re.I)
# A habit with its own amount is the patient's habit even after a denial earlier in the list ("doesn't smoke, wine on
# weekends"); "denies smoking, alcohol" is still a denial of both.
AMOUNT = re.compile(r"\d|\b(?:a|one|two|three|several|few|couple)\s+(?:glass|glasses|beers?|drinks?|cups?|packs?|cigarettes?)\b|"
                    r"\b(?:weekends?|socially|social|occasional(?:ly)?|daily|nightly|most nights|every (?:day|night|evening)|"
                    r"per (?:day|week|night)|a (?:day|week|night)|ppd|rarely|sometimes)\b|/wk|/week|/day", re.I)
# Correcting oneself: "diabetes, well, prediabetes actually".
CORRECTION = re.compile(r"^\W*(?:well|i mean|or rather|actually)\b", re.I)
_NN = r"(?<!non)(?<!non-)(?<!non )"        # "non-smoker", "lifelong non smoker"
SOCIAL = {
    "social-tobacco-remote": re.compile(rf"\b(?:former|ex-?|quit|stopped|remote|past|previous|prior)\s+(?:\w+\s+)?(?:smok\w*|tobacco|cigarettes?)\b|"
                                        rf"\bquit\s+(?:\w+\s+){{0,3}}(?:years?|yrs?|months?)\s+ago\b|{_NN}\bsmoked\b", re.I),
    # vaping is tobacco only with nicotine said: it may be cannabis
    "social-tobacco": re.compile(rf"{_NN}\b(?:smokes|smoker|smoking|tobacco|cigarettes?|cigars?|chews tobacco|ppd|pack[- ]?years?)\b|"
                                 r"\b(?:vapes?|vaping)\b(?=.*\bnicotine\b)", re.I),
    "social-etoh-remote": re.compile(r"\b(?:former|ex-?|quit|stopped|remote|past|previous|prior|hx of|history of)\s+(?:\w+\s+)?"
                                     r"(?:etoh|alcohol|drinking|drinker)\b|\bsober\b|\bquit drinking\b", re.I),
    "social-etoh-history": re.compile(r"\b(?:etoh|alcohol)\s+(?:abuse|use disorder|dependence|addiction)\b|\balcoholism\b|\balcoholic\b", re.I),
    "social-etoh": re.compile(r"\b(?:alcohol|etoh|beers?|wine|liquor|cocktails?|social drinker|drinks?\s+(?:alcohol|socially|daily|nightly|"
                              r"beer|wine|on weekends|per (?:day|week|night)|a (?:day|week|night)))\b", re.I),
    "social-drugs": re.compile(r"\b(?:marijuana|cannabis|thc|weed|cocaine|methamphetamine|meth|heroin|illicit drugs?|recreational drugs?|street drugs?)\b", re.I),
}
# "Triggers: poor sleep, red wine": a trigger is not a drinking history.
TRIGGER_LEAD = re.compile(r"\b(?:triggers?|triggered|worse (?:with|after|w/)|brought on|provoked|avoids?|avoiding|sensitive to)\b", re.I)
SOCIAL_DENIES = re.compile(r"\b(?:denies|no|negative for)\s+(?:any\s+)?(?:tobacco|smoking|cigarettes)[,/ ]+(?:(?:and|or)\s+)?(?:alcohol|etoh|drinking)"
                           r"[,/ ]+(?:(?:and|or)\s+)?(?:illicit\s+|recreational\s+)?(?:drugs?|drug use|substances?)\b|\bdenies\s+(?:t/e/d|tobacco/etoh/drugs)\b", re.I)
FALLS_YES = re.compile(r"\b(?:fell|has fallen|have fallen|had (?:a\s+)?falls?|(?:one|a|1|two|2|three|3|four|4|several|multiple|few|recent|frequent|"
                       r"\d+)\s+(?:recent\s+|mechanical\s+)?falls?|falls?\s+(?:in|over|x\s*\d|last|this|since|recently)|history of falls|hx of falls|"
                       r"(?:falls|fell)\s+(?:once|twice|\d+\s+times)|(?:been|keeps?|kept)\s+falling)\b", re.I)
FALLS_NO = re.compile(r"\b(?:no|denies|denied|without|w/o|negative for|never)\s+(?:any\s+)?(?:recent\s+|interim\s+|further\s+|new\s+)?falls?\b|"
                      r"\b(?:hasn'?t|has not|have not|haven'?t|never)\s+fallen\b|"
                      r"\b(?:hasn'?t|has not|have not|haven'?t|did not|didn'?t|never)\s+(?:had|have)\s+(?:any\s+)?(?:recent\s+)?falls?\b", re.I)
# "... but not the dizzy": a "not" that leans on the clause before it
ELLIPTIC_NOT = re.compile(r"\s*(?:but|although|though|whereas|yet)\s+not\b", re.I)
# A dizzy feeling only as part of a fall ("once lightheaded getting up at night", "fell after getting dizzy") is that
# event, not a complaint of its own.
FALL_EVENT = re.compile(r"\b(?:fell|falls?|fallen|falling)\b", re.I)
IMBALANCE_SAID = re.compile(r"\b(?:wobbly|unsteady|unsteadiness|off[- ]balance|imbalance|disequilibrium|rocking|swaying)\b", re.I)
NEAR_FALL = re.compile(r"\b(?:near[- ]falls?|almost fell|nearly fell|almost falling|nearly falling)\b", re.I)
GAIT_DENY = {"ga-assoc-weakness-denies": r"weakness|weak", "ga-assoc-sensory-denies": r"numbness|numb|tingling|paresthesias?|sensory (?:loss|changes)",
             "ga-assoc-bowel-denies": r"bowel|b/b|bowel or bladder|bowel/bladder|bowel and bladder",
             "ga-assoc-bladder-denies": r"bladder|urinary|b/b|bowel or bladder|bowel/bladder|bowel and bladder"}
MED_CHANGE = re.compile(r"\b(?:medications?|meds?|pills?|dose|doses|prescription)\b[^.;]*?\b(?:changed|change|started|stopped|new|increased|decreased|"
                        r"switched|adjusted|missed|missing|ran out|not taking|skips?|skipped|forgets?)\b|"
                        r"\b(?:changed|started|stopped|increased|decreased|switched|adjusted|missed|ran out of|not taking|skips?|skipped)\b[^.;]*?"
                        r"\b(?:medications?|meds?|pills?|doses?|prescription)\b", re.I)
# A medication the patient no longer takes ("Stopped meclizine", "gabapentin ..., stopped after 1 wk"): one to review,
# not one to add to the list.
MED_STOPPED = re.compile(r"\b(?:stopped|stop|discontinued|d/c'?d|d/c|quit|off|no longer (?:on|taking|takes)|came off|weaned off|ran out of)\b", re.I)
# A dose, a sig: what a medication clause looks like ("ASA 81 mg daily", "metoprolol 25 mg BID").
DOSE = re.compile(r"\b\d+(?:\.\d+)?\s*(?:mg|mcg|g|ml|units?|iu)\b|\b(?:qd|bid|tid|qid|qhs|qam|qpm|prn|q\d+h)\b", re.I)
GENERIC_MEDS = re.compile(r"\b(?:blood pressure|bp|cholesterol|heart|thyroid|diabetes|sugar|sleep(?:ing)?|pain|water|nerve|anxiety|depression|"
                          r"seizure|migraine|headache|dizziness|vertigo|allergy)\s+(?:medications?|meds?|pills?)\b|\bwater pills?\b", re.I)


@dataclass
class Reading:
    set: dict = field(default_factory=dict)       # box -> value, only what the text says plainly
    why: dict = field(default_factory=dict)       # box -> the words that ticked it
    unclear: list[str] = field(default_factory=list)
    unfinished: str = ""                          # the last thought, when it stops mid-sentence
    meds: list[str] = field(default_factory=list)          # medication names in the text, as written
    med_notes: list[str] = field(default_factory=list)     # medication changes, misses, unnamed meds
    facts: dict = field(default_factory=dict)              # age, sex, hand from the opening
    # sentence by sentence, for the HPI builder: {text, topics (the symptom the sentence continues), set (what it
    # ticked or filled), unclear (kinds of doubt it raised), cut (it stops mid-thought)}
    sentences: list[dict] = field(default_factory=list)
    past: list[str] = field(default_factory=list)          # sections the notes say are over ("vertigo resolved")

    def as_dict(self) -> dict:
        return {"set": self.set, "why": self.why, "unclear": self.unclear, "unfinished": self.unfinished, "meds": self.meds,
                "med_notes": self.med_notes, "facts": self.facts}


# ---------------------------------------------------------------------------------------------------- pieces

def _clean(text: str) -> str:
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n").replace("’", "'").replace("“", '"').replace("”", '"')
    text = re.sub(r"\b(\d{1,3})\s*[yY]\.[oO]\.", r"\1 y/o", text)
    return re.sub(r"[ \t]+", " ", text)


# Where a sentence ends: after . ! ? ; ... or the *** clinical() leaves, not after "Dr." or "vs.", not at a "?" in
# front of a word ("referred for ? BPPV", "hearing loss AD, ? AS too"), and not between a question and its one-word
# answer ("Headache? Denies.", "Tinnitus? Yes, AS").
ANSWER = r"(?:denies|denied|no|none|neg|negative|nope|yes|endorses|endorsed|reports|positive)\b"
SENTENCE_END = re.compile(r"(?<=[.!;])(?<!\bDr\.)(?<!\bMr\.)(?<!\bMs\.)(?<!\bMrs\.)(?<!\bSt\.)(?<!\bvs\.)\s+(?=\S)|"
                          rf"(?<=\?)(?<![\s,(]\?)\s+(?=\S)(?!{ANSWER}|\+)|(?<=\.\.\.)\s+|(?<=…)\s+|(?<=\s\*\*\*)\s+(?=\S)", re.I)
# The question-and-answer shorthand, read as what it says: "Headache? Denies." is "denies headache".
QA_NO = re.compile(r"^(?P<q>[^?]+?)\s*\?\s+(?:denies|denied|no|none|neg|negative|nope)\b[.,;:]?\s*", re.I)
QA_YES = re.compile(r"^(?P<q>[^?]+?)\s*\?\s+(?:yes|endorses|endorsed|reports|positive|\+)(?:\b|(?=\s))[.,;:]?\s*", re.I)


def sentences(text: str) -> list[str]:
    out = []
    for line in _clean(text).split("\n"):
        for s in SENTENCE_END.split(line):
            s = s.strip()
            if s and re.search(r"[A-Za-z]", s):
                out.append(s)
    return out


def _as_read(s: str) -> str:
    """A question answered in the same sentence, as a plain statement: "Headache? Denies." -> "Denies headache."."""
    m = QA_NO.match(s)
    if m:
        return "Denies " + re.sub(r"^(?:any|other)\s+", "", m.group("q").strip(), flags=re.I) + ". " + s[m.end():]
    m = QA_YES.match(s)
    if m:
        return m.group("q").strip() + (", " + s[m.end():] if s[m.end():].strip() else ".")
    return s


def _short(s: str, n: int = 140) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1].rsplit(" ", 1)[0] + "…"


def _topic_hits(text: str) -> list[tuple[int, str, re.Match]]:
    """Every symptom word in the text, in order: (where, section, match)."""
    t = _without_pmh(text)
    hits = []
    for k, rx in TOPIC_RE.items():
        for m in rx.finditer(t):
            if k == "headache" and m.group(0).lower() == "ha" and m.group(0) != "HA":
                continue                  # "ha" that isn't the abbreviation HA
            hits.append((m.start(), k, m))
    return sorted(hits, key=lambda h: h[0])


def _topics(text: str) -> list[str]:
    """The symptom sections the text names, in the order it names them."""
    return list(dict.fromkeys(k for _pos, k, _m in _topic_hits(text)))


def _without_pmh(text: str) -> str:
    """History mentions ("history of migraines") aren't the current symptom. Same length, so positions line up."""
    return PMH_RE["hist-migraine"].sub(lambda m: " " * len(m.group(0)), text)


def _clause_doubt(text: str) -> bool:
    """A hedge over the whole clause: "not sure", "unsure", a question; not a "?" in front of one word ("? BPPV")."""
    return bool(HEDGE_CLAUSE.search(text) or MOSTLY_DENIED.search(text))


def hedged_spans(text: str) -> list[tuple[int, int]]:
    """Where hedges apply: a few words after "maybe", "? X" or "R/O X", both sides of "X vs Y", the whole clause for
    "not sure" or "no X usually"."""
    spans = []
    if _clause_doubt(text):
        spans.append((0, len(text)))
    for m in HEDGE_AHEAD.finditer(text):
        if re.match(r"\s*(?:[,.;?!]|$)", text[m.end():]):
            start = max(text.rfind(",", 0, m.start()), text.rfind(";", 0, m.start())) + 1
            spans.append((start, m.end()))
            continue
        # "maybe 30 seconds": the number is hedged, the unit isn't
        if re.match(rf"\s+(?:\d+|{wording.NUM_WORD}|a few|several|half|about|around|~)", text[m.end():], re.I) \
                and re.match(rf"\s+(?:about\s+|around\s+|~\s*)?(?:\d+|{wording.NUM_WORD}|a few|several|half)(?:\s*(?:or|to|-)\s*(?:\d+|{wording.NUM_WORD}))?"
                             r"\s*(?:seconds?|secs?|minutes?|mins?|hours?|hrs?|days?|weeks?|months?|years?|times?|x)\b", text[m.end():], re.I):
            continue
        stop = re.search(r"[,.;]|\b(?:but|and|although|then|though)\b", text[m.end():])
        end = m.end() + (stop.start() if stop else len(text) - m.end())
        words = list(re.finditer(r"\S+", text[m.end():end]))
        if len(words) > NEAR_WORDS:
            end = m.end() + words[NEAR_WORDS - 1].end()
        spans.append((m.start(), end))
    for m in VERSUS.finditer(text):
        start = max(text.rfind(",", 0, m.start()), text.rfind(";", 0, m.start())) + 1
        stop = re.search(r"[,.;]", text[m.end():])
        spans.append((start, m.end() + (stop.start() if stop else len(text) - m.end())))
    return spans


def _in(spans, start: int, end: int) -> bool:
    return any(a <= start and end <= b for a, b in spans)


# A comma that starts a new subject ends what a denial before it covers: "nothing spins, I can't keep my balance",
# "no vertigo, more lightheaded".
NEW_SUBJECT = re.compile(r"[,;]\s*(?:it'?s|i|i'm|i've|she|he|she's|he's|they|they're|pt|patient|more|mostly|mainly|rather|instead|just)\b", re.I)
GENERAL_DOUBT = ("Unsure", "Check")
# Said plainly, these say what the dizziness is like ("lightheaded"): the description, when nothing else describes it.
PLAIN_QUALITY = re.compile(r"\b(?:light[- ]?headed(?:ness)?|woozy|wooziness|presyncop\w*)\b", re.I)
# "ringing in the left ear, maybe both": which side is in doubt.
SIDE_DOUBT = re.compile(r"[,;]\s*(?:or\s+)?(?:(?:maybe|possibly|probably|perhaps|or)\s+|\?\s*)(?:both|bilateral\w*|the other(?:\s+(?:side|ear))?|"
                        r"right|left|(?-i:AD|AS|AU))\b", re.I)
NOT_SIDE = wording.NOT_SIDE_RE
# "hx of AD" is Alzheimer's disease, not the right ear.
ABBR_NOT_EAR = re.compile(r"(?:\bhx of|\bhistory of|\bh/o|\bdx(?:\s+(?:of|with))?|\bdiagnosed(?:\s+with)?|\br/o|\bfamily hx of|\bmother|\bfather|"
                          r"\bmom|\bdad|\bwith|\bw/)\s*$", re.I)
# "now more of a constant imbalance", "feels like rocking": what the dizziness is like, not a gait complaint.
DIZZ_QUALITY = re.compile(r"(?:\bmore (?:of )?(?:an? )?|\blike (?:an? )?|\bdescribed as (?:an? )?|\bfeels? like (?:an? )?|\bfeeling of |"
                          r"\bsense of |\bkind of (?:an? )?|\bsort of (?:an? )?)(?:\w+\s+){0,2}$", re.I)
# "doesn't go anywhere without her cane due to imbalance": the reason isn't denied.
REASON_LINK = re.compile(r"\b(?:due to|because(?: of)?|secondary to|d/t|2/2|from the)\b", re.I)


def _denied_at(clause: str, pos: int) -> bool:
    """A denial before this point of the clause, not undone by "except", a new subject, a reason ("due to"), or a
    second negative ("never free of")."""
    for m in DENIAL.finditer(clause[:pos]):
        between = clause[m.end():pos]
        if EXCEPT.search(between) or NEW_SUBJECT.search(between) or REASON_LINK.search(between):
            continue
        if m.group(0).lower() in ("without", "w/o", "free of") and DOUBLE_NEG.search(clause[: m.end()]):
            continue
        if m.group(0).lower() in ("without", "w/o") and re.match(r"\s*(?:them|it|those|these|that|this|him|her)\b", between, re.I):
            continue                              # "without them I can't hear": without the hearing aids
        if DOUBLE_NEG.search(clause[m.start(): pos]):
            continue
        return True
    return False


def _polarity(clause: str) -> str:
    return "deny" if DENIAL.search(clause) or NORMAL_WORDS.search(clause) else "affirm"


def _subjects(clause: str) -> set[str]:
    """What the clause is about. Vertigo words are their own subject ("dizziness:vertigo"), so "denies vertigo but
    endorses lightheadedness" isn't one subject said both ways."""
    found = {k for k, rx in SUBJECT_RE.items() if rx.search(clause)}
    for _pos, k, m in _topic_hits(clause):
        if k == "dizziness" and SUBJECT_RE["vertigo"].fullmatch(m.group(0)):
            continue
        found.add(k)
    return found


def _words_for(name: str, section: str) -> list[str]:
    prefix = next((p for p in GROUPS[section].values() if name.startswith(p)), "")
    key = name[len(prefix):] if prefix else name
    words = list(WORDS.get(key, [])) + [w for w in dictation.ALIASES.get(key, []) if len(w) >= 4]
    label = FIELDS[name].get("label", "").lower()
    if label and 4 <= len(label) < 60 and label not in ("other", "none", "attacks", "normal", "yes", "no"):
        words.append(label)
    return sorted(set(words), key=len, reverse=True)


def _boxes(section: str, group: str) -> list[str]:
    prefix = GROUPS[section].get(group)
    if not prefix:
        return []
    out = []
    for n, spec in FIELDS.items():
        if spec["type"] != "checkbox" or not n.startswith(prefix) or n.endswith(("-denies", "-other", "-none", "-notab")):
            continue
        rest = n[len(prefix):]
        if group == "side":
            if rest in ("right", "left", "bilat", "global", "frontal", "occipital", "pulsatile", "nonpulsatile"):
                out.append(n)
        elif "-" not in rest or group in ("course", "dur"):
            out.append(n)
    return out


def _find(words: list[str], text: str):
    for w in words:
        for m in re.finditer(rf"(?<![a-z]){re.escape(w)}(?![a-z])", text, re.I):
            if not re.search(r"\bnon[- ]?$", text[: m.start()], re.I):      # "non-throbbing" isn't throbbing
                return m
    return None


def _without_family(text: str) -> str:
    """The sentence with what is about someone else blanked out (same length, so positions still line up)."""
    out = text
    for m in FAMILY.finditer(text):
        if TOLD_BY.search(text[: m.start()]) and not re.match(r"\w+\s+(?:had|has|have)\s+(?:a\s+)?(?:hx|history)\b", text[m.start():], re.I):
            continue                          # "daughter states mom gets dizzy": mom is the patient
        stop = FAMILY_END.search(text, m.end())
        end = stop.start() if stop else len(text)
        out = out[: m.start()] + " " * (end - m.start()) + out[end:]
    return out


def _without_past(text: str) -> tuple[str, bool]:
    """The clause with what is over and done with blanked out, and whether there was any."""
    cuts = [0] + [m.start() for m in SEGMENT_CUT.finditer(text)] + [len(text)]
    segs = [(a, b) for a, b in zip(cuts, cuts[1:]) if b > a]
    out, found = list(text), False
    for i, (a, b) in enumerate(segs):
        seg = text[a:b].lstrip(",;() ")
        if _is_past(seg):
            found = True
            out[a:b] = " " * (b - a)
            if PAST_BACK.match(seg):               # "(that one resolved ...)", ", now resolved": about the words before
                before = [s for s in segs[:i] if re.search(r"[A-Za-z]", text[s[0]:s[1]])]
                if before:
                    pa, pb = before[-1]
                    out[pa:pb] = " " * (pb - pa)
    return "".join(out), found


def _is_past(seg: str) -> bool:
    """Something over and done with ("resolved", "used to"), not "it never went away"."""
    return any(not NOT_PAST.search(seg[: m.start()]) for m in PAST.finditer(seg))


# The parts of a clause (between commas), for whose details are whose: "intermittent dizziness w/ head turns, HA daily"
# is intermittent dizziness and daily headaches, not the other way round.
PART_CUT = re.compile(r"[,;()]|\s(?=(?:but|although|though|however|whereas)\b)", re.I)
ASSOC_LEAD = re.compile(r"(?:\bw/|\bwith|\balong with|\bassociated with|\bassoc(?:iated)?\s+w/|\baccompanied by|\bwithout|\bw/o|\+)\s*(?:\w+\s+){0,2}$", re.I)
ASSOC_CUE = re.compile(r"(?:\bw/|\bwith\b|\balong with\b|\bassoc\w*|\baccompan\w*|\+)[^,;.]*$", re.I)
# The association boxes that name another section's symptom: ticked only with "w/", "associated", "+" before them.
OTHER_SYMPTOM = {"dizziness": ("tinnitus", "hearingloss", "headache"), "hearing": ("vertigo", "diseq", "headache", "tinnitus"),
                 "tinnitus": ("vertigo", "diseq", "headache", "hearingloss"), "gait": ("headache",),
                 "headache": ("vertigo", "diseq", "hearing", "tinnitus")}
EVENT_NOUN = re.compile(r"\b(?:spells?|attacks?|episodes?|events?)\b", re.I)


def _is_med_part(part: str, med_names=None) -> bool:
    """A part about a medication ("ASA 81 mg daily", "metoprolol 25 mg BID", "started sertraline"): its "daily" is the
    dose, not how often a symptom comes. A part that names a symptom too is about the symptom, unless it gives a dose."""
    if DOSE.search(part):
        return True
    return not _topics(part) and bool(_names_med(part) if not med_names else find_meds(part, med_names))


@functools.lru_cache(maxsize=2048)
def _names_med(part: str) -> bool:
    return bool(find_meds(part))


def _gait_as_dizziness(text: str, pos: int, end: int) -> bool:
    """A gait word that says what the dizziness is like: "now more of a constant imbalance", "rocking/off-balance
    sensation", "feels like rocking"."""
    return bool(DIZZ_QUALITY.search(text[:pos]) or re.match(r"(?:\s*/\s*[\w-]+)*\s+(?:sensation|feeling)\b", text[end:], re.I)
                or re.search(r"(?:dizz\w*|rocking|swaying|lightheaded\w*|woozy)\s*/\s*$", text[:pos], re.I))


def _parts(clause: str) -> list[tuple[int, int, list[str], list[str]]]:
    """The clause in parts, each with the symptom sections it is about: the ones it names (a symptom only named as an
    association, "vertigo w/ tinnitus", isn't what the part is about), else the ones of the part before it. "Spells
    last 2-3 hours" is about the dizziness when the clause has dizziness; "all AS" is about everything listed before
    it. The fourth item adds the symptoms named as associations, for the association words that follow ("dizziness
    with the headaches, light and sound sensitivity")."""
    cuts = [0] + [m.end() for m in PART_CUT.finditer(clause)] + [len(clause)]
    spans = [(a, b) for a, b in zip(cuts, cuts[1:]) if b > a]
    all_topics = _topics(clause)
    out, last, last_assoc, seen = [], None, None, []
    for a, b in spans:
        part = clause[a:b]
        hits = [(pos, "dizziness" if k == "gait" and "dizziness" in all_topics and _gait_as_dizziness(part, pos, m.end()) else k, m)
                for pos, k, m in _topic_hits(part)]
        named = [k for _p, k, _m in hits]
        owners = [k for i, (pos, k, _m) in enumerate(hits) if not (i and ASSOC_LEAD.search(part[:pos]))]
        owners = list(dict.fromkeys(owners or named))
        assoc = list(dict.fromkeys(owners + named))
        if not owners and re.match(r"\W*(?:all|both|each)\b", part, re.I) and seen:
            owners = assoc = list(seen)           # "w/ roaring tinnitus, fullness and muffled hearing, all AS"
        if not owners and EVENT_NOUN.search(part) and "dizziness" in all_topics:
            owners = assoc = ["dizziness"]
        if not owners and last is not None and not re.match(r"\W*(?:but|although|though|however|whereas)\b", part, re.I):
            owners, assoc = last, last_assoc
        out.append((a, b, owners, assoc))
        if owners:
            last, last_assoc = owners, assoc
        seen += [k for k in named if k not in seen]
    # the parts before the first symptom named are its, unless a "but" stands between ("now improving but still off
    # balance": what is improving is the complaint before, not the balance)
    first = next(((o, x) for _a, _b, o, x in out if o), ([], []))
    lead = []
    for a, b, o, x in out:
        if o:
            break
        lead.append((a, b))
    contrast = any(re.match(r"\W*(?:but|although|though|however|whereas)\b", clause[a:b], re.I)
                   for a, b, o, x in out[len(lead): len(lead) + 1])
    return [(a, b, o or ([] if contrast else first[0]), x or ([] if contrast else first[1])) for a, b, o, x in out]


def _owned(section: str, clause: str, med_names=None, whole: str = "", at: int = 0, meds: bool = False, assoc: bool = False) -> str:
    """The clause with the parts about other symptoms blanked out (and, with `meds`, the parts about a medication: its
    "daily" is the dose); same length. A part about no symptom, and a clause that names none (carried over from the
    sentence before), is this section's. Given the `whole` sentence and where the clause starts in it, the parts are
    those of the sentence ("..., 1-2x/mo, w/ photophobia, +HA in ~half": the photophobia goes with what comes before
    it). With `assoc`, a part also counts for the symptoms it carries as associations."""
    text = whole if whole and whole[at: at + len(clause)].strip() and len(whole) >= at + len(clause) else clause
    at = at if text is whole else 0
    parts = _parts(text)
    out = list(clause)
    for a, b, owners, assoc_owners in parts:
        mine = assoc_owners if assoc else owners
        if (mine and section not in mine) or (meds and _is_med_part(text[a:b], med_names)):
            lo, hi = max(a - at, 0), min(b - at, len(clause))
            if hi > lo:
                out[lo:hi] = " " * (hi - lo)
    return "".join(out)


# ---------------------------------------------------------------------------------------------------- reading

# A "better with medications" box and the text box that names them.
MED_TEXT = {"better-meds": "better-thesemeds", "h-better-meds": "h-better-thesemeds"}
SIMPLE_MEDS = {"ibuprofen", "tylenol", "advil", "motrin", "aleve", "excedrin", "aspirin", "meclizine", "sumatriptan", "nurtec", "acetaminophen",
               "naproxen", "imitrex", "ubrelvy", "valium", "diazepam", "dramamine"}

# ---------------------------------------------------------------------------------------------------- onset, description, frequency

# The text boxes each symptom section has for what the words say about it. Values are short and in the clinic's
# style ("~3 weeks ago", "possibly January", "Longstanding", "lightheadedness w/ room moving at times", "~2x/month").
ONSET_FIELD = {"dizziness": "DizzOnsetDate", "hearing": "HLOnsetDate", "tinnitus": "TinnitusOnsetDate", "gait": "GAOnsetDate",
               "headache": "HOnsetDate"}
DESC_FIELD = {"dizziness": "DizzyDesc", "hearing": "HLdesc", "tinnitus": "tinnitus-descr", "gait": "gaitataxia-descr",
              "headache": "headache-descr"}
SLOTS = set(ONSET_FIELD.values()) | set(DESC_FIELD.values()) | {"OnsetDate", "DizzFreq", "h-freq-wk", "h-freq-month"}

_MONTH = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_SEASON = r"(?:spring|summer|fall|autumn|winter)"
_WHEN = (rf"(?:(?:early|late|mid)[- ]?)?(?:{_MONTH}|{_SEASON})\b(?:\s+(?:of\s+)?(?:19|20)\d\d\b)?|(?:19|20)\d\d\b|"
         rf"last\s+(?:week|month|year|{_SEASON}|{_MONTH})\b|this\s+(?:past\s+)?(?:week|month|year|{_SEASON})\b|yesterday\b|childhood\b")
_HEDGE_W = r"(?:maybe|possibly|perhaps|probably)"
_SPAN = (rf"(?:(?P<h>{_HEDGE_W})\s+)?(?:(?P<a>about|around|approximately|approx\.?|roughly|~|like|nearly|almost)\s*)?"
         rf"(?:(?P<more>over|more than|less than|at least|>|<)\s*)?"
         rf"(?P<q>\d+(?:\.\d+)?\+?(?:\s*(?:-|to|or)\s*\d+(?:\.\d+)?\+?)?|a few|a couple(?:\s+of)?|several|an?|"
         rf"{wording.NUM_WORD}(?:\s*(?:-|to|or)\s*{wording.NUM_WORD})?)\s*(?P<u>days?|weeks?|wks?|months?|mos?|years?|yrs?)\b")
# "... or maybe longer", "two weeks ago or maybe three": the other value said, kept with its hedge
_TAIL = (rf"(?P<tail>\s+or\s+(?:(?:maybe|possibly|probably|perhaps)\s+)?(?:longer|more|so|less|earlier))?"
         rf"(?P<alt>\s+or\s+(?:(?:maybe|possibly|probably|perhaps)\s+)?(?:\d+|{wording.NUM_WORD})(?!\s*(?:x\b|times)))?")
ONSET_PATTERNS = [   # (pattern, kind); tried in this order
    (re.compile(rf"\b(?:since|started|starting|began|begun|beginning|onset|came on)\s+(?:(?P<h>{_HEDGE_W})\s+)?"
                rf"(?:(?:in|on|around|about|approximately|~)\s*)?(?P<w>{_WHEN})", re.I), "when"),
    (re.compile(rf"{_SPAN}\s+ago\b{_TAIL}", re.I), "ago"),
    (re.compile(rf"\bx\s*{_SPAN}(?!\s+ago){_TAIL}", re.I), "ago"),
    (re.compile(rf"(?<!lasts )(?<!lasting )(?<!lasted )(?<!last )\bfor\s+(?:the\s+)?(?:past\s+|last\s+)?{_SPAN}"
                rf"(?!\s+(?:ago|at a time|each|per)){_TAIL}", re.I), "ago"),
    (re.compile(r"\b(?:x|for|over)\s+(?:many\s+|several\s+|the\s+past\s+(?:few\s+|several\s+)?)?(?:years|yrs)\b(?!\s+ago)|"
                r"\blongstanding\b|\bfor\s+a\s+long\s+time\b", re.I), "long"),
]
ONSET_BARE_END = re.compile(rf"(?<=\w)\s+{_SPAN}{_TAIL}\W*$", re.I)
START_VERB = re.compile(r"\b(?:started|starting|began|begun|beginning|onset|came on|first noticed|woke up|since)\b", re.I)
ONSET_LEAD = re.compile(r"^\W*(?:(?:the\s+)?(?:symptoms?|sxs?|it|this|they|which|that|these|episodes?|attacks?)\s+)?(?:first\s+|all\s+|have\s+|"
                        r"has\s+|had\s+)?(?:started|began|begun|starting|beginning|onset|came on|since|been going on)\b", re.I)
NOT_ONSET = re.compile(r"\b(?:medications?|medicines?|meds?|pills?|drugs?|job|therapy|exercises?|treatment|taking|prescribed|falls?|fell)\b", re.I)
ONSET_UNSURE = re.compile(r"\b(?:unclear|uncertain|unsure|not sure|doesn'?t (?:know|remember|recall)|does not (?:know|remember|recall)|"
                          r"can'?t (?:remember|recall)|cannot (?:remember|recall))\s+(?:when|how long)\b", re.I)
SEGMENT = re.compile(r"[^,;()]+")

DESC_RE = re.compile(r"\b(?:described as|describes (?:it |this |them |these |the \w+ )?as|feels? like|felt like|feeling like|sounds? like)\s+"
                     r"(?P<d>[^.;]+)", re.I)
DESC_STOP = re.compile(r",?\s+(?:worse|better|lasting|lasts|lasted|started|starting|began|since|onset|associated|assoc|he|she|it'?s|it is|they|"
                       r"pt|which|and then)\b|,?\s+x\s*\d|"
                       r",?\s+for\s+(?:about\s+|~)?\d|,\s+(?:and\s+)?(?:w/|with)\s", re.I)
DESC_NOUN = {"lightheaded": "lightheadedness", "light-headed": "lightheadedness", "woozy": "wooziness", "unsteady": "unsteadiness",
             "dizzy": "dizziness", "foggy": "fogginess"}
SOUND_RE = re.compile(r"\b(?:(?P<p>high|low)[- ]pitched\s+)?(?P<s>whooshing|buzzing|roaring|hissing|humming|crickets|clicking|whistling|"
                      r"static)\b|\b(?P<p2>high|low)[- ]pitched(?:\s+(?P<s2>ringing|tone|tinnitus))?\b", re.I)
HEARING_WORD_RE = re.compile(r"\b(?:muffled|plugged|underwater|tinny|distorted)\b", re.I)
FREQ_SLOT = re.compile(rf"(?:(?P<h>{_HEDGE_W})\s+)?(?:(?P<a>about|around|approximately|roughly|~|like)\s*)?"
                       rf"(?:(?P<n>\d+(?:\s*(?:-|to|or)\s*\d+)?|{wording.NUM_WORD}(?:\s*(?:-|to|or)\s*{wording.NUM_WORD})?)\s*"
                       r"(?:x|times|headaches?|episodes?|attacks?|spells?|days?)|(?P<n2>once|twice))\s*(?:a|an|per|each|every|/|in\s+a)\s*"
                       r"(?P<u>day|night|week|wk|month|mo|year)\b", re.I)
FREQ_MORE = re.compile(r"\s*,?\s*(?:or\s+)?(?P<h>maybe|possibly|probably|perhaps)?\s*(?:or\s+)?(?P<w>more|less|more often|less often)\b", re.I)
FREQ_WORDS = re.compile(r"\b(?P<w>daily|every day|nightly|every night|(?:several|a few|multiple|many) times (?:a|per|each) (?:day|week|month))\b", re.I)


def _digits(q: str) -> str:
    q = re.sub(rf"\b{wording.NUM_WORD}\b", lambda w: str(wording.word_number(w.group(0))), q, flags=re.I)
    q = re.sub(r"\bonce\b", "1", re.sub(r"\btwice\b", "2", q, flags=re.I), flags=re.I)
    return re.sub(r"\s*(?:-|to)\s*", "-", q.strip())


def _hedge_word(h: str | None) -> str:
    if not h:
        return ""
    return "probably " if h.lower() == "probably" else "possibly "


def _span_text(m: re.Match, ago: bool = True) -> str:
    """"maybe 6 or 8 months" -> "possibly 6 or 8 months ago"; "about 3 wks" -> "~3 weeks ago"."""
    a = (m.group("a") or "").lower()
    a = "" if not a else (a + " " if a in ("nearly", "almost") else "~")
    more = {"over": ">", "more than": ">", ">": ">", "less than": "<", "<": "<", "at least": "at least "}.get((m.group("more") or "").lower(), "")
    q = m.group("q").lower()
    q = "1" if q in ("a", "an") else _digits(q)
    if q.endswith("+") and more == ">":
        more = ""
    alt = m.group("alt") if "alt" in m.re.groupindex else None
    if alt:                                  # "2 weeks ago or maybe 3" -> "2 or possibly 3 weeks ago"
        hedged = re.search(r"\b(?:maybe|possibly|probably|perhaps)\b", alt, re.I)
        q += " or " + ("possibly " if hedged else "") + _digits(alt.split()[-1])
    unit = {"d": "day", "w": "week", "m": "month", "y": "year"}[m.group("u")[0].lower()]
    unit += "" if q == "1" else "s"
    tail = ""
    if "tail" in m.re.groupindex and m.group("tail"):
        tail = " " + re.sub(r"\b(?:maybe|perhaps)\b", "possibly", " ".join(m.group("tail").split()).lower())
    return f"{_hedge_word(m.group('h'))}{a}{more}{q} {unit}" + (" ago" if ago else "") + tail


def _when_text(m: re.Match) -> str:
    when = " ".join(m.group("w").split())
    when = re.sub(rf"\b{_MONTH}\b", lambda x: x.group(0).capitalize(), when, flags=re.I)
    return _hedge_word(m.group("h")) + when


def onset_value(text: str, topic: "re.Pattern | None" = None, anywhere: bool = False) -> str:
    """The onset the words give, in clinic form, or "". A time phrase counts in a part of the sentence (between commas)
    about the symptom (`topic`), or one that starts with it or with "started" / "since" ("Started maybe January",
    "..., 4 days ago"), so "3 falls since June" isn't the onset of the imbalance. `anywhere` drops that check (the
    opening sentence is all about the complaint)."""
    for rx, kind in ONSET_PATTERNS:
        for m in rx.finditer(text):
            seg = next((g for g in SEGMENT.finditer(text) if g.start() <= m.start() < g.end()), None)
            seg_text = seg.group(0) if seg else text
            lead = text[seg.start(): m.start()] if seg else text[: m.start()]
            if NOT_ONSET.search(seg_text) or find_meds(seg_text):
                continue                                  # "started meclizine 2 weeks ago" is the medication's start
            if not (anywhere or not lead.strip(" ,;(") or START_VERB.search(lead) or ONSET_LEAD.match(lead)
                    or (topic is not None and topic.search(_without_pmh(seg_text)))):
                continue
            if kind == "when":
                return _when_text(m)
            if kind == "long":
                return "Longstanding"
            return _span_text(m)
    return ""


ONSET_ONLY = re.compile(rf"^\W*(?:(?:which|that|and)\s+)?(?:(?:started|starting|began|beginning|onset)\s+)?(?:(?:for|x|over)\s*)?(?:the\s+)?"
                        rf"(?:past\s+|last\s+)?{_SPAN}(?:\s+ago)?{_TAIL}\W*$", re.I)
ONSET_ONLY_WHEN = re.compile(rf"^\W*(?:(?:which|that|and)\s+)?(?:started|starting|began|beginning|onset|since)\s+(?:(?P<h>{_HEDGE_W})\s+)?"
                             rf"(?:(?:in|on|around|about|approximately|~)\s*)?(?P<w>{_WHEN})\W*$", re.I)
ONSET_ONLY_LONG = re.compile(r"^\W*(?:x|for)\s+(?:many\s+|several\s+)?(?:years|yrs)\W*$", re.I)


def onset_phrase(fragment: str) -> str:
    """The onset, when these words are nothing but a time ("maybe 6 or 8 months", "for eight months", "started around 3
    weeks ago or maybe longer", "since January"); "" otherwise."""
    m = ONSET_ONLY.match(fragment or "")
    if m:
        return _span_text(m)
    m = ONSET_ONLY_WHEN.match(fragment or "")
    if m:
        return _when_text(m)
    return "Longstanding" if ONSET_ONLY_LONG.match(fragment or "") else ""


def _desc_text(words: str) -> str:
    """A description in the clinic's words, short: "lightheaded but also room moving sometimes" ->
    "lightheadedness w/ room moving at times"."""
    text = wording.clinical(words)
    if not text or text.endswith(wording.BLANK):
        return ""
    text = text.rstrip(".?:")
    if re.match(r"(?:on|in|at|to|for|with|w/|of|from|by|when|if)\b", text, re.I):
        return ""                                # "like on a boat": a comparison, not a description to print
    if not (text.split(" ", 1)[0].isupper() and len(text.split(" ", 1)[0]) > 1):
        text = text[:1].lower() + text[1:]
    text = re.sub(r"\bbut also\b", "w/", text)
    text = re.sub(r"\band also\b", "and", text)
    text = re.sub(r"\bsometimes\b", "at times", text)
    first = text.split(" ", 1)
    if first[0] in DESC_NOUN:
        text = DESC_NOUN[first[0]] + (" " + first[1] if len(first) > 1 else "")
    return text if 2 < len(text) <= 60 else ""


def describe(section: str, clause: str, sentence: str = "") -> str:
    """How the clause describes the symptom ("described as ...", the sound of the tinnitus, muffled hearing), or "".
    A description starts in the clause but may run on in the sentence ("described as lightheaded but also room
    moving sometimes")."""
    m = DESC_RE.search(clause)
    if m and sentence:
        at = sentence.find(m.group(0)[:24])
        m = DESC_RE.search(sentence, at) if at >= 0 else m
    if m:
        d = m.group("d")
        stop = DESC_STOP.search(d)
        return _desc_text(d[: stop.start()] if stop else d)
    if section == "tinnitus":
        m = SOUND_RE.search(clause)
        if m and not _denied_at(clause, m.start()) and not re.search(r"\bnon[- ]?$", clause[: m.start()], re.I):
            pitch = m.group("p") or m.group("p2")
            sound = m.group("s") or (m.group("s2") if (m.group("s2") or "").lower() != "tinnitus" else "") or ""
            return " ".join(x for x in ((pitch.lower() + "-pitched") if pitch else "", sound.lower()) if x)
    if section == "hearing":
        m = HEARING_WORD_RE.search(clause)
        if m and not _denied_at(clause, m.start()):
            return m.group(0).lower()
    return ""


def frequency(clause: str) -> tuple[str, str, str, bool]:
    """How often, from the clause: (text for a frequency box, count, per what, hedged), or empty strings."""
    m = FREQ_SLOT.search(clause)
    if m and not _denied_at(clause, m.start()):
        unit = {"wk": "week", "mo": "month"}.get(m.group("u").lower(), m.group("u").lower())
        n = _digits((m.group("n") or m.group("n2")).lower())
        approx = "~" if m.group("a") else ""
        hedge = _hedge_word(m.group("h"))
        more = FREQ_MORE.match(clause[m.end():])       # "maybe twice a week, maybe more": the open end is kept
        tail = (", possibly " if more and more.group("h") else " or ") + more.group("w").lower() if more else ""
        return f"{hedge}{approx}{n}x/{unit}{tail}", approx + n, unit, bool(hedge or more)
    m = FREQ_WORDS.search(clause)
    if m and not _denied_at(clause, m.start()):
        w = m.group("w").lower()
        w = {"every day": "daily", "every night": "nightly"}.get(w, re.sub(r"\s+times\s+(?:a|per|each)\s+", " times/", w))
        return w, "", "", False
    return "", "", "", False


class _Reader:
    def __init__(self, med_names=None):
        self.med_names = med_names
        self.affirmed: list[str] = []         # sections this sentence said the patient has
        self.r = Reading()
        self.conflicted: set[str] = set()    # boxes a sentence says both ways: never ticked
        self.cur: dict | None = None         # the record of the sentence being read
        self.own = ""                        # that sentence, with someone else's health blanked out
        self.unsure_onset = False            # "unclear when it started": no onset is filled in
        self.onset_doubt = False             # this sentence gives the onset two ways ("2 weeks ago, possibly 2 months")
        self.plain_desc: dict[str, str] = {}  # "lightheaded" said plainly: the description, unless one is described
        self.at = 0                          # where the clause being read starts in the sentence

    def _what(self, name: str) -> str:
        """What a box is, for a line to check: the section ("dizziness"), not the form's diagnosis label."""
        head = name[: -len("-denies")] if name.endswith("-denies") else name
        sec = next((s for s, h in SECTION_HEAD.items() if h == head), "")
        if name.startswith("ga-falls-"):
            return "falls"                                 # one question, two boxes: "Said both ways: falls"
        return SECTION_TITLE[sec].lower() if sec else FIELDS[name]["label"].lower()

    def tick(self, name: str, value, why: str) -> None:
        if name not in FIELDS or name in self.conflicted:
            return
        have = self.r.set.get(name)
        if have is not None and have != value:
            self.conflicted.add(name)
            self.r.set.pop(name, None)
            self.r.why.pop(name, None)
            self.unclear(f"Said both ways: {self._what(name)}", why)
            return
        self.r.set[name] = value
        self.r.why.setdefault(name, _short(why, 90))
        if self.cur is not None:
            self.cur["set"][name] = value

    def slot(self, name: str, value: str, why: str, what: str = "") -> None:
        """A text box from the words. Said two different ways, it is left empty and listed."""
        if not value or name not in FIELDS or name in self.conflicted:
            return
        have = self.r.set.get(name)
        if have is not None and have != value:
            self.conflicted.add(name)
            self.r.set.pop(name, None)
            self.r.why.pop(name, None)
            self.unclear(f"Said two ways: {what or FIELDS[name]['label'].lower()}", why)
            return
        self.r.set[name] = value
        self.r.why.setdefault(name, _short(why, 90))
        if self.cur is not None:
            self.cur["set"][name] = value

    def slots(self, section: str, clause: str, sentence: str, carried: bool = False) -> None:
        """Onset, description and frequency the clause gives for this symptom (from its own part of the clause)."""
        if _clause_doubt(clause):
            return
        title = SECTION_TITLE[section].lower()
        if not self.unsure_onset and not self.onset_doubt:
            self.slot(ONSET_FIELD[section], onset_value(clause, None if carried else TOPIC_RE[section]), sentence, f"{title} onset")
        self.slot(DESC_FIELD[section], describe(section, clause, self.own), sentence, f"{title} description")
        if section == "dizziness" and DESC_FIELD[section] not in self.plain_desc:
            plain = PLAIN_QUALITY.search(clause)
            if plain and not _denied_at(clause, plain.start()) and not _in(hedged_spans(clause), plain.start(), plain.end()):
                self.plain_desc[DESC_FIELD[section]] = DESC_NOUN.get(plain.group(0).lower().replace(" ", "-"), plain.group(0).lower())
        text, count, per, hedged = frequency(_owned(section, clause, self.med_names, self.own, self.at, meds=True))
        if section == "dizziness" and text:
            self.slot("DizzFreq", text, sentence, "how often the dizziness comes")
        elif section == "headache" and count and not hedged and per in ("week", "month"):
            self.slot("h-freq-wk" if per == "week" else "h-freq-month", count, sentence, "headaches per " + per)

    def unclear(self, what: str, sentence: str, kind: str = "check") -> None:
        """A line for the provider to check. One line per sentence: a specific doubt ("Hearing loss is uncertain")
        replaces a general one ("Unsure") about the same words."""
        if self.cur is not None:
            self.cur["unclear"].append(kind)
        line = f"{what}: “{_short(sentence)}”" if sentence else what
        if line in self.r.unclear:
            return
        if sentence:
            quote = f": “{_short(sentence)}”"
            for i, old in enumerate(self.r.unclear):
                if old.endswith(quote):
                    old_what = old[: -len(quote)]
                    if old_what in GENERAL_DOUBT and what not in GENERAL_DOUBT:
                        self.r.unclear[i] = line
                        return
                    if what in GENERAL_DOUBT:
                        return
        self.r.unclear.append(line)

    # one symptom's details inside a clause
    def details(self, section: str, clause: str, hedges) -> list[str]:
        """The boxes of one section the clause ticks, read only in the parts of the clause about that section."""
        assoc_text = _owned(section, clause, self.med_names, self.own, self.at, assoc=True)
        clause = _owned(section, clause, self.med_names, self.own, self.at)
        ticked = []
        for cue_name in ("worse", "when", "better", "occur", "dur"):
            group = "worse" if cue_name == "when" else cue_name
            for cue in CUES[cue_name].finditer(clause):
                if _in(hedges, cue.start(), cue.end()) or _denied_at(clause, cue.start()):
                    continue
                rest = clause[cue.end():]
                end = CUE_END.search(rest[1:])
                seg = rest[: end.start() + 1] if end else rest
                # "ibuprofen helped", "standing makes it worse": the few words before the cue count too
                lead = re.split(r"[,;]", clause[: cue.start()])[-1]
                lead = " ".join(lead.split()[-5:]) if group in ("worse", "better") and cue_name != "when" else ""
                if lead and DENIAL.search(lead):
                    lead = ""
                cands = sorted(_boxes(section, group), key=lambda n: -max((len(w) for w in _words_for(n, section)), default=0))
                for name in cands:
                    words = _words_for(name, section)
                    m = _find(words, seg)
                    if m and not _in(hedges, cue.end() + m.start(), cue.end() + m.end()) \
                            and not _denied_at(seg, m.start()):
                        ticked.append(name)
                        seg = seg[: m.start()] + " " * (m.end() - m.start()) + seg[m.end():]
                    elif lead and _find(words, lead) and not HEDGE_AHEAD.search(lead):
                        ticked.append(name)
                        lm = _find(words, lead)
                        lead = lead[: lm.start()] + " " * (lm.end() - lm.start()) + lead[lm.end():]
        for group in ("course", "assoc"):
            for name in _boxes(section, group):
                if name in ticked:
                    continue
                words = _words_for(name, section)
                if section != "dizziness" and name.endswith(("-parox", "course-parox")):
                    words = [w for w in words if w not in ("episodes", "spells")]      # the dizzy spells, not the tinnitus
                text = assoc_text if group == "assoc" else clause
                m = _find(words, text)
                if not m or _in(hedges, m.start(), m.end()):
                    continue
                if group == "assoc" and name.split("-")[-1] in OTHER_SYMPTOM.get(section, ()) and not ASSOC_CUE.search(text[: m.start()]):
                    continue                  # two symptoms listed together aren't an association
                if _denied_at(text, m.start()):
                    if group == "assoc":
                        twin = name + "-denies"
                        if twin in FIELDS:
                            ticked.append(twin)
                    continue
                ticked.append(name)
        # sides and headache locations: only next to the symptom or an ear / side / eye word, in a part that isn't
        # about the other ear being fine ("L ear normal"), and not hedged ("left ear, maybe both")
        if SIDE_DOUBT.search(clause):
            self.unclear("Side uncertain", self.cur["text"] if self.cur else clause, "hedge")
            return ticked
        chosen_exclusive = set()
        side_boxes = sorted(_boxes(section, "side"), key=lambda n: -max((len(w) for w in _words_for(n, section)), default=0))
        for name in side_boxes:
            exclusive = FIELDS[name].get("exclusive")
            if exclusive is not None and exclusive in chosen_exclusive:
                continue
            key = name.split("-", 1)[1] if "-" in name else name
            if key in SIDES:
                rx = re.compile(rf"\b(?:{SIDES[key]})\b", re.I)
                for m in rx.finditer(clause):
                    if _in(hedges, m.start(), m.end()) or _denied_at(clause, m.start()) or NOT_SIDE.search(clause[max(0, m.start() - 4): m.end() + 8]):
                        continue
                    part = clause[max(clause.rfind(",", 0, m.start()), clause.rfind(";", 0, m.start())) + 1:]
                    part = part[: (re.search(r"[,;]", part).start() if re.search(r"[,;]", part) else len(part))]
                    if SIDE_NORMAL.search(part) or re.search(r"\b(?:than|not|no|other)\s+(?:the\s+)?$", clause[: m.start()], re.I):
                        continue
                    if m.group(0) in ("AD", "AS", "AU") and ABBR_NOT_EAR.search(clause[: m.start()]):
                        continue              # "hx of AD" is Alzheimer's
                    around = clause[max(0, m.start() - 40): m.end() + 40]
                    near_side = re.search(rf"\b(?:{SIDES[key]})\b(?:\W+\w+){{0,{NEAR_WORDS - 2}}}?\W+{SIDE_CONTEXT}\b|\b(?:in|on|behind|over)\s+the\s+(?:{SIDES[key]})\b", around, re.I)
                    near_topic = TOPIC_RE[section].search(around)
                    if near_side or (near_topic and abs(near_topic.start() - (m.start() - max(0, m.start() - 40))) < 30):
                        ticked.append(name)
                        if exclusive is not None:
                            chosen_exclusive.add(exclusive)
                        break
            else:
                if key == "pulsatile" and re.search(r"\bnon[- ]?pulsatile\b", clause, re.I):
                    continue
                m = _find(_words_for(name, section), clause)
                if m and not _in(hedges, m.start(), m.end()) and not _denied_at(clause, m.start()):
                    ticked.append(name)
                    if exclusive is not None:
                        chosen_exclusive.add(exclusive)
        return ticked

    def _mention(self, section: str, clause: str, m: re.Match, hedges) -> str:
        """What the clause does with one symptom word: "affirm", "deny", "kind" (a kind of dizziness denied, "no
        vertigo"), "hedge", "check" (said in a way the reader won't guess at: "never free of", "ruled out", "not the
        dizziness"), or "skip" (not about having it: "no new headaches", "no dizziness today")."""
        before, after = clause[: m.start()], clause[m.end():]
        if _in(hedges, m.start(), m.end()):
            return "hedge"
        if TRAILING_NEG.match(after) or ELLIPTIC_BEFORE.search(before):
            return "check"
        if DOUBLE_NEG.search(before + m.group(0)) and re.search(r"\b(?:without|w/o|free of)\s+(?:\w+\s+){0,3}$", before, re.I):
            return "check"
        if STABLE_NEG.search(before):
            return "skip"
        denied = _denied_at(clause, m.start()) or bool(re.match(r"(?:'s|\W+(?:is|are|was|seems?))?\s*(?:fine|normal|ok|okay|good|intact|"
                                                                r"unchanged|the same)\b", after, re.I))
        if not denied:
            return "affirm"
        if NOT_NOW.match(after) or SCOPED.match(after):
            return "skip"
        if re.match(r"\s+(?:is\s+|was\s+)?(?:like|as if|as though)\b", after, re.I):
            return "skip"                     # "no, the roaring isn't like a heartbeat": denies what it is like
        if EXCEPT.search(after) or re.search(r"\b(?:no|not)\s+(?:longer|only)\b", before, re.I):
            return "skip"
        word = m.group(0)
        if section == "dizziness" and not DIZZ_WORD.search(word):
            return "kind"                     # "no vertigo" isn't "denies dizziness"
        if section == "gait" and not re.search(r"imbalance|unsteady|balance|gait", word, re.I):
            return "skip"
        if section == "hearing" and not re.search(r"hearing|deaf|hear\b|HL", word, re.I):
            return "skip"
        return "deny"

    def clause(self, clause: str, topics: list[str], sentence: str) -> None:
        hedges = hedged_spans(clause)
        own = _without_pmh(clause)
        for section in topics:
            head = SECTION_HEAD[section]
            hits = [m for _p, k, m in _topic_hits(own) if k == section]
            if not hits:
                # carried over from the sentence before ("Worse standing up ...", "Started maybe January"): details only
                self.apply(self.details(section, clause, hedges), clause, sentence)
                if not DENIAL.search(clause):
                    self.slots(section, clause, sentence, carried=True)
                continue
            said = [(m, self._mention(section, clause, m, hedges)) for m in hits]
            kinds = {k for _m, k in said}
            if "affirm" not in kinds:
                if "deny" in kinds:
                    if section == "dizziness" and IMBALANCE_SAID.search(sentence):
                        self.unclear("Check", sentence)
                        continue              # "not dizzy, I just feel wobbly": the form's dizziness includes imbalance
                    self.tick(head + "-denies", True, sentence)
                    self.tick(head, False, sentence)
                elif "hedge" in kinds:
                    self.unclear(f"{SECTION_TITLE[section]} is uncertain", sentence, "hedge")
                elif "check" in kinds:
                    self.unclear("Check", sentence)
                if "kind" in kinds:
                    self.apply([n for n in self.details(section, clause, hedges) if n.endswith("-denies")], clause, sentence)
                continue
            if section == "gait":
                words = [m.group(0) for m, k in said if k == "affirm"]
                if not any(re.search(r"gait|imbalance|unsteady|unsteadiness|off[- ]balance|balance|wobbly|veer|shuffl|legs?\s+(?:feel|feels|felt)", w, re.I)
                           for w in words):
                    continue
                if "dizziness" in _topics(own) and all(_gait_as_dizziness(clause, m.start(), m.end()) for m, k in said if k == "affirm"):
                    continue                  # "dizziness, now more of a constant imbalance": what the dizziness is like
            if section == "dizziness" and FALL_EVENT.search(sentence) and FALLS_YES.search(sentence):
                if self.cur is not None:
                    self.cur["fall_dizzy"] = True
                continue                      # "once lightheaded getting up at night" before a fall: that fall
            if "deny" in kinds:                # said both ways in one breath: listed, neither ticked
                for n in (head, head + "-denies"):
                    self.conflicted.add(n)
                    self.r.set.pop(n, None)
                self.unclear(f"Said both ways: {SECTION_TITLE[section].lower()}", sentence)
                continue
            self.tick(head, True, sentence)
            self.tick(head + "-denies", False, sentence)
            self.affirmed.append(section)
            self.apply(self.details(section, clause, hedges), clause, sentence)
            self.slots(section, clause, sentence)

    def apply(self, names: list[str], clause: str, sentence: str) -> None:
        for name in dict.fromkeys(names):
            if name.endswith("-denies"):
                self.tick(name, True, sentence)
                self.tick(name[: -len("-denies")], False, sentence)
                continue
            self.tick(name, True, sentence)
            text_box = MED_TEXT.get(name)
            if text_box and not self.r.set.get(text_box):
                named = find_meds(clause, self.med_names) or [w for w in re.findall(r"[A-Za-z]+", clause) if w.lower() in SIMPLE_MEDS]
                if named:
                    self.r.set[text_box] = ", ".join(n.lower() for n in dict.fromkeys(named))

    def falls(self, s: str, sentence: str = "") -> None:
        sentence = sentence or s
        hedges = hedged_spans(s)
        if NEAR_FALL.search(s):
            self.unclear("Near-fall, not a fall", sentence)
            return
        no = FALLS_NO.search(s)
        yes = FALLS_YES.search(s)
        if no and EXCEPT.search(s[no.end():]):
            no, yes = None, no
        if yes and not (no and no.start() <= yes.start() <= no.end()):
            if _in(hedges, yes.start(), yes.end()):
                self.unclear("Falls are uncertain", sentence, "hedge")
            elif not _denied_at(s, yes.start()) or (no is None):
                self.tick("ga-falls-yes", True, sentence)
                self.tick("ga-falls-no", False, sentence)
                return
        if no:
            if _in(hedges, no.start(), no.end()):
                self.unclear("Falls are uncertain", sentence, "hedge")
            else:
                self.tick("ga-falls-no", True, sentence)
                self.tick("ga-falls-yes", False, sentence)

    def history(self, s: str, sentence: str = "") -> None:
        """Past history and habits. `s` has someone else's health blanked out already; what is over ("had high blood
        pressure with my last pregnancy but it went away") isn't the patient's history now."""
        sentence = sentence or s
        own, past = _without_past(s)
        hedges = hedged_spans(s)
        for name, rx in PMH_RE.items():
            for m in rx.finditer(s):
                if own[m.start(): m.end()].strip() != m.group(0).strip():
                    self.unclear(f"{PMH_LABEL[name]}: in the past, check it is still the case", sentence)
                elif _in(hedges, m.start(), m.end()) or CORRECTION.match(s[m.end():]):
                    self.unclear(f"{PMH_LABEL[name]} is uncertain", sentence, "hedge")
                elif _denied_at(s, m.start()):
                    pass                           # "no diabetes": the form has no box for it
                elif not re.search(r"\b(?:medications?|meds?|pills?)\b", s[m.end(): m.end() + 14], re.I):
                    self.tick(name, True, sentence)
                break
        if SOCIAL_DENIES.search(s):
            self.tick("social-denies", True, sentence)
            return
        for name, rx in SOCIAL.items():
            m = rx.search(s)
            if not m or _in(hedges, m.start(), m.end()):
                continue
            if _denied_at(s, m.start()) and not _own_amount(s, m.start()):
                continue
            if name == "social-tobacco" and "social-tobacco-remote" in self.r.set:
                continue
            if name == "social-etoh" and ("social-etoh-remote" in self.r.set or "social-etoh-history" in self.r.set):
                continue
            if name.startswith("social-etoh") and TRIGGER_LEAD.search(s[: m.start()]):
                continue                           # "Triggers: poor sleep, red wine"
            self.tick(name, True, sentence)

    def gait_denials(self, s: str, sentence: str = "") -> None:
        """Denied leg weakness, numbness, bowel or bladder trouble. "No weakness except in her right hand" isn't a
        denial (it is listed), and weakness of a hand or arm isn't the legs'."""
        sentence = sentence or s
        hedges = hedged_spans(s)
        for m in DENIAL.finditer(s):
            scope = s[m.end():]
            end = re.search(r"[.;]", scope)
            whole = scope[: end.start()] if end else scope          # the rest of the clause, past any "except"
            stop = re.search(r"\b(?:but|although|though|however)\b|[.;]", scope)
            scope = scope[: stop.start()] if stop else scope
            excepted = EXCEPT.search(scope)
            for name, words in GAIT_DENY.items():
                w = re.search(rf"(?<![a-z])(?:{words})(?![a-z])", scope, re.I)
                if not w or _in(hedges, m.end() + w.start(), m.end() + w.end()):
                    continue
                if excepted and excepted.start() > w.start():
                    self.unclear("Check", sentence)
                    continue
                if name in ("ga-assoc-bowel-denies", "ga-assoc-bladder-denies") or re.search(r"\b(?:legs?|lower extremit|ble|feet)\b", whole, re.I) \
                        or not re.search(r"\b(?:arms?|hands?|face|grip|fingers?)\b", whole, re.I):
                    self.tick(name, True, sentence)
                    self.tick(name[: -len("-denies")], False, sentence)

    def opening(self, first: str) -> None:
        m = re.search(r"(?<![\d/])(\d{1,3})\s*(?:-?\s*(?:years?|yrs?)[- ]?old|y/?o\b|yo\b|y\.o\.)", first, re.I)
        if not m or not 0 < int(m.group(1)) < 120:
            return
        self.r.facts["age"] = m.group(1)
        after = first[m.end(): m.end() + 40]
        sex = re.search(r"\b(female|woman|lady|girl|f|male|man|gentleman|boy|m)\b", after, re.I)
        if sex and not (len(sex.group(1)) == 1 and sex.group(1).islower()):
            self.r.facts["sex"] = "female" if sex.group(1).lower() in ("female", "woman", "lady", "girl", "f") else "male"
        hand = re.search(r"\b(right|left)[- ]handed\b|\b([RL])HD\b", first, re.I)
        if hand:
            self.r.facts["hand"] = (hand.group(1) or hand.group(2))[0].upper()


def _own_amount(s: str, pos: int) -> bool:
    """A habit later in a list, with its own amount, past the denial that starts the list ("doesn't smoke, wine on
    weekends")."""
    denials = [m for m in DENIAL.finditer(s[:pos])]
    if not denials or not re.search(r"[,;]", s[denials[-1].end(): pos]):
        return False
    start = max(s.rfind(",", 0, pos), s.rfind(";", 0, pos)) + 1
    stop = re.search(r"[,;]", s[pos:])
    item = s[start: pos + (stop.start() if stop else len(s) - pos)]
    if re.match(r"\s*(?:but|although|though|however|yet)\b", item, re.I):
        return False
    return bool(AMOUNT.search(item)) and not DENIAL.search(item)


def _clause_spans(s: str) -> list[tuple[int, int]]:
    """Where the clauses of a sentence are: split before "but" / "although", at ", has / reports / w/ ..." and where
    the patient answers a companion (", he says he's fine")."""
    cuts = [(m.start(), m.end()) for rx in (CONTRAST, AFFIRM_START, PATIENT_TURN) for m in rx.finditer(s)]
    spans, pos = [], 0
    for a, b in sorted(cuts):
        if a < pos:
            continue
        spans.append((pos, a))
        pos = b
    spans.append((pos, len(s)))
    out = []
    for a, b in spans:
        while a < b and s[a] in " ,":
            a += 1
        while b > a and s[b - 1] in " ,":
            b -= 1
        if b > a:
            out.append((a, b))
    return out


def topics_of(values: dict) -> list[str]:
    """The symptom sections the note already has as the complaint: the ones the chief complaint names (none, when it
    names something else, like a tremor), else the ones ticked present. Words that name no symptom ("Started maybe
    January") belong to the complaint, not to every symptom the note has."""
    cc = str(values.get("ChiefComplaint") or "").strip()
    if cc:
        return [sec for sec in SECTION_HEAD if TOPIC_RE[sec].search(cc)]
    return [sec for sec, head in SECTION_HEAD.items() if values.get(head) is True]


# A second onset in the same sentence: "onset 2 weeks ago, possibly 2 months", "x3 weeks, though husband thinks longer".
ONSET_ALT = re.compile(rf"[,;]\s*(?:or\s+|though\s+|but\s+)?(?:maybe|possibly|perhaps|probably|or)\s+(?:~\s*|about\s+|around\s+)?"
                       rf"(?:\d+|{wording.NUM_WORD}|a few|several|a couple)\s*(?:days?|weeks?|wks?|months?|mos?|years?|yrs?)\b|"
                       r"\b(?:thinks?|says?|feels?|believes?|thought)\s+(?:it'?s\s+(?:been\s+)?|it\s+(?:has\s+)?been\s+)?(?:longer|shorter|earlier|later)\b", re.I)
CHRONIC_BOX = {"dizziness": "course-chronic", "hearing": "hlcourse-chronic", "tinnitus": "t-course-chronic", "gait": "ga-course-chronic",
               "headache": "h-course-chronic"}
# The boxes and text boxes that belong to a section, by name.
SECTION_BOXES = {"dizziness": ("assoc-", "worse-", "course-", "occur-", "duration-", "better-", "adl-", "DizzFreq", "DizzOnsetDate", "DizzyDesc"),
                 "hearing": ("hl-", "hlcourse-", "hlbetter-", "hlworse-", "hlassoc-", "hldur-", "HLOnsetDate", "HLdesc", "hl-"),
                 "tinnitus": ("t-", "TinnitusOnsetDate", "tinnitus-descr"),
                 "gait": ("ga-assoc-", "ga-course-", "ga-better-", "ga-worse-", "ga-use", "GAOnsetDate", "gaitataxia-descr"),
                 "headache": ("h-", "HOnsetDate", "headache-descr")}


def section_of(name: str) -> str:
    """The symptom section a box belongs to ("worse-headturn" -> "dizziness"), or "" for history, meds and the rest."""
    for sec, head in SECTION_HEAD.items():
        if name in (head, head + "-denies"):
            return sec
    for sec, prefixes in SECTION_BOXES.items():
        if any(name.startswith(p) for p in prefixes):
            return sec
    return ""


def topic_states(reading: "Reading | None", values: dict, complaint: "list[str] | None" = None) -> list[dict]:
    """Each symptom section as the HPI has it: present, denied, mentioned (its boxes come up, the symptom itself
    isn't named) or absent, with the words that say so. The HPI decides; boxes the provider ticked by hand count too."""
    rset = reading.set if reading else {}
    whys = reading.why if reading else {}
    complaint = complaint or []
    out = []
    for sec, head in SECTION_HEAD.items():
        deny = head + "-denies"
        if rset.get(head) is True or sec in complaint or values.get(head) is True:
            state, why = "present", whys.get(head, "")
        elif rset.get(deny) is True or values.get(deny) is True:
            state, why = "denied", whys.get(deny, "")
        elif any(section_of(n) == sec and v not in (False, "", None) for n, v in rset.items()):
            state, why = "mentioned", next((whys[n] for n in rset if section_of(n) == sec and whys.get(n)), "")
        else:
            state, why = "absent", ""
        out.append({"id": sec, "title": SECTION_TITLE[sec], "state": state, "why": why})
    return out


def _months(onset: str) -> float | None:
    """How long ago an onset slot is, in months ("2 weeks ago" -> 0.5), or None when it doesn't say."""
    if re.match(r"longstanding", onset or "", re.I):
        return 999.0
    m = re.search(r"(\d+(?:\.\d+)?)(?:\s*(?:-|or(?: possibly)?)\s*(\d+(?:\.\d+)?))?\+?\s*(day|week|month|year)s?\b", onset or "")
    if not m:
        return None
    n = float(m.group(2) or m.group(1))
    return n * {"day": 1 / 30, "week": 0.25, "month": 1, "year": 12}[m.group(3)]


# A sentence of a word or three, nothing but a symptom: after a denial of it, an echo of that denial.
ECHO = re.compile(r"^\W*(?:[A-Za-z'/-]+\W+){0,2}[A-Za-z'/-]+\W*$")


def _disputed(sents: list[str]) -> dict[int, set[str]]:
    """A companion's report the patient disputes in the next sentence ("Wife says he's unsteady. He says he's fine."):
    for both sentences, the subjects neither is ticked for."""
    out: dict[int, set[str]] = {}
    for i in range(len(sents) - 1):
        a, b = sents[i], sents[i + 1]
        if COMPANION.search(a) and re.match(r"^\W*(?:but\s+)?(?:she|he|pt|patient)\b", b, re.I) and PATIENT_SAYS.search(b) \
                and (PATIENT_DISPUTES.search(b) or _polarity(a) != _polarity(b)):
            subjects = _subjects(a) | _subjects(b)
            out.setdefault(i, set()).update(subjects)
            out.setdefault(i + 1, set()).update(subjects)
    return out


def read_notes(text: str, meds: "list[str] | None" = None, topics: "list[str] | None" = None) -> Reading:
    """What typed notes say plainly, box by box, and what they leave unclear. `topics` are the complaint's sections
    when the text itself doesn't name one first ("How long does it last?")."""
    rd = _Reader(meds)
    sents = sentences(text)
    if not sents:
        return rd.r
    rd.opening(sents[0])
    rd.unsure_onset = bool(ONSET_UNSURE.search(_clean(text)))
    last_topics: list[str] = list(topics or [])
    main_topics: list[str] = list(topics or [])
    complaint: list[str] = []              # the sections the first sentence is about
    disputed = _disputed(sents)
    stopped: set[str] = set()              # medications the notes say were stopped
    for i, raw in enumerate(sents):
        s = _as_read(raw)                  # "Headache? Denies." read as "Denies headache."
        rd.affirmed = []
        rec = {"text": raw, "topics": list(last_topics or main_topics), "set": {}, "unclear": [], "cut": bool(CUT_END.search(raw))}
        rd.r.sentences.append(rec)
        rd.cur = rec
        if i and ECHO.match(raw) and _topics(raw) and set(_topics(raw)) <= set(_topics(sents[i - 1])) and DENIAL.search(sents[i - 1]):
            continue                       # "No headaches with these. Headaches.": the same answer said again
        if i == len(sents) - 1 and _dangles(raw):
            rec["cut"] = True
        rd.onset_doubt = bool(ONSET_ALT.search(s))
        if rd.onset_doubt and onset_value(s, anywhere=True):
            rd.unclear("Onset unclear", raw, "hedge")
        spans = _clause_spans(s)
        # What the patient said, to read for boxes: someone else's health blanked out, and the last clause of a thought
        # that stops mid-phrase ("Symptoms last seconds to...") left unread.
        own = _without_family(s)
        if rec["cut"] and MID_PHRASE.search(CUT_END.sub("", s)) and not COMPLETE_END.search(s):
            frag = max([a for a, _b in spans[1:]] + [s.rfind(",") + 1, s.rfind(";") + 1, 0])
            own = own[:frag] + " " * (len(s) - frag)
        rd.own = own
        rd.falls(own, raw)
        rd.history(own, raw)
        rd.gait_denials(own, raw)
        clauses = [s[a:b] for a, b in spans]
        # Two halves of a sentence that disagree about the same thing ("denies weakness but later says grip feels
        # weaker"), or a companion the patient contradicts ("husband says she's unsteady, she says no"): neither half
        # is ticked for it.
        blocked: set[str] = set(disputed.get(i, set()))
        if blocked:
            rd.unclear("Conflicting", raw)
        for a, b in zip(clauses, clauses[1:]):
            contrast = re.match(r"(?:but|although|though|however|whereas|yet|while)\b", b, re.I)
            companion = ((COMPANION.search(a) and PATIENT_SAYS.search(b)) or (contrast and COMPANION.search(b) and PATIENT_SAYS.search(a)))
            if companion and (_polarity(a) != _polarity(b) or NORMAL_WORDS.search(a + " " + b) or PATIENT_DISPUTES.search(b)):
                blocked |= _subjects(a) | _subjects(b)
                rd.unclear("Conflicting", raw)
                continue
            if not contrast:
                continue
            shared = _subjects(a) & _subjects(b)
            if shared and _polarity(a) != _polarity(b):
                blocked |= shared
                rd.unclear("Conflicting", raw)
            elif REPORTED.search(b) and (_polarity(a) != _polarity(b) or re.search(r"\b(?:says?|reports?|later|then|insists?)\b", b, re.I)):
                rd.unclear("Check", raw)
        if "vertigo" in blocked:
            blocked.add("dizziness:vertigo")
        if re.search(r"\b(?:says?|states?|reports?|insists?|feels?|thinks?)\b", s, re.I) and re.search(r"\b(?:fine|normal|nothing wrong|no problems?|just stress|mostly stress)\b", s, re.I):
            rd.unclear("Check", raw)
        if _clause_doubt(s):
            rd.unclear("Unsure", raw)
        elif hedged_spans(s) and not _topics(s):
            rd.unclear("Unsure", raw, "hedge")
        topics_here = _topics(own)
        for k, (a, b) in enumerate(spans):
            c, had_past = _without_past(own[a:b])
            gone = set(_topics(own[a:b])) - set(_topics(c))
            if had_past and gone:
                rd.unclear("Past episode, not current", raw, "past")
                rd.r.past += [t for t in gone if t not in rd.r.past]
            if not c.strip():
                continue
            if k and ELLIPTIC_NOT.match(c) and CUES["better"].search(own[spans[k - 1][0]: spans[k - 1][1]]):
                rd.unclear("Check", raw)
                continue                          # "helps the headache but not the dizzy": not a denial of dizziness
            here = _topics(c)
            topics = [t for t in here if t not in blocked
                      and not (t == "dizziness" and "dizziness:vertigo" in blocked and not DIZZ_WORD.search(c))]
            carry = (main_topics or last_topics)[:1]
            if not here and not (_subjects(c) & blocked):
                if _is_med_part(c, meds) and not CUES["better"].search(c):
                    topics = []                   # "ASA 81 mg daily": the dose, not a symptom's frequency
                elif EVENT_NOUN.search(c) and not MAIN_SUBJECT.match(c):
                    # "1 brief episode turning head, much improved": that event, which is dizziness when the notes say so
                    topics = ["dizziness"] if "dizziness" in (main_topics + last_topics) and "dizziness" not in blocked else []
                elif MAIN_SUBJECT.match(c) and (main_topics or last_topics):
                    topics = [t for t in (main_topics or last_topics) if t not in blocked]
                    if EVENT_NOUN.match(c.strip()) or re.match(r"\W*(?:the\s+)?(?:episodes?|attacks?|spells?)\b", c, re.I):
                        topics = [t for t in topics if t == "dizziness"] or topics[:1]
                elif last_topics and (GENERIC_SUBJECT.match(c) or (len(re.findall(r"[A-Za-z']+", c)) <= 6 and not _subjects(c))):
                    topics = [t for t in last_topics[:1] if t not in blocked]
                elif carry and not _subjects(c) and (CUES["better"].search(c) or CUES["worse"].search(c)):
                    topics = [t for t in carry if t not in blocked]           # a trigger goes with the complaint
                elif last_topics and ONSET_LEAD.match(c) and not _subjects(c):
                    topics = [t for t in last_topics[:1] if t not in blocked]  # "Started maybe January."
            if topics:
                rd.at = a
                rd.clause(c, topics, raw)
        for subject, boxes in (("weak", ("ga-assoc-weakness-denies",)), ("numb", ("ga-assoc-sensory-denies",)),
                               ("fall", ("ga-falls-yes", "ga-falls-no"))):
            if subject in blocked:
                for n in boxes:
                    rd.r.set.pop(n, None)
                    rd.conflicted.add(n)
        for sec in [t for t in blocked if t in SECTION_HEAD]:
            for n in (SECTION_HEAD[sec], SECTION_HEAD[sec] + "-denies"):
                if rec["set"].get(n) is not None:
                    rd.r.set.pop(n, None)
                    rd.conflicted.add(n)
        # "no tinnitus. well, maybe a little on the left": the denial before is taken back
        if i and CORRECTION.match(raw) and (hedged_spans(s) or rd.affirmed):
            before = rd.r.sentences[i - 1]["set"]
            for sec, head in SECTION_HEAD.items():
                if before.get(head + "-denies") is True and (not _topics(s) or sec in _topics(s)):
                    for n in (head, head + "-denies"):
                        rd.r.set.pop(n, None)
                        rd.conflicted.add(n)
                    rd.unclear(f"Said both ways: {SECTION_TITLE[sec].lower()}", raw)
        # the complaint's onset (page 1): from the opening sentence, or a "Started ..." sentence right after it
        if not rd.unsure_onset and not rd.onset_doubt and "OnsetDate" not in rd.r.set:
            if i == 0 and rd.r.facts.get("age"):
                first = SEGMENT.search(own)
                bare = ONSET_BARE_END.search(first.group(0)) if first else None
                found = onset_value(own, anywhere=False) or (onset_value(first.group(0), anywhere=True) if first else "")
                rd.slot("OnsetDate", found or (_span_text(bare) if bare else ""), raw, "onset")
            elif i <= 2 and not _topics(own) and ONSET_LEAD.match(own) and not _clause_doubt(own):
                rd.slot("OnsetDate", onset_value(own), raw, "onset")
        if i == 0:
            complaint = list(dict.fromkeys(rd.affirmed))
        # what the patient has carries over to the next sentence ("Worse standing up."); a denial doesn't
        if rd.affirmed:
            last_topics = list(dict.fromkeys(rd.affirmed))
            main_topics = main_topics or last_topics
        elif topics_here and not main_topics and not any(SECTION_HEAD[t] + "-denies" in rd.r.set for t in topics_here):
            last_topics = topics_here
        for part in re.split(r"[,;]", s):
            if MED_STOPPED.search(part):
                stopped.update(n.lower() for n in find_meds(part, meds))
        if MED_CHANGE.search(s) or GENERIC_MEDS.search(s) or (MED_STOPPED.search(s) and find_meds(s, meds)):
            line = _short(raw)
            if line not in rd.r.med_notes:
                rd.r.med_notes.append(line)
    rd.cur = None
    _settle(rd, sents, complaint)
    last = sents[-1]
    if _dangles(last) or (text.rstrip().endswith(("...", "…")) and not COMPLETE_END.search(text)):
        rd.r.unfinished = _short(last)
    rd.r.meds = [m for m in find_meds(text, meds) if m.lower() not in stopped]
    # drop "False" twins that only clear a box nobody ticked
    rd.r.set = {k: v for k, v in rd.r.set.items() if v is not False or rd.r.set.get(_twin(k))}
    return rd.r


def _settle(rd: "_Reader", sents: list[str], complaint: list[str]) -> None:
    """What only the whole note decides: the visit's onset, a plainly said description, a "constant" that is too new
    to be chronic, a dizzy feeling that was only part of a fall, and no details for a symptom that is denied or said
    both ways."""
    r = rd.r
    if complaint and "OnsetDate" not in r.set and "OnsetDate" not in rd.conflicted and ONSET_FIELD[complaint[0]] in r.set:
        r.set["OnsetDate"] = r.set[ONSET_FIELD[complaint[0]]]          # the main complaint's onset is the visit's
        r.why["OnsetDate"] = r.why.get(ONSET_FIELD[complaint[0]], "")
    for name, value in rd.plain_desc.items():
        if r.set.get("dizziness") is True and name not in r.set and name not in rd.conflicted:
            r.set[name] = value
            r.why[name] = next((rec["text"] for rec in r.sentences if PLAIN_QUALITY.search(rec["text"])), "")
    for sec, box in CHRONIC_BOX.items():
        months = _months(str(r.set.get(ONSET_FIELD[sec], "") or (r.set.get("OnsetDate", "") if complaint[:1] == [sec] else "")))
        if r.set.get(box) is True and not re.search(r"\bchronic\b", r.why.get(box, ""), re.I) and months is not None and months < 3:
            r.set.pop(box, None)                   # "constant, onset 2 weeks ago" isn't chronic
            r.why.pop(box, None)
    if any(rec.get("fall_dizzy") for rec in r.sentences) and r.set.get("dizziness") is not True:
        rec = next(rec for rec in r.sentences if rec.get("fall_dizzy"))
        rd.cur = None
        rd.unclear("Dizziness only with a fall, not ticked", rec["text"])
    for sec, head in SECTION_HEAD.items():
        if head in rd.conflicted or r.set.get(head + "-denies") is True:
            dropped = [n for n in list(r.set) if n.startswith(SECTION_BOXES[sec]) and not n.endswith("-denies")
                       and not n.startswith("ga-falls-") and r.set.get(n) not in (False, None)]
            for n in dropped:
                r.set.pop(n, None)
                r.why.pop(n, None)
            if dropped:
                what = "said both ways" if head in rd.conflicted else "denied"
                rd.unclear(f"{SECTION_TITLE[sec]} is {what}; not ticked: " + ", ".join(FIELDS[n]["label"].lower() if FIELDS[n]["type"] == "checkbox"
                                                                                      else str(FIELDS[n]["label"]).lower() for n in dropped), "")
    if "OnsetDate" in r.set and complaint and (SECTION_HEAD[complaint[0]] in rd.conflicted or r.set.get(SECTION_HEAD[complaint[0]] + "-denies")):
        r.set.pop("OnsetDate", None)


def _twin(name: str) -> str:
    if name.endswith("-denies"):
        return name[: -len("-denies")]
    if name == "ga-falls-yes":
        return "ga-falls-no"
    if name == "ga-falls-no":
        return "ga-falls-yes"
    return name + "-denies"


def find_meds(text: str, names: "list[str] | None" = None) -> list[str]:
    """Medication names (generic or brand) that appear in the text, as the list knows them."""
    if not names:
        from . import medlist
        names = medlist.all_names()
    low = " " + re.sub(r"[^a-z0-9\-]+", " ", (text or "").lower()) + " "
    found = []
    for n in names:
        key = n.lower()
        if len(key) >= 4 and key not in NOT_MEDS and f" {key} " in low and n not in found and not any(key in f.lower() for f in found):
            if key in WORD_MEDS and not _taken(text or "", key):
                continue                  # "Daughter Heather says ...", "eyes don't align"
            found.append(n)
    return found


# Drug names that are also everyday words or first names: a medication only when it is taken or dosed.
WORD_MEDS = {"align", "amethyst", "heather", "ocean", "portia", "sonata", "intermezzo", "soma", "centrum", "flector", "trental", "neffy",
             "hemocyte"}


def _taken(text: str, key: str) -> bool:
    """The name is used as a medication: a dose after it, or "takes / on / started / stopped / tried" just before it,
    and not right after a family word ("daughter Heather")."""
    for m in re.finditer(rf"(?<![a-z]){re.escape(key)}(?![a-z])", text, re.I):
        before, after = text[: m.start()], text[m.end():]
        if re.search(rf"\b{FAMILY_MEMBER}\s*,?\s*$", before, re.I):
            continue
        if re.match(r"\s*\d+(?:\.\d+)?\s*(?:mg|mcg|g|ml|units?)?\b", after, re.I) or \
                re.search(r"\b(?:takes|taking|took|take|on|started|starting|stopped|stopping|uses|using|tried|trying|prescribed|"
                          r"continue|continues|refill)\s+(?:\w+\s+)?$", before, re.I):
            return True
    return False


# On the drug list, but in a story they are habits, not medications ("caffeine 3-4 cups").
NOT_MEDS = {"caffeine", "nicotine", "alcohol", "oxygen", "water", "cannabis", "marijuana", "probiotic"}


# ---------------------------------------------------------------------------------------------------- notes or a visit?

SPEAKER = re.compile(r"^\s*(?:\[?\d{1,2}:\d{2}(?::\d{2})?\]?\s*)?(?P<who>dr\.?(?:\s+\w+)?|doctor|provider|physician|clinician|pa|np|nurse|ma|"
                     r"patient|pt|speaker\s*\d+|spk\s*\d+|s\d|interviewer|interviewee|"
                     r"wife|husband|spouse|partner|daughter|son|mother|mom|father|dad|caregiver|companion|family|friend|sister|brother)"
                     r"\s*(?:\(\w+\))?\s*:\s*", re.I)


def looks_like_visit(text: str) -> bool:
    """A conversation (speaker labels, or questions and first-person answers) rather than the provider's notes."""
    lines = [ln for ln in _clean(text).split("\n") if ln.strip()]
    labelled = sum(1 for ln in lines if SPEAKER.match(ln))
    if labelled >= 2:
        return True
    t = _clean(text)
    questions = len(re.findall(r"\?", t))
    you = len(re.findall(r"\b(?:you|your|you're|you've)\b", t, re.I))
    me = len(re.findall(r"\b(?:i|i'm|i've|my|me)\b", t, re.I))
    return questions >= 3 and you >= 3 and me >= 3
