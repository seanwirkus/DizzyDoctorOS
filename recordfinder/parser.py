"""Turn chat text copied out of Microsoft Teams into structured billing requests.

Teams' "select all + copy" output looks like this for every message:

    <preview of message>... by <Sender>
    <Sender>                      (order of these two lines varies)
    Friday 12:21 PM
    <body lines>
    1 Like reaction.

Quoted replies carry the original inside the body as "<Name>9/18/2026 9:08 AM"
followed by one (often truncated) line of the quoted text.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

# --------------------------------------------------------------------------- message splitting

_DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_NAME = r"[A-Z][\w'\-]+(?: [A-Z][\w'\-\.]*){0,3}"

TIME_LINE = re.compile(
    r"^(?:(?P<day>Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|Yesterday|Today"
    r"|\d{1,2}/\d{1,2}(?:/\d{2,4})?)\s+)?(?P<time>\d{1,2}:\d{2}\s*[AP]M)$",
    re.I,
)
HEADER_LINE = re.compile(rf"^(?P<preview>.*?)\s+by\s+(?P<sender>{_NAME})$")
QUOTE_LINE = re.compile(rf"^(?P<sender>{_NAME}?)\s?(?P<when>\d{{1,2}}/\d{{1,2}}/\d{{4}} \d{{1,2}}:\d{{2}} [AP]M)$")

NOISE = [re.compile(p, re.I) for p in (
    r"^\d+ \w+ reactions?\.?$",
    r"^has context menu$",
    r"^new messages?$",
    r"^replying to external participants\.?$",
    r"^square: sign in",
    r"^sign in to your square account",
    r"^app\.squareup\.com$",
    r"^\S+\.(com|net|org)$",
    r"^edited$",
    r"^\(?\d+ repl(y|ies)\)?",
)]


@dataclass
class Message:
    sender: str
    when: str               # as shown in Teams, e.g. "Friday 12:21 PM"
    received: datetime | None
    text: str               # body with quotes and noise removed
    quotes: list = field(default_factory=list)  # [(sender, when, snippet)]


def _resolve_when(day: str | None, time_s: str, today: date) -> datetime | None:
    try:
        t = datetime.strptime(re.sub(r"\s+", " ", time_s.upper()).replace("AM", " AM").replace("PM", " PM").replace("  ", " "), "%I:%M %p").time()
    except ValueError:
        return None
    d = today
    if day:
        dl = day.lower()
        if dl == "yesterday":
            d = today - timedelta(days=1)
        elif dl in _DAYS:
            back = (today.weekday() - _DAYS.index(dl)) % 7 or 7
            d = today - timedelta(days=back)
        elif "/" in dl:
            parts = [int(p) for p in dl.split("/")]
            y = parts[2] if len(parts) > 2 else today.year
            y = y + 2000 if y < 100 else y
            try:
                d = date(y, parts[0], parts[1])
            except ValueError:
                pass
    return datetime.combine(d, t)


def _is_noise(line: str) -> bool:
    return any(p.search(line) for p in NOISE)


def split_messages(raw: str, today: date | None = None) -> list[Message]:
    today = today or date.today()
    lines = [ln.strip() for ln in raw.replace("\r\n", "\n").split("\n")]
    anchors = []  # (start_index, body_index, sender, when_display, received)
    for i, ln in enumerate(lines):
        m = TIME_LINE.match(ln)
        if not m:
            continue
        header_i, sender = None, ""
        for back in (1, 2):
            if i - back >= 0 and (h := HEADER_LINE.match(lines[i - back])):
                header_i, sender = i - back, h.group("sender")
                break
        if header_i is None:
            prev = lines[i - 1] if i else ""
            if re.fullmatch(_NAME, prev):
                header_i, sender = i - 1, prev
            else:
                continue
        body_i = i + 1
        if body_i < len(lines) and lines[body_i] == sender:
            body_i += 1
        anchors.append((header_i, body_i, sender, ln, _resolve_when(m.group("day"), m.group("time"), today)))

    if not anchors:  # a single pasted message with no Teams chrome
        text = "\n".join(ln for ln in lines if ln and not _is_noise(ln))
        return [Message("", "", None, text)] if text else []

    messages = []
    for n, (start, body_i, sender, when, received) in enumerate(anchors):
        end = anchors[n + 1][0] if n + 1 < len(anchors) else len(lines)
        body, quotes, j = [], [], body_i
        while j < end:
            ln = lines[j]
            q = QUOTE_LINE.match(ln)
            if q and j + 1 < end:
                quotes.append((q.group("sender").strip(), q.group("when"), lines[j + 1]))
                j += 2
                continue
            if ln and not _is_noise(ln):
                body.append(ln)
            j += 1
        messages.append(Message(sender, when, received, "\n".join(body), quotes))
    return messages


# --------------------------------------------------------------------------- entity extraction

PAYERS = {
    "Aetna": r"aetna",
    "UnitedHealthcare": r"\buhc\b|united\s?health",
    "Humana": r"humana",
    "Scripps": r"scripps",
    "Blue Shield": r"blue\s?shield|\bbs\b(?= denied| is| claim)",
    "Blue Cross / Anthem": r"blue\s?cross|anthem",
    "SCMG": r"\bscmg\b",
    "Medicare": r"medicare|\bmcr\b",
    "Medi-Cal": r"medi-?cal",
    "Health Net": r"health\s?net",
    "CalPERS": r"calpers",
    "Cigna": r"cigna",
    "Tricare": r"tricare",
    "Kaiser": r"kaiser",
    "Bankers Life": r"bankers? life",
}

# (label, pattern, filename hints that suggest the right document)
REQUEST_TYPES = [
    ("Medical records", r"\bm/r\b|medica?l? ?records?", ["note", "progress", "visit", "chart", "record", "consult", "report", "ov"]),
    ("Appeal / downcode", r"down\s?-?cod|appeal|underpa", ["note", "progress", "visit", "chart", "record"]),
    ("Denial", r"\bden(?:y|ying|ied|ial)\b", ["note", "progress", "visit", "report", "record"]),
    ("Missing auth", r"\bauth", ["auth", "referral", "approval", "precert"]),
    ("Provider not circled", r"\b(?:provider|attending|physician|doctor|md)\b.{0,12}\b(?:not|n't|never)\s+(?:circled|marked|checked|selected)"
                             r"|\bno\s+(?:provide[rd]?|attending)\s+(?:circled|marked|checked)"
                             r"|\byes\s*/?\s*(?:or\s+)?no\s+(?:is\s+|was\s+)?not\s+(?:circled|marked|checked)", ["sb", "superbill"]),
    ("Coding question", r"do we bill|circled|bilateral|\b\d{5}\s?-\s?50\b", ["note", "procedure", "sb"]),
    ("Superbill / visit check", r"\bs/b\b|super\s?bill|was (?:pt|patient) seen|\bseen on\b", ["sb", "superbill", "encounter", "sign in", "fee"]),
    ("Insurance update", r"new ins|new \w+ id|correct insurance|not active|ins(?:urance)? cards?|new insurance", ["ins", "card", "id", "face", "demo"]),
    ("Address update", r"address", ["demo", "face", "registration", "intake"]),
    ("Payment to post", r"post payment|please post", ["receipt", "payment"]),
    ("Refund", r"refund", ["receipt", "eob", "refund"]),
    ("Copay log", r"copay log", ["copay", "log"]),
    ("Call back", r"call me|give me a call|call back", []),
]

STOPWORDS = {
    "ACCT", "ACCT#", "ACCOUNT", "DOB", "DOS", "PT", "PT:", "PATIENT", "WAS", "IS", "SEEN", "ON", "WE", "NEED",
    "HAS", "HAD", "AND", "THE", "FOR", "TO", "OF", "IN", "CALLED", "WROTE", "MISSING", "NO", "NOT", "PLEASE",
    "PLS", "BC", "YES", "OK", "HI", "NOTE", "NEW", "DISCOUNTED", "CO-INSURANCE", "DEDUCTIBLE", "BATCH", "WITH",
    "BANKER", "INS", "MEDICARE", "AETNA", "HUMANA", "SCRIPPS", "UHC", "SCMG", "MCR", "BS", "F/U", "CHAIR",
} | {m.upper() for m in ("jan feb mar apr may jun jul aug sep sept oct nov dec".split())}

NOT_VISIT_CODES = {"BS", "BC", "MC", "PT", "NO", "WE", "IS", "ON", "OR", "TO", "IN", "AT", "OF"}
NAME_SUFFIXES = {"JR", "SR", "II", "III", "IV"}
_TOKEN = re.compile(r"^[A-Za-z][A-Za-z'\-]*\.?$")
_DATE = re.compile(r"(?<![\d/])(\d{1,2})/(\d{1,2})/(\d{2,4})(?![\d/])")
_ACCT = re.compile(r"\b(?:account|acct)\s*#?\s*:?\s*#?\s*(\d{4,6})\b|^\s*(\d{4,6})(?=\s+(?:pt|patient)\b)", re.I)
_COMMA_NAME = re.compile(r"\b([A-Z][A-Za-z'\-]+),\s*([A-Z][A-Za-z'\-]+)(?:\s+([A-Z])\.?)?\s+(?:(\d{4,6})\b|(?=DOB))")
_DASH_LIST = re.compile(r"\b(\d{4,6})\s*-\s*([A-Z][A-Za-z'\-]+(?:\s+[A-Z][A-Za-z'\-]*\.?){1,4})\s*-\s*DOB", re.I)
_PT_MARKER = re.compile(r"\b(?:pt|patient)\b\s*[:.]?\s+", re.I)
_CODE = re.compile(r"(?<![\d$#/.\-])(\d{5})(?:\s?-\s?(\d{2}))?(?![\d/]|\.\d)")
_AMOUNT = re.compile(r"\$\s?([\d,]+\.\d{2})")
_VISIT_CODE = re.compile(r"(?:DOS\s*:?\s*\d{1,2}/\d{1,2}/\d{2,4}\s+([A-Z]{2}\d?)\b(?!/)|\b([A-Z]{2}\d?)\s+(?:\d{1,2}/\d{1,2}/\d{2,4}\s+)?batch\b)", re.I)
_STREET = re.compile(r"^\s+[A-Z]+\s+(?:RD|ST|AVE|DR|BLVD|WAY|LN|CT|PL|HWY)\b", re.I)


def _style(tok: str) -> str:
    core = tok.rstrip(".")
    if len(core) == 1:
        return "initial"
    if core.isupper():
        return "upper"
    return "title" if core[0].isupper() else "lower"


def _read_name(text: str, pos: int, max_tokens: int = 4) -> tuple[list[str], int]:
    """Read person-name tokens starting at text[pos]; stop at stop-words or a change in letter case."""
    tokens, style, i = [], None, pos
    for m in re.finditer(r"\S+", text[pos:]):
        raw = m.group(0)
        tok = raw.rstrip(",;:")
        if not _TOKEN.match(tok) or tok.upper().rstrip(".") in STOPWORDS or tok.upper() in STOPWORDS:
            break
        s = _style(tok)
        if s == "lower":
            break
        if s != "initial":
            if style is None:
                style = s
            elif s != style:
                break
        elif not tokens:
            break  # a name never starts with a lone initial
        tokens.append(tok.rstrip("."))
        i = pos + m.end()
        if raw != tok or len(tokens) >= max_tokens:
            break
    while tokens and len(tokens[-1]) == 1:  # drop trailing initials
        tokens.pop()
    return (tokens, i) if len(tokens) >= 2 else ([], pos)


def _norm_year(y: str) -> int:
    n = int(y)
    if len(y) <= 2:
        return 2000 + n if n <= (date.today().year % 100) + 1 else 1900 + n
    if 0 < n < 1000 and len(y) == 4:  # typo like 0226 -> 2026
        return 2000 + n % 100
    return n


@dataclass
class DateRef:
    raw: str
    label: str              # "DOB", "DOS" or ""
    value: date | None      # None when the date is impossible (e.g. 09/31)

    def display(self) -> str:
        return self.value.strftime("%m/%d/%Y") if self.value else f"{self.raw} (?)"


def extract_dates(text: str) -> list[DateRef]:
    out: list[DateRef] = []
    prev_end, prev_label = -100, ""
    for m in _DATE.finditer(text):
        before = text[max(0, m.start() - 14):m.start()]
        between = text[prev_end:m.start()] if prev_end >= 0 else ""
        if re.search(r"\bDOB\b", before, re.I):
            label = "DOB"
        elif re.search(r"\bDOS\b|\bseen on\b", before, re.I) or re.match(r"\s*batch\b", text[m.end():], re.I):
            label = "DOS"
        elif prev_label and re.fullmatch(r"\s*(?:,|and|&|/)?\s*", between, re.I):
            label = prev_label
        else:
            label = ""
        try:
            value = date(_norm_year(m.group(3)), int(m.group(1)), int(m.group(2)))
        except ValueError:
            value = None
        out.append(DateRef(m.group(0), label, value))
        prev_end, prev_label = m.end(), label
    return out


@dataclass
class Patient:
    name: list[str] = field(default_factory=list)   # tokens, first..last
    account: str = ""
    dob: DateRef | None = None
    dos: list[DateRef] = field(default_factory=list)
    other_dates: list[DateRef] = field(default_factory=list)
    codes: list[str] = field(default_factory=list)
    visit_code: str = ""

    @property
    def first(self) -> str:
        return self.name[0].title() if self.name else ""

    @property
    def last(self) -> str:
        toks = [t for t in self.name if t.upper().rstrip(".") not in NAME_SUFFIXES] or self.name
        return toks[-1].title() if toks else ""  # "Bob C Tester Jr" -> "Tester"

    @property
    def display_name(self) -> str:
        return " ".join(t.title() if len(t) > 1 else t.upper() for t in self.name)

    def key(self) -> str:
        return self.account.lstrip("0") or self.last.lower()


def _patients_in_segment(seg: str) -> list[Patient]:
    found: list[Patient] = []

    def add(name: list[str], acct: str = ""):
        for p in found:
            if (acct and p.account == acct) or (name and p.name and p.name[-1].lower() == name[-1].lower()):
                p.account = p.account or acct
                p.name = p.name or name
                return
        found.append(Patient(name=name, account=acct))

    claimed: list[tuple[int, int]] = []
    for m in _DASH_LIST.finditer(seg):
        tokens, _ = _read_name(m.group(2), 0, max_tokens=6)
        add(tokens or m.group(2).split(), m.group(1))
        claimed.append(m.span(1))
    for m in _COMMA_NAME.finditer(seg):
        last, first, _mi, acct = m.group(1), m.group(2), m.group(3), m.group(4) or ""
        if last.upper() in STOPWORDS or first.upper() in STOPWORDS or (first.isupper() and len(first) == 2):
            continue
        add([first, last], acct)
        if acct:
            claimed.append(m.span(4))
    for m in _ACCT.finditer(seg):
        g = 1 if m.group(1) else 2
        acct = m.group(g)
        claimed.append(m.span(g))
        pos = m.end()
        name: list[str] = []
        if seg[pos:pos + 1] == " ":
            mk = _PT_MARKER.match(seg, pos + 1)
            name, _ = _read_name(seg, mk.end() if mk else pos + 1)
        add(name, acct)
    for m in _PT_MARKER.finditer(seg):
        name, end = _read_name(seg, m.end())
        if not name:
            continue
        am = re.match(r"\s*(?:acct|account)\s*#?\s*:?\s*(\d{4,6})", seg[end:], re.I)
        add(name, am.group(1) if am else "")

    dates = extract_dates(seg)
    codes = []
    date_spans = [m.span() for m in _DATE.finditer(seg)]
    for m in _CODE.finditer(seg):
        s = m.start(1)
        if any(a <= s < b for a, b in claimed + date_spans):
            continue
        if re.search(r",\s*[A-Z]{2}\s+$", seg[max(0, s - 8):s]):
            continue  # "Santee, CA 92071" is a zip code
        if _STREET.match(seg[m.end():]):
            continue
        code = m.group(1) + (f"-{m.group(2)}" if m.group(2) else "")
        if code not in codes:
            codes.append(code)
    vm = _VISIT_CODE.search(seg)
    visit = (vm.group(1) or vm.group(2)).upper() if vm else ""
    if visit in NOT_VISIT_CODES:
        visit = ""

    if not found and (dates or codes):
        return []
    for p in found:
        p.dob = next((d for d in dates if d.label == "DOB"), None)
        p.dos = [d for d in dates if d.label == "DOS"]
        p.other_dates = [d for d in dates if not d.label]
        p.codes = codes
        p.visit_code = visit
    return found


def extract_patients(text: str) -> list[Patient]:
    """Patients mentioned in a message. Multi-line lists yield one patient per line."""
    lines = [ln for ln in text.split("\n") if ln.strip()]
    per_line = [_patients_in_segment(ln) for ln in lines]
    if sum(1 for p in per_line if p) >= 2:
        merged: list[Patient] = []
        for group in per_line:
            for p in group:
                same = next((q for q in merged if q.key() == p.key()), None)
                if same is None:
                    merged.append(p)
                else:
                    same.dos += [d for d in p.dos if d.raw not in {x.raw for x in same.dos}]
                    same.codes += [c for c in p.codes if c not in same.codes]
        return merged
    return _patients_in_segment(" ".join(lines))


def classify(text: str) -> list[str]:
    return [label for label, pat, _ in REQUEST_TYPES if re.search(pat, text, re.I)]


def payers_in(text: str) -> list[str]:
    return [name for name, pat in PAYERS.items() if re.search(pat, text, re.I)]


def hints_for(types: list[str]) -> list[str]:
    hints: list[str] = []
    for label, _, h in REQUEST_TYPES:
        if label in types:
            hints += [x for x in h if x not in hints]
    return hints


# --------------------------------------------------------------------------- requests

@dataclass
class Request:
    key: str
    sender: str
    when: str
    received: datetime | None
    text: str
    types: list[str]
    payers: list[str]
    amounts: list[str]
    patient: Patient | None
    answered_by: str = ""
    answer: str = ""
    msg_index: int = -1
    # Filled in when Claude read the message (see ai.py)
    ai_kind: str = ""
    summary: str = ""
    missing: list = field(default_factory=list)
    suggested_reply: str = ""

    @property
    def kind(self) -> str:
        if self.ai_kind:
            return self.ai_kind
        if self.types:
            return self.types[0]
        if self.patient and (self.patient.dob or self.patient.account):
            return "Status inquiry"
        return "General"


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _base(msg: Message) -> str:
    return f"{msg.sender}|{_norm(msg.text)[:400]}"


def message_id(msg: Message) -> str:
    """Stable id for a message, so re-pasting the same chat is recognised (and never re-sent to Claude)."""
    return hashlib.sha1(_base(msg).encode()).hexdigest()[:12]


def _request_key(msg: Message, p: Patient | None) -> str:
    return hashlib.sha1(f"{_base(msg)}|{p.key() if p else ''}".encode()).hexdigest()[:16]


def _ai_date(s: str, label: str) -> DateRef | None:
    m = re.fullmatch(r"\s*(\d{1,2})/(\d{1,2})/(\d{2,4})\s*", s or "")
    if not m:
        return None
    try:
        value = date(_norm_year(m.group(3)), int(m.group(1)), int(m.group(2)))
    except ValueError:
        value = None
    return DateRef(s.strip(), label, value)


def patient_from_ai(d: dict) -> Patient | None:
    name = (d.get("patient_first", "").split() + d.get("patient_last", "").split())
    account = re.sub(r"\D", "", d.get("account", ""))
    if not name and not account:
        return None
    return Patient(
        name=name, account=account, dob=_ai_date(d.get("dob", ""), "DOB"),
        dos=[x for x in (_ai_date(s, "DOS") for s in d.get("dos", [])) if x],
        codes=[c.strip() for c in d.get("codes", []) if c.strip()],
    )


def parse_requests(raw: str, today: date | None = None, ai=None) -> list[Request]:
    """ai: optional callable(list[Message]) -> {message_id: [request dicts]}; messages it covers skip the regex rules."""
    msgs = split_messages(raw, today)
    ai_results = ai(msgs) if ai and msgs else {}
    requests: list[Request] = []
    for idx, msg in enumerate(msgs):
        amounts = _AMOUNT.findall(msg.text)
        found = ai_results.get(message_id(msg))
        if found is not None:
            for d in found:
                p = patient_from_ai(d)
                payers = [d["payer"]] if d.get("payer") else payers_in(msg.text)
                requests.append(Request(
                    _request_key(msg, p), msg.sender, msg.when, msg.received, msg.text, [d["kind"]], payers, amounts, p,
                    msg_index=idx, ai_kind=d["kind"], summary=d.get("summary", ""),
                    missing=list(d.get("missing_info", [])), suggested_reply=d.get("suggested_reply", "")))
            continue
        types = classify(msg.text)
        patients = extract_patients(msg.text)
        payers = payers_in(msg.text)
        if not patients and msg.quotes and types:
            patients = extract_patients("\n".join(q[2] for q in msg.quotes))
        if not patients:
            if not types or not msg.text.strip() or msg.quotes:
                continue  # chit-chat ("Gracias") or a reply to an earlier request
            patients = [None]
        for p in patients:
            requests.append(Request(_request_key(msg, p), msg.sender, msg.when, msg.received, msg.text, types, payers, amounts, p, msg_index=idx))

    # Link replies: a later message that quotes a request marks it answered.
    for idx, msg in enumerate(msgs):
        for q_sender, _q_when, snippet in msg.quotes:
            snip = _norm(snippet.rstrip("…").rstrip("."))[:80]
            if len(snip) < 12:
                continue
            for r in requests:
                if r.msg_index < idx and r.sender == q_sender and _norm(r.text).startswith(snip):
                    reply = msg.text.strip()
                    if reply and not reply.startswith(r.text[:30]):
                        r.answered_by = msg.sender
                        r.answer = reply if not r.answer else r.answer + "\n---\n" + reply
    return requests
