"""Optional: have Claude (Anthropic API) read billing requests and draft replies/appeals.

This sends patient information to Anthropic. The app only does so after the practice confirms in
Settings that its BAA with Anthropic is signed and HIPAA is enabled on the API organization.
Only the Messages API is used (covered by Anthropic's BAA; Batch/Files/Code Execution are not).
The API key lives in Windows Credential Manager, never in config files.
"""
from __future__ import annotations

import base64
import json
import os
import re
import zipfile

from . import config
from .parser import REQUEST_TYPES, Message, message_id

MODEL = "claude-opus-5-5"          # Work it and Ask: open-ended reading and judgement
FAST_MODEL = "claude-sonnet-5-5"   # reading pasted chats, drafts, the Settings test: half the price per token
# USD per million tokens: input, output, cache read, cache write (5-minute cache = 1.25 x input)
PRICES = {"claude-opus-5-5": (4.00, 20.00, 0.20, 5.00), "claude-sonnet-5-5": (2.00, 10.00, 0.20, 2.50)}
KEYRING_SERVICE = "DizzyDoctor"
LEGACY_KEYRING_SERVICES = ("dizzdocOs", "ClinicRecordFinder")
KEYRING_USER = "anthropic_api_key"
KINDS = [label for label, _, _ in REQUEST_TYPES] + ["Status inquiry", "General"]
BATCH = 20  # messages per API call

REQUEST_SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": KINDS},
        "patient_first": {"type": "string"},
        "patient_last": {"type": "string"},
        "account": {"type": "string"},
        "dob": {"type": "string"},
        "dos": {"type": "array", "items": {"type": "string"}},
        "codes": {"type": "array", "items": {"type": "string"}},
        "payer": {"type": "string"},
        "summary": {"type": "string"},
        "missing_info": {"type": "array", "items": {"type": "string"}},
        "suggested_reply": {"type": "string"},
    },
    "required": ["kind", "patient_first", "patient_last", "account", "dob", "dos", "codes", "payer",
                 "summary", "missing_info", "suggested_reply"],
    "additionalProperties": False,
}
EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {"messages": {"type": "array", "items": {
        "type": "object",
        "properties": {"id": {"type": "string"}, "requests": {"type": "array", "items": REQUEST_SCHEMA}},
        "required": ["id", "requests"],
        "additionalProperties": False,
    }}},
    "required": ["messages"],
    "additionalProperties": False,
}

EXTRACT_SYSTEM = f"""You read messages from a Microsoft Teams chat between the front office of an outpatient \
neurology / dizziness clinic and the clinic's outside billing company, and turn them into a worklist.

For each <message>, return every separate request it contains - one entry per patient. Return an empty list for \
chit-chat, thanks, greetings, and for replies from our own staff that only answer an earlier request.

Field rules:
- kind: one of {", ".join(KINDS)}. "Status inquiry" = someone asks about a patient's account without a specific task.
- Copy names, account numbers and codes exactly as written; never invent or guess them. Keep leading zeros in \
account numbers. Use "" or [] when something is not stated.
- Dates as MM/DD/YYYY. dob = date of birth; dos = date(s) of service. If a date looks like a typo \
(e.g. year 0226, or 09/31), keep your best reading and mention it in missing_info.
- codes: CPT codes with modifiers, e.g. "64405-50". Zip codes and street numbers are not codes.
- payer: the insurance named (Aetna, UnitedHealthcare, Medicare, Blue Shield, SCMG, ...). In these chats "BC" usually \
means Blue Cross, "BS" Blue Shield, "MCR" Medicare.
- Abbreviations: M/R = medical records, S/B = superbill, DOS = date of service, pt = patient, auth = authorization. \
Two-letter codes after a DOS or before "batch" (TY, EC, JP, IP, MP2) are the provider/visit batch.
- summary: one short sentence saying what is needed, for whom.
- missing_info: things the front office needs to resolve or that are missing from the request (e.g. "No insurance \
card provided", "DOS not given", "Year 0226 looks like a typo").
- suggested_reply: a short, professional reply the front office could post once the work is done, in the chat's \
style. Use [brackets] for anything unknown. Do not claim anything has already been done unless the message says so."""

DRAFT_SYSTEM = """You help the front office of an outpatient neurology / dizziness clinic respond to its billing \
company and to insurance payers. Write clearly and professionally. Use only facts from the request and the supporting \
text provided; put [brackets] around anything that must be filled in or verified. Never invent clinical findings, \
dates, codes or policy numbers. Output only the finished text."""

DRAFT_TASKS = {
    "Reply to billing": "Write a short reply to post in the Teams chat to the billing company for this request.",
    "Appeal letter": "Write an appeal letter to the payer for this denial or downcoding. Base the medical-necessity "
                     "argument only on the visit note provided. Include placeholders for letterhead, claim number and signature.",
    "Explain": "Explain in plain language what this request means, what the front office needs to do, and which "
               "documents to look for.",
}

ASK_SYSTEM = """You are the assistant for the front office of an outpatient neurology / dizziness clinic. Staff ask \
you about billing requests, EOBs, denials, authorizations, superbills and visit notes for a specific patient case.

- Use only the case details, the attached documents and the clinic playbook. If they don't answer the question, \
say what's missing and where staff should look.
- When you rely on a document, say which one (and the page, for PDFs).
- Explain payer codes (CARC/RARC such as CO-97 or N130, CPT codes and modifiers) in plain language.
- Be practical: end with the next step the front office should take.
- Never invent clinical findings, dates, amounts, codes or policy numbers. Put [brackets] around anything to verify.
- Plain text only: short paragraphs and "-" bullets, no markdown headings or tables."""

PLAYBOOK_TEMPLATE = """CLINIC PLAYBOOK
Clinic knowledge Claude uses on every request. No patient information here - rules, people and procedures only.
Lines marked (verify) were picked up from past chats; confirm or correct them.

PRACTICE
- Ian Purcell, M.D., APC - otoneurology / dizziness and balance, San Diego. Front desk (858) 223-2172.

PROVIDERS (code - name - role)
- IP - Ian Purcell, MD, PhD - physician
- EC - [name] - [provider type]
- TY - [name] - [provider type]
- JP - [name] - [provider type]
- MP2 - [name] - [provider type]

INSURANCE WE ACCEPT (matches the website's accepted-insurances page)
- Most commercial PPO, EPO and POS plans: Anthem Blue Cross, Blue Shield of California (incl. FEP, BlueCard), UnitedHealthcare
  (incl. Oxford, UMR, Surest, Student Resources), Aetna (incl. Global Benefits, AFSPA), Cigna (PPO, Open Access Plus), Humana PPO.
- Original Medicare (Part B), Railroad Medicare; Medicare Supplement (Medigap) as secondary with Original Medicare.
- Medicare Advantage PPO: AARP/UHC, Aetna, Humana, Anthem, Alignment Health, Heritage Select Advantage.
- Medicare participation is scheduled to end soon - patients should call to confirm. (verify date)
- HMO: ONLY Sharp Community Medical Group (SCMG, incl. Graybill) and Scripps (Health Plan, Clinic, Coastal, Physicians).
  A referral from the primary care doctor is required.
- Federal/military: Tricare for Life, VA Community Care, GEHA, NALC, APWU.
- Administrators: Meritain, HealthComp, WebTPA, Western Growers, Advantek, BRMS, PacificSource, Oscar; OneNet PPO (auto liability).

NOT ACCEPTED
- Health Net - EXCEPT through SCMG, or a Health Net Medicare Supplement (accepted as secondary).
- Workers' Compensation.
- HMOs other than SCMG and Scripps; Tricare other than Tricare for Life.
- CalPERS ASO Platinum PPO: out of network; the patient is responsible. (verify)
- Patients with plans we don't take can be seen as self-pay.

PAYER RULES
- SCMG: an authorization is required for goggle / vestibular studies (92550, 92548). No auth on file -> don't bill. (verify)
- Aetna: frequently downcodes E/M visits (99215 -> 99213, 99205 -> 99204); appeal with the visit note. Expect it; it isn't a
  reason for the patient to call Aetna.

CODING
- Occipital nerve block done on both sides: bill 64405-50 (bilateral). (verify)

OFFICE PROCEDURES
- Documents requested by billing go in the Missing Folder. Check the Missing Folder before asking billing again.
- Payments taken in Square are posted by billing; the receipt note has patient, account, amount, type and DOS.
- If a pending authorization is approved after the visit, bill insurance with it and apply any payment collected as a credit.

HOW REPLIES SHOULD SOUND
- Short and professional; sign with our initials (e.g. *IC).
"""

SAMPLE_CHAT = """PLEASE PROVIDE M/R TO APPEAL FOR ACCOUNT 234567 PT BOB C TESTER DOS 09/01/2026. AETNA IS DOWN CODING FROM 99215 TO 99213."""


class AIError(Exception):
    pass


# --------------------------------------------------------------------------- clinic playbook

PLAYBOOK_PATH = os.path.join(config.CONFIG_DIR, "playbook.md")


def load_playbook() -> str:
    try:
        with open(PLAYBOOK_PATH, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def save_playbook(text: str) -> None:
    os.makedirs(os.path.dirname(PLAYBOOK_PATH), exist_ok=True)
    with open(PLAYBOOK_PATH, "w", encoding="utf-8") as f:
        f.write(text)


# --------------------------------------------------------------------------- attachments

MAX_ATTACHMENT = 20 * 1024 * 1024
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}
TEXT_TYPES = {".txt", ".csv", ".md", ".rtf"}


def _docx_text(path: str) -> str:
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8", "replace")
    xml = re.sub(r"</w:p>", "\n", xml)
    return re.sub(r"<[^>]+>", "", xml)


def attachment_block(path: str) -> dict:
    """Content block for a local file. Read on this PC and sent inline in the Messages API request (not the Files API)."""
    name, ext = os.path.basename(path), os.path.splitext(path)[1].lower()
    if os.path.getsize(path) > MAX_ATTACHMENT:
        raise AIError(f"{name} is too large to send (over 20 MB).")
    if ext == ".docx":
        return {"type": "text", "text": f'<document name="{name}">\n{_docx_text(path)}\n</document>'}
    if ext in TEXT_TYPES:
        with open(path, encoding="utf-8", errors="replace") as f:
            return {"type": "text", "text": f'<document name="{name}">\n{f.read()}\n</document>'}
    if ext in (".tif", ".tiff"):
        try:
            import pymupdf
            with pymupdf.open(path) as img:
                data, ext = img.convert_to_pdf(), ".pdf"
        except Exception as ex:
            raise AIError(f"Couldn't read the scan {name}: {ex}") from ex
    else:
        with open(path, "rb") as f:
            data = f.read()
    b64 = base64.standard_b64encode(data).decode("ascii")
    if ext == ".pdf":
        return {"type": "document", "title": name, "source": {"type": "base64", "media_type": "application/pdf", "data": b64}}
    if ext in IMAGE_TYPES:
        return {"type": "image", "source": {"type": "base64", "media_type": IMAGE_TYPES[ext], "data": b64}}
    raise AIError(f"Claude can't read {ext or 'these'} files yet ({name}). PDF, images, Word and text files work.")


# --------------------------------------------------------------------------- API key (Windows Credential Manager)

def get_key() -> str:
    try:
        import keyring
        key = keyring.get_password(KEYRING_SERVICE, KEYRING_USER) or ""
        for legacy in LEGACY_KEYRING_SERVICES if not key else ():
            key = keyring.get_password(legacy, KEYRING_USER) or ""
            if key:
                keyring.set_password(KEYRING_SERVICE, KEYRING_USER, key)
                break
        return key
    except Exception:
        return os.environ.get("ANTHROPIC_API_KEY", "")


def set_key(key: str) -> None:
    import keyring
    if key:
        keyring.set_password(KEYRING_SERVICE, KEYRING_USER, key)
    else:
        try:
            keyring.delete_password(KEYRING_SERVICE, KEYRING_USER)
        except Exception:
            pass


# --------------------------------------------------------------------------- spend

class Meter:
    """Today's Claude spend on this PC, with a daily limit. Stored in the app's database (meta table).

    check() before a call refuses once the limit is reached (the app then falls back to its built-in rules);
    add() after a call prices the tokens the API reports and adds them to today's total."""

    def __init__(self, get, put, daily_limit: float, on_change=None, today=None):
        from datetime import date
        self.get, self.put, self.limit = get, put, float(daily_limit or 0)
        self.on_change = on_change or (lambda spent, limit: None)
        self._today = today or date.today

    def key(self) -> str:
        return f"ai_spend:{self._today().isoformat()}"

    def spent(self) -> float:
        try:
            return float(self.get(self.key()) or 0)
        except ValueError:
            return 0.0

    def check(self) -> None:
        if self.limit > 0 and self.spent() >= self.limit:
            raise AIError(f"Today's Claude limit (${self.limit:.2f}) is used up. The built-in rules still work; "
                          "raise the limit in Settings > Claude if you need more today.")

    @staticmethod
    def cost(model: str, usage) -> float:
        if usage is None:
            return 0.0
        p_in, p_out, p_read, p_write = PRICES.get(model, PRICES[MODEL])
        g = lambda k: getattr(usage, k, 0) or 0  # noqa: E731
        return (g("input_tokens") * p_in + g("output_tokens") * p_out + g("cache_read_input_tokens") * p_read
                + g("cache_creation_input_tokens") * p_write) / 1_000_000

    def add(self, model: str, usage) -> float:
        c = self.cost(model, usage)
        if c:
            total = self.spent() + c
            self.put(self.key(), f"{total:.6f}")
            self.on_change(total, self.limit)
        return c


# --------------------------------------------------------------------------- client

class ClaudeReader:
    def __init__(self, api_key: str, cache_get=None, cache_put=None, staff: list[str] = (), signature: str = "",
                 client=None, playbook: str | None = None, meter: Meter | None = None):
        if client is None:
            try:
                import anthropic
            except ImportError as ex:
                raise AIError("The Claude library isn't installed in this build.") from ex
            client = anthropic.Anthropic(api_key=api_key, max_retries=3)
        self.client = client
        self.cache_get = cache_get or (lambda _k: None)
        self.cache_put = cache_put or (lambda _k, _v: None)
        self.staff, self.signature = list(staff), signature
        self.playbook = (load_playbook() or PLAYBOOK_TEMPLATE) if playbook is None else playbook
        self.meter = meter

    def _system(self, instructions: str) -> list[dict]:
        blocks = [{"type": "text", "text": instructions}]
        if self.playbook.strip():
            blocks.append({"type": "text", "text": "<clinic_playbook>\n" + self.playbook.strip() + "\n</clinic_playbook>"})
        blocks[-1]["cache_control"] = {"type": "ephemeral"}  # instructions + playbook are reused on every call
        return blocks

    def _create(self, system: str, messages: list, schema: dict | None = None, effort: str = "low", tools: list | None = None,
                model: str = MODEL):
        if self.meter:
            self.meter.check()
        output_config = {"effort": effort}
        if schema:
            output_config["format"] = {"type": "json_schema", "schema": schema}
        extra = {}
        if tools:
            # Agent runs resend a growing conversation every turn: cache it so only the new part is billed in full.
            extra = {"tools": tools, "cache_control": {"type": "ephemeral"}}
        try:
            # Plain Messages API only: no beta features. A HIPAA-configured organization rejects features that
            # aren't on Anthropic's HIPAA list with a 400 (server-side fallbacks aren't on it).
            r = self.client.messages.create(
                model=model,
                max_tokens=16000,
                system=self._system(system),
                messages=messages,
                output_config=output_config,
                **extra,
            )
        except Exception as ex:  # network, auth, rate limit - the caller falls back to the built-in rules
            raise AIError(f"Claude API error: {ex}") from ex
        if self.meter:
            self.meter.add(model, getattr(r, "usage", None))
        if r.stop_reason == "refusal":
            raise AIError("Claude declined this request.")
        if r.stop_reason == "max_tokens":
            raise AIError("Claude's answer was cut off (too much text at once).")
        return r

    def _call(self, system: str, user: str, schema: dict | None = None, effort: str = "low", model: str = FAST_MODEL) -> str:
        r = self._create(system, [{"role": "user", "content": user}], schema, effort, model=model)
        return "".join(b.text for b in r.content if b.type == "text").strip()

    # ------------------------------------------------------------------ reading chats
    def extract(self, msgs: list[Message]) -> dict[str, list[dict]]:
        """Structured requests per message id. Messages read before come from the local cache (no API cost)."""
        out: dict[str, list[dict]] = {}
        todo: list[tuple[str, Message]] = []
        for m in msgs:
            mid = message_id(m)
            cached = self.cache_get(mid)
            if cached is not None:
                out[mid] = cached
            elif m.text.strip() and not any(mid == t[0] for t in todo):
                todo.append((mid, m))
        header = ""
        if self.staff:
            header += "Our staff (their messages are replies, not requests): " + ", ".join(self.staff) + "\n"
        if self.signature:
            header += f'Sign suggested replies with: {self.signature}\n'
        for i in range(0, len(todo), BATCH):
            chunk = todo[i:i + BATCH]
            body = "\n\n".join(self._format(mid, m) for mid, m in chunk)
            data = json.loads(self._call(EXTRACT_SYSTEM, header + "\n" + body, EXTRACT_SCHEMA))
            wanted = {mid for mid, _ in chunk}
            for entry in data.get("messages", []):
                if entry["id"] in wanted:
                    reqs = [r for r in entry["requests"] if r.get("kind") in KINDS]
                    out[entry["id"]] = reqs
                    self.cache_put(entry["id"], reqs)
        return out

    @staticmethod
    def _format(mid: str, m: Message) -> str:
        quoted = "".join(f"\n  (quoting {s}, {w}: {t})" for s, w, t in m.quotes)
        return f'<message id="{mid}" from="{m.sender}" sent="{m.when}">{quoted}\n{m.text}\n</message>'

    # ------------------------------------------------------------------ drafting
    def draft(self, task: str, item: dict, supporting_text: str = "") -> str:
        facts = [f"Request from {item.get('sender', '')} ({item.get('when', '')}):\n{item.get('text', '')}"]
        fields = {k: item.get(k) for k in ("name", "account", "dos_display", "codes", "payers", "kind") if item.get(k)}
        facts.append("Extracted fields: " + json.dumps(fields))
        if supporting_text.strip():
            facts.append("Supporting document text:\n" + supporting_text.strip())
        if self.signature:
            facts.append(f"Sign chat replies with: {self.signature}")
        prompt = DRAFT_TASKS.get(task, task) + "\n\n" + "\n\n".join(facts)
        return self._call(DRAFT_SYSTEM, prompt, effort="medium")

    # ------------------------------------------------------------------ asking about a case
    def ask(self, history: list, question: str, item: dict | None = None, attachments: list[str] = ()) -> tuple[str, list]:
        """One turn of a conversation about a case. Returns (answer, updated history).

        The first turn carries the case details; attachments are added on the turn they're attached.
        Claude's full reply (thinking blocks included) is kept in the history, which the API requires.
        """
        content: list[dict] = []
        if not history and item:
            fields = {k: item.get(k) for k in ("name", "account", "dob", "dos_display", "codes", "payers", "kind") if item.get(k)}
            content.append({"type": "text", "text": f"Case - request from {item.get('sender', '')} ({item.get('when', '')}):\n"
                                                    f"{item.get('text', '')}\nExtracted fields: {json.dumps(fields)}"})
        content += [attachment_block(p) for p in attachments]
        content.append({"type": "text", "text": question})
        messages = list(history) + [{"role": "user", "content": content}]
        r = self._create(ASK_SYSTEM, messages, effort="medium")
        answer = "".join(b.text for b in r.content if b.type == "text").strip()
        return answer, messages + [{"role": "assistant", "content": r.content}]

    def test(self) -> list[dict]:
        """Round trip with made-up data only - safe to run before the BAA is in place."""
        m = Message("Billing (sample)", "Monday 9:34 AM", None, SAMPLE_CHAT)
        mid = message_id(m)
        data = json.loads(self._call(EXTRACT_SYSTEM, self._format(mid, m), EXTRACT_SCHEMA))
        return next((e["requests"] for e in data.get("messages", []) if e["id"] == mid), [])
