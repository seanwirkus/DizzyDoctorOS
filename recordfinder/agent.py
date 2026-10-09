"""Claude works a request end to end, using the clinic's own data on this PC as tools.

Claude decides what to look up (drive, Outlook schedule, progress notes, superbills), reads the documents,
checks them against the request, and finishes with findings, a reply for billing and any letter needed.
Lookups run locally; only what Claude asks for is sent back to it (Messages API, under the BAA).
Anything that changes something - copying files to the Missing Folder, adding a playbook rule - waits
for a person to approve it.
"""
from __future__ import annotations

import base64
import json
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import date, datetime

from . import ai
from .matcher import rank, search_terms

MAX_TURNS = 12   # each step resends the conversation; 12 covers a normal request, then it hands back
MAX_PAGES = 4    # pages per read; Claude asks for a page range when it needs more

SYSTEM = """You are the front-office agent of an outpatient neurology / dizziness clinic. You work one billing \
request at a time, end to end, using tools that look things up on the clinic's own computer:
the file index of the clinic drive, the Outlook appointment history, the progress notes and the scanned superbills.

How to work:
1. Work out exactly what the billing company needs (records, auth, superbill fix, insurance info, an answer...).
2. Gather evidence. Check the schedule to confirm the visit happened (and with which provider). Find and READ the \
documents - don't trust file names alone. Confirm the patient's name and the date of service on each document \
before relying on it.
3. Judge the request against the evidence and the clinic playbook. For downcoding/denials, say whether the note \
supports the billed code and why. For auths, say whether one is on file. Be specific and cite the document and page.
4. If billing asked for documents and you found the right ones, call copy_to_missing_folder with them.
5. If you learned a general clinic rule that isn't in the playbook (never anything about a specific patient), \
call propose_playbook_rule.
6. Always end by calling finish.

Be efficient: search broadly once, then read only what matters. Never invent facts, codes, dates or amounts; \
say what you couldn't find and where a person should look."""

TOOLS = [
    {"name": "search_files", "strict": True,
     "description": "Search the clinic drive index for a patient's files (notes, EOBs, auths, cards, reports, faxes). "
                    "Returns file ids ranked by how well they match. Use the ids with read_document.",
     "input_schema": {"type": "object", "additionalProperties": False,
                      "required": ["last_name", "first_name", "account", "dates", "keywords"],
                      "properties": {
                          "last_name": {"type": "string"}, "first_name": {"type": "string"},
                          "account": {"type": "string", "description": "Account number, or empty"},
                          "dates": {"type": "array", "items": {"type": "string"}, "description": "MM/DD/YYYY dates of service to prefer"},
                          "keywords": {"type": "array", "items": {"type": "string"},
                                       "description": "Document types to prefer, e.g. note, eob, auth, card, referral"}}}},
    {"name": "list_notes", "strict": True,
     "description": "List a patient's progress notes (final and prepped) with their dates of service. Returns file ids.",
     "input_schema": {"type": "object", "additionalProperties": False, "required": ["last_name", "first_name"],
                      "properties": {"last_name": {"type": "string"}, "first_name": {"type": "string"}}}},
    {"name": "read_document", "strict": True,
     "description": "Read a file found by another tool. PDFs come back as text; scanned pages come back as images. "
                    "Progress notes also include their form fields (visit type, time, attestation, impressions).",
     "input_schema": {"type": "object", "additionalProperties": False, "required": ["file_id", "pages"],
                      "properties": {"file_id": {"type": "string"},
                                     "pages": {"type": "string", "description": 'e.g. "1-3"; empty for the first pages'}}}},
    {"name": "patient_schedule", "strict": True,
     "description": "Every Outlook booking for a patient: dates, provider calendar, seen / no-show / cancelled, "
                    "categories and insurance written on the booking.",
     "input_schema": {"type": "object", "additionalProperties": False, "required": ["last_name", "first_name", "dob", "account"],
                      "properties": {"last_name": {"type": "string"}, "first_name": {"type": "string"},
                                     "dob": {"type": "string", "description": "MM/DD/YYYY or empty"},
                                     "account": {"type": "string"}}}},
    {"name": "day_schedule", "strict": True,
     "description": "All Outlook appointments on one day, optionally for one provider calendar.",
     "input_schema": {"type": "object", "additionalProperties": False, "required": ["date", "calendar"],
                      "properties": {"date": {"type": "string", "description": "MM/DD/YYYY"},
                                     "calendar": {"type": "string", "description": "Calendar name, or empty for all"}}}},
    {"name": "find_superbill", "strict": True,
     "description": "Find the patient's page in the day's scanned superbill batch (Windows only; can take a minute). "
                    "Returns a file id for the extracted page and how sure the match is.",
     "input_schema": {"type": "object", "additionalProperties": False,
                      "required": ["last_name", "first_name", "dos", "dob", "account"],
                      "properties": {"last_name": {"type": "string"}, "first_name": {"type": "string"},
                                     "dos": {"type": "string"}, "dob": {"type": "string"}, "account": {"type": "string"}}}},
    {"name": "copy_to_missing_folder", "strict": True,
     "description": "Ask a staff member to approve copying documents to the Missing Folder for billing. "
                    "Only for documents you have read and confirmed belong to this patient and request.",
     "input_schema": {"type": "object", "additionalProperties": False, "required": ["file_ids", "reason"],
                      "properties": {"file_ids": {"type": "array", "items": {"type": "string"}},
                                     "reason": {"type": "string"}}}},
    {"name": "propose_playbook_rule", "strict": True,
     "description": "Suggest a general clinic rule for the playbook (payer rule, coding rule, procedure). "
                    "Never include patient names, account numbers or dates. A staff member approves it.",
     "input_schema": {"type": "object", "additionalProperties": False, "required": ["section", "rule", "reason"],
                      "properties": {"section": {"type": "string", "enum": ["PAYER RULES", "CODING", "OFFICE PROCEDURES", "PROVIDERS"]},
                                     "rule": {"type": "string"}, "reason": {"type": "string"}}}},
    {"name": "finish", "strict": True,
     "description": "Finish the request. Call exactly once, last.",
     "input_schema": {"type": "object", "additionalProperties": False,
                      "required": ["summary", "findings", "reply_to_billing", "letter", "status"],
                      "properties": {
                          "summary": {"type": "string", "description": "One or two sentences: what was needed and the outcome."},
                          "findings": {"type": "array", "items": {"type": "string"},
                                       "description": "Evidence, each citing its document/page or schedule entry."},
                          "reply_to_billing": {"type": "string", "description": "Ready-to-post Teams reply, signed with our initials."},
                          "letter": {"type": "string", "description": "Appeal or other letter if one is needed, else empty."},
                          "status": {"type": "string", "enum": ["Done", "Waiting", "Needs staff"],
                                     "description": "Done = resolved; Waiting = waiting on someone else; Needs staff = a person must act."}}}},
]

ACTION_LABELS = {
    "search_files": "Searching the drive", "list_notes": "Listing progress notes", "read_document": "Reading",
    "patient_schedule": "Checking the patient's appointments", "day_schedule": "Checking the day's schedule",
    "find_superbill": "Finding the superbill", "copy_to_missing_folder": "Copy to Missing Folder",
    "propose_playbook_rule": "New playbook rule", "finish": "Finishing",
}


@dataclass
class Result:
    summary: str = ""
    findings: list = field(default_factory=list)
    reply_to_billing: str = ""
    letter: str = ""
    status: str = "Needs staff"
    copied: list = field(default_factory=list)
    rules_added: list = field(default_factory=list)


def _date(s: str) -> date | None:
    s = (s or "").strip()
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


class Agent:
    """One run of Claude working one request. `events(kind, text)` reports progress; `approve(kind, text)` blocks
    until a person answers (True/False). Both are called from the worker thread."""

    def __init__(self, reader: ai.ClaudeReader, store, cfg: dict, item: dict, events, approve, cancel=None):
        self.reader, self.store, self.cfg, self.item = reader, store, cfg, item
        self.events, self.approve, self.cancel = events, approve, cancel
        self.files: dict[str, str] = {}
        self.result = Result()

    # ------------------------------------------------------------------ file registry
    def _register(self, path: str) -> str:
        for k, p in self.files.items():
            if p == path:
                return k
        k = f"F{len(self.files) + 1}"
        self.files[k] = path
        return k

    # ------------------------------------------------------------------ tools
    def search_files(self, last_name, first_name, account, dates, keywords):
        acct = re.sub(r"\D", "", account)
        rows = self.store.find_files(search_terms(first_name, last_name, acct))
        hits = rank(rows, first_name, last_name, acct, [d for d in map(_date, dates) if d], None,
                    [k.lower() for k in keywords], limit=15)
        return [{"file_id": self._register(h.path), "name": h.name, "folder": h.folder,
                 "modified": datetime.fromtimestamp(h.mtime).strftime("%m/%d/%Y"), "match": h.why} for h in hits] \
            or "No files matched. Try the last name alone, or other spellings."

    def list_notes(self, last_name, first_name):
        from . import notes
        found = notes.find_notes(self.store, self.cfg, last_name, first_name)[:30]
        return [{"file_id": self._register(n.path), "name": n.name, "dos": n.dos.strftime("%m/%d/%Y") if n.dos else "",
                 "kind": n.kind} for n in found] or "No progress notes found under that name."

    def read_document(self, file_id, pages):
        path = self.files.get(file_id)
        if not path:
            return f"Unknown file id {file_id}. Use an id returned by a search tool."
        ext = os.path.splitext(path)[1].lower()
        if ext != ".pdf":
            block = ai.attachment_block(path)
            return [block] if block["type"] != "document" else [{"type": "text", "text": "Unreadable file."}]
        import pymupdf
        out: list[dict] = []
        with pymupdf.open(path) as doc:
            n = doc.page_count
            first, last = 1, min(n, MAX_PAGES)
            m = re.fullmatch(r"\s*(\d+)\s*(?:-\s*(\d+))?\s*", pages or "")
            if m:
                first = max(1, int(m.group(1)))
                last = min(n, int(m.group(2) or m.group(1)), first + MAX_PAGES - 1)
            out.append({"type": "text", "text": f"{os.path.basename(path)} - {n} page(s); showing {first}-{last}."})
            for i in range(first - 1, last):
                page = doc[i]
                text = page.get_text().strip()
                if len(text) >= 40:
                    out.append({"type": "text", "text": f"--- page {i + 1} ---\n{text}"})
                else:  # a scan: send the picture
                    png = page.get_pixmap(dpi=110).tobytes("png")
                    out.append({"type": "text", "text": f"--- page {i + 1} (scanned image) ---"})
                    out.append({"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                            "data": base64.standard_b64encode(png).decode("ascii")}})
        try:
            from . import notes
            nf = notes.fields(self.store, path)
            if nf and (nf.name or nf.date):
                out.append({"type": "text", "text": "Progress-note form fields: " + json.dumps(nf.__dict__)})
        except Exception:
            pass
        return out

    def patient_schedule(self, last_name, first_name, dob, account):
        from . import outlook_cache
        bookings = outlook_cache.find_patient(self.store, last_name, first_name, _date(dob), re.sub(r"\D", "", account))
        if not bookings:
            return "No Outlook bookings found (is the Outlook cache synced on this PC?)."
        rows = [{"date": b.start.strftime("%m/%d/%Y %I:%M %p"), "calendar": b.cal, "seen": b.seen,
                 "categories": b.categories, "insurance": b.insurance, "secondary": b.secondary} for b in bookings[-60:]]
        s = outlook_cache.summary(bookings)
        s["last_seen"] = s["last_seen"].strftime("%m/%d/%Y") if s.get("last_seen") else ""
        return {"summary": s, "bookings": rows}

    def day_schedule(self, date_s, calendar):
        from . import outlook_cache
        d = _date(date_s)
        if not d:
            return "Give the date as MM/DD/YYYY."
        appts = outlook_cache.day(self.store, d, [calendar] if calendar else None)
        return [{"time": a.when, "calendar": a.calendar, "patient": a.patient_name(), "seen": a.seen,
                 "categories": a.categories} for a in appts if a.is_patient] or "No appointments cached for that day."

    def find_superbill(self, last_name, first_name, dos, dob, account):
        from . import attach
        res = attach.find_superbill(self.store, self.cfg, last_name, first_name, _date(dos), _date(dob),
                                    re.sub(r"\D", "", account), cancel=self.cancel,
                                    progress=lambda m: self.events("status", m))
        out = {"found": bool(res.path), "sure": res.confident, "score": res.score, "reasons": res.reasons, "notes": res.notes}
        if res.path:
            out["file_id"] = self._register(res.path)
            out["batch"], out["page"] = os.path.basename(res.batch), res.page
        return out

    def copy_to_missing_folder(self, file_ids, reason):
        paths = [self.files[f] for f in file_ids if f in self.files]
        if not paths:
            return "None of those file ids exist."
        dest = self.cfg.get("missing_folder", "")
        if not dest or not os.path.isdir(dest):
            return "The Missing Folder isn't set up on this PC; tell staff to copy them."
        names = ", ".join(os.path.basename(p) for p in paths)
        if not self.approve("copy", f"Copy to the Missing Folder:\n{names}\n\nWhy: {reason}"):
            return "Staff declined. Don't copy; mention the documents in your findings instead."
        if self.cfg.get("per_patient_subfolder"):
            who = ", ".join(x for x in (self.item.get("last", "").title(), self.item.get("first", "").title()) if x)
            dest = os.path.join(dest, re.sub(r'[<>:"/\\|?*]', "", f"{who} {self.item.get('account', '')}".strip()))
            os.makedirs(dest, exist_ok=True)
        for p in paths:
            base, ext = os.path.splitext(os.path.basename(p))
            target, n = os.path.join(dest, base + ext), 1
            while os.path.exists(target):
                target, n = os.path.join(dest, f"{base} ({n}){ext}"), n + 1
            shutil.copy2(p, target)
            self.result.copied.append(os.path.basename(target))
        return f"Copied: {', '.join(self.result.copied)}"

    def propose_playbook_rule(self, section, rule, reason):
        if not self.approve("rule", f"Add to the playbook ({section}):\n{rule}\n\nWhy: {reason}"):
            return "Staff declined the rule."
        text = ai.load_playbook() or ai.PLAYBOOK_TEMPLATE
        line = f"- {rule.strip()}"
        if section in text:
            head = text.index(section)
            nxt = text.find("\n\n", head)
            text = text[:nxt] + "\n" + line + text[nxt:] if nxt != -1 else text + "\n" + line
        else:
            text = text.rstrip() + f"\n\n{section}\n{line}\n"
        ai.save_playbook(text)
        self.reader.playbook = text
        self.result.rules_added.append(rule)
        return "Added to the playbook."

    # ------------------------------------------------------------------ loop
    def _run_tool(self, name: str, args: dict):
        if name == "day_schedule":
            args = {"date_s": args["date"], "calendar": args["calendar"]}
        fn = getattr(self, name, None)
        if fn is None or name == "finish":
            return "Unknown tool."
        try:
            return fn(**args)
        except Exception as ex:  # a failed lookup is information for Claude, not a crash
            return f"That lookup failed: {ex}"

    def _describe(self, name: str, args: dict) -> str:
        label = ACTION_LABELS.get(name, name)
        if name == "read_document":
            return f"{label} {os.path.basename(self.files.get(args.get('file_id', ''), args.get('file_id', '')))}"
        who = " ".join(x for x in (args.get("first_name", ""), args.get("last_name", "")) if x)
        when = args.get("date") or args.get("dos") or ""
        return " ".join(x for x in (label, who, when) if x)

    def run(self) -> Result:
        it = self.item
        facts = {k: it.get(k) for k in ("name", "first", "last", "account", "dob", "dos_display", "codes", "payers", "kind", "visit_code")
                 if it.get(k)}
        intro = (f"Request from {it.get('sender', '')} ({it.get('when', '')}):\n{it.get('text', '')}\n\n"
                 f"Fields read from it: {json.dumps(facts)}")
        if it.get("answer"):
            intro += f"\n\nEarlier reply in the chat: {it['answer']}"
        if it.get("note"):
            intro += f"\n\nOur notes on this request: {it['note']}"
        messages: list = [{"role": "user", "content": intro}]
        for _turn in range(MAX_TURNS):
            if self.cancel is not None and self.cancel.is_set():
                self.result.summary = "Stopped."
                return self.result
            r = self.reader._create(SYSTEM, messages, effort="high", tools=TOOLS)
            messages.append({"role": "assistant", "content": r.content})
            for b in r.content:
                if b.type == "text" and b.text.strip():
                    self.events("think", b.text.strip())
            calls = [b for b in r.content if b.type == "tool_use"]
            if not calls:
                text = "".join(b.text for b in r.content if b.type == "text").strip()
                self.result.summary = self.result.summary or text or "Claude stopped without a summary."
                return self.result
            results, finished = [], False
            for c in calls:
                if c.name == "finish":
                    a = c.input
                    self.result.summary, self.result.findings = a.get("summary", ""), a.get("findings", [])
                    self.result.reply_to_billing, self.result.letter = a.get("reply_to_billing", ""), a.get("letter", "")
                    self.result.status = a.get("status", "Needs staff")
                    finished = True
                    results.append({"type": "tool_result", "tool_use_id": c.id, "content": "Recorded."})
                    continue
                self.events("tool", self._describe(c.name, c.input))
                out = self._run_tool(c.name, dict(c.input))
                content = out if isinstance(out, list) and out and isinstance(out[0], dict) and "type" in out[0] \
                    else json.dumps(out, default=str) if not isinstance(out, str) else out
                results.append({"type": "tool_result", "tool_use_id": c.id, "content": content})
            messages.append({"role": "user", "content": results})
            if finished:
                return self.result
        self.result.summary = self.result.summary or "Stopped after too many steps - finish this one by hand."
        return self.result
