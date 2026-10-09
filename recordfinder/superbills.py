"""Find a patient's superbill inside the day's scanned batches.

The front desk scans each provider's superbills for a day into one PDF named with the provider
code and the date ("EC 09.24.26.pdf", "TY EMG NCV 09.24.26.pdf"), saved in "<Month YYYY>" folders
and moved into "Uploaded" once billing has them. Inside a batch the first page(s) are the
provider's printed schedule, then every patient's superbill is followed by that patient's other
pages (insurance cards, ID, referrals, intake forms) until the next superbill. That makes each
superbill + following pages a "packet", and the printed pages in a packet identify the patient
even though the name on the superbill itself is handwritten.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from difflib import SequenceMatcher

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
          "October", "November", "December"]

# --------------------------------------------------------------------------- batch files

_DATE_IN_NAME = re.compile(r"(?=(?<!\d)(\d{1,2})\s*[.\- ]\s*(\d{1,2})\s*[.\- ]\s*(\d{4}|\d{2})(?!\d))")
_AMENDED = re.compile(r"amend|revis|correct|redo", re.I)


@dataclass
class Batch:
    path: str
    code: str      # provider/visit code before the date, e.g. "EC", "TY EMG NCV", "MP 2"
    day: date
    note: str      # anything after the date, e.g. "Amended"

    @property
    def name(self) -> str:
        return os.path.basename(self.path)

    @property
    def amended(self) -> bool:
        return bool(_AMENDED.search(self.note))


def norm_code(code: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", code.upper())


def parse_batch_name(path: str) -> Batch | None:
    name = os.path.basename(path)
    stem, ext = os.path.splitext(name)
    if ext.lower() != ".pdf":
        return None
    for m in _DATE_IN_NAME.finditer(stem):
        mo, dd, yy = int(m.group(1)), int(m.group(2)), m.group(3)
        year = int(yy) + (2000 if len(yy) == 2 else 0)
        if not 2015 <= year <= 2045:
            continue  # "MP 2 06.01.26" first tries "2 06.01"
        try:
            day = date(year, mo, dd)
        except ValueError:
            continue
        code = stem[:m.start()].strip(" -_,.")
        # the match is a zero-width lookahead (so overlapping dates are tried); find where the date ends
        tail = re.match(r"\d{1,2}\s*[.\- ]\s*\d{1,2}\s*[.\- ]\s*(?:\d{4}|\d{2})", stem[m.start():])
        return Batch(path, code, day, stem[m.start() + tail.end():].strip(" -_,."))
    return None


def candidate_folders(root: str, day: date) -> list[str]:
    """Month folders that could hold a batch for this day (current month sits at the root,
    older months under the year; late-month batches can land in the next month's folder)."""
    out = []
    for d in (day, (day.replace(day=1) + timedelta(days=32)).replace(day=1)):
        month = f"{MONTHS[d.month - 1]} {d.year}"
        for base in (root, os.path.join(root, str(d.year))):
            out += [os.path.join(base, month), os.path.join(base, month, "Uploaded")]
    out.append(root)
    return [p for p in dict.fromkeys(out) if os.path.isdir(p)]


def find_batches(root: str, day: date, code: str = "") -> list[Batch]:
    """Batches for a date of service. With a code, exact code matches come first, then
    batches that start with it ("TY" also finds "TY EMG NCV"); without, every batch that day."""
    want = norm_code(code)
    found: dict[str, Batch] = {}
    for folder in candidate_folders(root, day):
        try:
            names = os.listdir(folder)
        except OSError:
            continue
        for n in names:
            b = parse_batch_name(os.path.join(folder, n))
            if b is None or b.day != day or b.path in found:
                continue
            if want and not norm_code(b.code).startswith(want):
                continue
            found[b.path] = b
    return sorted(found.values(), key=lambda b: (norm_code(b.code) != want, b.amended, len(b.code), b.name))


# --------------------------------------------------------------------------- pages and packets

_FIVE_DIGIT = re.compile(r"(?<![\d.\-/])(\d{5})(?![\d.\-/])")
_ICD10 = re.compile(r"\b[A-TV-Z]\d{2}\.[0-9A-Z]{1,4}\b")


def is_superbill(text: str) -> bool:
    """A superbill is a dense grid of printed procedure (CPT) and diagnosis (ICD-10) codes."""
    cpt = {c for c in _FIVE_DIGIT.findall(text) if 10000 <= int(c) <= 99607}
    return len(cpt) >= 10 and len(set(_ICD10.findall(text))) >= 15


@dataclass
class Packet:
    index: int          # order within the batch, 0-based
    superbill: int      # page index (0-based) of the superbill
    pages: list[int]    # superbill first, then the patient's other pages


def split_packets(texts: list[str]) -> tuple[list[int], list[Packet]]:
    """(cover pages, packets). Cover pages come before the first superbill (the printed schedule)."""
    sb = [i for i, t in enumerate(texts) if is_superbill(t)]
    if not sb:
        return list(range(len(texts))), []
    packets = [Packet(n, s, list(range(s, sb[n + 1] if n + 1 < len(sb) else len(texts)))) for n, s in enumerate(sb)]
    return list(range(sb[0])), packets


# --------------------------------------------------------------------------- matching a patient

@dataclass
class PatientQuery:
    first: str = ""
    last: str = ""
    account: str = ""
    dob: date | None = None
    nicknames: list[str] = field(default_factory=list)
    payers: list[str] = field(default_factory=list)   # from the booking, e.g. ["MCR", "Anthem BC"]

    def describe(self) -> str:
        bits = [f"{self.first} {self.last}".strip(), f"acct {self.account}" if self.account else "",
                f"DOB {self.dob:%m/%d/%Y}" if self.dob else ""]
        return ", ".join(b for b in bits if b)


def _fold(s: str) -> str:
    # Windows OCR on these scans reads "rn" as "m" and drops punctuation; compare on a folded form.
    return re.sub(r"[^A-Z0-9]", "", s.upper()).replace("RN", "M")


def name_in(text: str, name: str, strict: float = 0.84) -> bool:
    target = _fold(name)
    if len(target) < 2:
        return False
    for tok in re.findall(r"[A-Za-z][A-Za-z'\-]+", text):
        t = _fold(tok)
        if t == target or (len(target) >= 5 and abs(len(t) - len(target)) <= 2
                           and SequenceMatcher(None, t, target).ratio() >= strict):
            return True
    return False


def dob_forms(d: date) -> list[str]:
    m, dd, y, yy = d.month, d.day, d.year, d.year % 100
    out = set()
    for sep in "/-.":
        out |= {f"{m:02}{sep}{dd:02}{sep}{y}", f"{m}{sep}{dd}{sep}{y}", f"{m:02}{sep}{dd:02}{sep}{yy:02}",
                f"{m}{sep}{dd}{sep}{yy:02}"}
    out |= {f"{y}-{m:02}-{dd:02}", f"{m:02}{dd:02}{y}"}
    return sorted(out, key=len, reverse=True)


def _has_date(text: str, d: date) -> bool:
    return any(re.search(rf"(?<![\d/.\-]){re.escape(f)}(?![\d])", text) for f in dob_forms(d))


# Payer names as printed on insurance cards / forms, and as the front desk types them in Outlook.
PAYER_PATTERNS = {
    "MEDICARE": r"\bmedicare\b(?!\s+advantage)|\bMCR\b|advance beneficiary notice|\bABN\b",
    "UHC": r"united\s?health|\bUHC\b|optum",
    "AETNA": r"\baetna\b",
    "BCBS": r"blue\s?(?:cross|shield)|\banthem\b|\bBCBS\b|\bB[CS]\b(?!\s*\d)|federal employee program|fepblue",
    "CIGNA": r"\bcigna\b",
    "TRICARE": r"\btricare\b|uniformed services|department of defense",
    "HEALTHNET": r"health\s?net",
    "SHARP": r"\bsharp\b|\bSCMG\b",
    "GEHA": r"\bGEHA\b",
    "MEDICAL": r"\bmedi[-\s]cal\b",  # not the word "medical"
    "HUMANA": r"\bhumana\b",
    "SCRIPPS": r"\bscripps\b",
}


def payers_in(text: str) -> set[str]:
    return {k for k, pat in PAYER_PATTERNS.items() if re.search(pat, text, re.I)}


def _tokens(text: str) -> set[str]:
    return {_fold(t) for t in re.findall(r"[A-Za-z][A-Za-z'\-]+", text)}


def name_exact(tokens: set[str], name: str) -> bool:
    """Whole-word match of a (possibly multi-part) name against folded tokens of machine-printed text."""
    parts = [_fold(p) for p in re.split(r"[\s\-]+", name) if len(p) > 1]
    joined = _fold(name)
    return bool(parts) and (joined in tokens or all(p in tokens for p in parts))


def score_packet(texts: list[str], packet: Packet, q: PatientQuery, expected_index: int | None = None) -> tuple[int, list[str]]:
    """How well a packet matches the patient, with the reasons.

    Printed pages (cards, referrals, forms) are machine text, so names must match whole words
    ("Hearty" must not match "heart"); a DOB or account number is the strongest evidence. The
    handwritten superbill page only adds a little, since OCR can't really read handwriting."""
    printed = "\n".join(texts[p] for p in packet.pages if p != packet.superbill)
    handwritten = texts[packet.superbill]
    toks = _tokens(printed)
    score, why = 0, []
    firsts = [f for f in [q.first] + list(q.nicknames) if f]
    first_hit = any(name_exact(toks, f) for f in firsts)
    if q.last and name_exact(toks, q.last):
        # a last name alone can be an ordinary word ("May", "King", "Way"): only with the first name
        # (or the DOB below) is it proof
        score += 15; why.append(f'"{q.last}" on the packet pages')
        if first_hit:
            score += 30; why.append("first name too")
    elif q.last and first_hit and name_in(printed, q.last):
        score += 20; why.append(f'"{q.last}" (close spelling) + first name on the packet pages')
    # (No handwriting check: OCR can't read it, and the form's own words cause false hits -
    #  "Moore" looks like "or more muscles".)
    if q.dob and _has_date(printed + "\n" + handwritten, q.dob):
        score += 35; why.append("DOB matches")
    if q.payers:
        # a matching insurance card helps; a non-matching one proves little (forms print payer names too)
        both = payers_in(" ".join(q.payers)) & payers_in(printed)
        if both:
            score += 12; why.append("insurance card matches (" + ", ".join(sorted(both)) + ")")
    if q.account and re.search(rf"(?<!\d){re.escape(q.account.lstrip('0'))}(?!\d)", printed + handwritten):
        score += 30; why.append("account # found")
    if expected_index is not None:
        if packet.index == expected_index:
            score += 15; why.append("same position as on the schedule")
        elif abs(packet.index - expected_index) == 1:
            score += 6; why.append("next to its schedule position")
    return score, why


@dataclass
class Match:
    batch: Batch
    packet: Packet
    score: int
    reasons: list[str]
    packet_count: int

    @property
    def page(self) -> int:  # 1-based, as people count pages
        return self.packet.superbill + 1

    @property
    def weak(self) -> bool:
        """Only the schedule position points here - a missing superbill earlier in the day shifts
        every later one, so a person has to read the handwritten name to confirm."""
        return all("position" in r for r in self.reasons)


def best_matches(batch: Batch, texts: list[str], q: PatientQuery, expected_index: int | None = None) -> list[Match]:
    _cover, packets = split_packets(texts)
    out = []
    for p in packets:
        s, why = score_packet(texts, p, q, expected_index)
        if s <= 0 and expected_index is not None and abs(p.index - expected_index) <= 3:
            s, why = 1, [f"near its schedule position ({p.index - expected_index:+d})"]
        if s > 0:
            out.append(Match(batch, p, s, why, len(packets)))
    return sorted(out, key=lambda m: -m.score)


# --------------------------------------------------------------------------- whole-day alignment

@dataclass
class Placement:
    patient: int              # index into the day's patients (schedule order)
    packet: Packet | None     # None: no superbill for this patient in the batch
    evidence: int             # printed-page evidence for this patient on this packet
    how: str                  # "printed" | "order" | "none"


def align_day(texts: list[str], patients: list[PatientQuery], weights: list[float] | None = None,
              groups: list[int] | None = None) -> list[Placement]:
    """Map the day's patients (in scan order) onto the batch's packets, keeping order.

    Packets whose printed pages identify a patient (name, DOB, account) anchor the alignment;
    the rest are placed in order between anchors. A booked patient without a superbill (a no-show
    nobody tagged, or a missing sheet) is skipped instead of shifting everyone after them.
    weights[j] < 1 marks patients less likely to have a superbill (e.g. no progress note filed).
    groups[j]: patients sharing a group id (same appointment time) may be scanned in any order;
    evidence decides their order, and without evidence their placement is marked "tie"."""
    from itertools import permutations
    _cover, packets = split_packets(texts)
    n, m = len(packets), len(patients)
    weights = weights or [1.0] * m
    groups = groups or list(range(m))
    ev = [[score_packet(texts, p, q)[0] for q in patients] for p in packets]
    # only strong evidence for someone else (printed name / DOB) should keep a packet from patient j
    best_other = [[max([ev[i][k] for k in range(m) if k != j and ev[i][k] >= 30] or [0]) for j in range(m)] for i in range(n)]
    match = lambda i, j: ev[i][j] - 0.6 * best_other[i][j] + 6 * weights[j]
    blocks: list[list[int]] = []
    for j in range(m):
        if blocks and groups[blocks[-1][0]] == groups[j]:
            blocks[-1].append(j)
        else:
            blocks.append([j])
    NEG = float("-inf")
    G = len(blocks)
    dp = [[NEG] * (G + 1) for _ in range(n + 1)]
    back: dict[tuple[int, int], tuple] = {}
    dp[0][0] = 0.0
    for i in range(n + 1):
        for g in range(G + 1):
            cur = dp[i][g]
            if cur == NEG:
                continue
            if i < n and cur - 1 > dp[i + 1][g]:  # packet i belongs to nobody on the list
                dp[i + 1][g], back[(i + 1, g)] = cur - 1, (i, g, None)
            if g == G:
                continue
            members = blocks[g]
            for t in range(0, min(len(members), n - i) + 1):  # the next t packets go to this group
                best, second, best_perm = (0.0, NEG, ()) if t == 0 else (NEG, NEG, None)
                for perm in (permutations(members, t) if t else ()):
                    s = sum(match(i + k, j) for k, j in enumerate(perm))
                    if s > best:
                        best, second, best_perm = s, best, perm
                    elif s > second:
                        second = s
                if best == NEG:
                    continue
                if cur + best > dp[i + t][g + 1]:
                    # decided: the evidence clearly prefers this order within the same-time group
                    decided = len(members) == 1 or t == 0 or best - second >= 10
                    dp[i + t][g + 1], back[(i + t, g + 1)] = cur + best, (i, g, best_perm, decided)
    placements = [Placement(j, None, 0, "none") for j in range(m)]
    i, g = n, G
    while (i, g) in back:
        pi, pg, *rest = back[(i, g)]
        if rest and rest[0] is not None:
            perm, decided = rest
            for k, j in enumerate(perm):
                e = ev[pi + k][j]
                how = "printed" if e >= 30 else ("order" if decided else "tie")
                placements[j] = Placement(j, packets[pi + k], e, how)
        i, g = pi, pg
    return placements


# --------------------------------------------------------------------------- referral hints

def referral_physicians(texts: list[str], packet: Packet) -> list[str]:
    """Physician names printed on referral/authorization pages in the packet ("Referred To ...")."""
    names = []
    for p in packet.pages:
        t = texts[p]
        if not re.search(r"Referr|Authoriz", t, re.I):
            continue
        for m in re.finditer(r"Referred\s+To", t, re.I):
            window = t[m.end():m.end() + 160]
            for n in re.finditer(r"([A-Z][a-z]+(?:\s+[A-Z]\.)?\s+[A-Z][a-z]+),?\s*M\.?\s?D\b", window):
                if n.group(1) not in names:
                    names.append(n.group(1))
    return names
