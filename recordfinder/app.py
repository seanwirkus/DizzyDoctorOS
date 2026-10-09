"""DizzyDoctor classic tools: the billing worklist, patient records and superbill tools.

Paste the billing Teams chat to get a worklist; each request gets the matching files from the drive,
plus its superbill page and progress note attached automatically. Quick find opens a patient's
complete record (every Outlook booking, note and document)."""
from __future__ import annotations

import logging
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from datetime import date, datetime, timedelta
from tkinter import filedialog, messagebox, ttk

from . import ai, classic_link, config, people, shared
from .index import Store
from .matcher import Hit, rank, search_terms
from .parser import extract_patients, hints_for, parse_requests
from .version import VERSION

log = logging.getLogger("recordfinder")

IS_WIN = sys.platform.startswith("win")

VIEWS = ["Open requests", "All open (incl. ours)", "Answered", "Done", "Everything"]


# --------------------------------------------------------------------------- helpers

def request_to_dict(r) -> dict:
    p = r.patient
    return {
        "sender": r.sender, "when": r.when,
        "received": r.received.isoformat(timespec="minutes") if r.received else "",
        "text": r.text, "kind": r.kind, "types": r.types, "payers": r.payers, "amounts": r.amounts,
        "answer": r.answer, "answered_by": r.answered_by, "hints": hints_for(r.types),
        "name": p.display_name if p else "", "first": p.first if p else "", "last": p.last if p else "",
        "account": p.account if p else "",
        "dob": p.dob.value.isoformat() if p and p.dob and p.dob.value else "",
        "dos": [d.value.isoformat() for d in (p.dos or p.other_dates) if d.value] if p else [],
        "dos_display": ", ".join(d.display() for d in (p.dos or p.other_dates)) if p else "",
        "codes": p.codes if p else [], "visit_code": p.visit_code if p else "",
        "summary": r.summary, "missing": r.missing, "suggested_reply": r.suggested_reply, "via_ai": bool(r.ai_kind),
    }


def _money(s: str, default: float) -> float:
    try:
        return max(0.0, round(float(s.strip().lstrip("$") or 0), 2))
    except ValueError:
        return float(default)


def parse_dates(s: str) -> list[date]:
    out = []
    for m in re.finditer(r"(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})", s):
        y = int(m.group(3))
        y = y + 2000 if y < 100 else y
        try:
            out.append(date(y, int(m.group(1)), int(m.group(2))))
        except ValueError:
            pass
    return out


def open_path(path: str, reveal: bool = False):
    if IS_WIN:
        if reveal:
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        else:
            os.startfile(path)  # noqa: S606 - opening a file the user picked
    else:
        subprocess.Popen(["open", "-R", path] if reveal else ["open", path])


def reply_text(item: dict, copied: list[str], signature: str) -> str:
    who = " ".join(x for x in (item.get("name"), f"Acct# {item['account']}" if item.get("account") else "") if x)
    dos = f" DOS {item['dos_display']}" if item.get("dos_display") else ""
    kind = item.get("kind", "")
    doc = {"Medical records": "M/R", "Appeal / downcode": "M/R", "Denial": "M/R", "Missing auth": "Auth",
           "Superbill / visit check": "S/B", "Insurance update": "Insurance card(s)"}.get(kind, "Requested documents")
    where = "uploaded to the Missing Folder" if copied else "attached"
    return f"{doc} for {who}{dos} {where}. {signature}".strip()


# --------------------------------------------------------------------------- settings dialog

class SettingsDialog(tk.Toplevel):
    def __init__(self, master, cfg: dict, on_save):
        super().__init__(master)
        self.title("Settings")
        self.transient(master)
        self.resizable(True, False)
        self.cfg, self.on_save = dict(cfg), on_save
        pad = {"padx": 8, "pady": 4}
        f = ttk.Frame(self, padding=10)
        f.pack(fill="both", expand=True)
        f.columnconfigure(1, weight=1)

        ttk.Label(f, text="Folders to search\n(clinic drive, scans, EHR exports)").grid(row=0, column=0, sticky="nw", **pad)
        lf = ttk.Frame(f)
        lf.grid(row=0, column=1, sticky="ew", **pad)
        self.folders = tk.Listbox(lf, height=5, width=60)
        self.folders.pack(side="left", fill="both", expand=True)
        for p in cfg["search_folders"]:
            self.folders.insert("end", p)
        bf = ttk.Frame(lf)
        bf.pack(side="left", fill="y", padx=(6, 0))
        ttk.Button(bf, text="Add…", command=self._add_folder).pack(fill="x")
        ttk.Button(bf, text="Remove", command=lambda: [self.folders.delete(i) for i in reversed(self.folders.curselection())]).pack(fill="x", pady=4)

        self.missing = tk.StringVar(value=cfg["missing_folder"])
        ttk.Label(f, text="Missing Folder").grid(row=1, column=0, sticky="w", **pad)
        mf = ttk.Frame(f)
        mf.grid(row=1, column=1, sticky="ew", **pad)
        ttk.Entry(mf, textvariable=self.missing).pack(side="left", fill="x", expand=True)
        ttk.Button(mf, text="Browse…", command=lambda: self.missing.set(filedialog.askdirectory(parent=self) or self.missing.get())).pack(side="left", padx=(6, 0))

        self.subfolder = tk.BooleanVar(value=cfg["per_patient_subfolder"])
        ttk.Checkbutton(f, text='Copy into a per-patient subfolder ("Last, First 123456")', variable=self.subfolder).grid(row=2, column=1, sticky="w", **pad)

        self.staff = tk.StringVar(value=", ".join(cfg["staff_names"]))
        ttk.Label(f, text="Our staff (comma separated)").grid(row=3, column=0, sticky="w", **pad)
        ttk.Entry(f, textvariable=self.staff).grid(row=3, column=1, sticky="ew", **pad)
        ttk.Label(f, text="Messages from these people are treated as our replies, not requests.", foreground="gray").grid(row=4, column=1, sticky="w", padx=8)

        self.sig = tk.StringVar(value=cfg["signature"])
        ttk.Label(f, text="Reply signature").grid(row=5, column=0, sticky="w", **pad)
        ttk.Entry(f, textvariable=self.sig, width=12).grid(row=5, column=1, sticky="w", **pad)

        self.hours = tk.IntVar(value=cfg["reindex_hours"])
        ttk.Label(f, text="Re-scan drive every (hours)").grid(row=6, column=0, sticky="w", **pad)
        ttk.Spinbox(f, from_=1, to=168, textvariable=self.hours, width=6).grid(row=6, column=1, sticky="w", **pad)

        self.skip = tk.StringVar(value=", ".join(cfg["skip_folders"]))
        ttk.Label(f, text="Skip folders named").grid(row=7, column=0, sticky="w", **pad)
        ttk.Entry(f, textvariable=self.skip).grid(row=7, column=1, sticky="ew", **pad)

        ttk.Separator(f).grid(row=8, column=0, columnspan=2, sticky="ew", pady=6)
        self.sb_root = tk.StringVar(value=cfg.get("superbill_root", ""))
        self.office = tk.StringVar(value=cfg.get("office_copy_folder", ""))
        self.billing = tk.StringVar(value=cfg.get("billing_folder", ""))
        for row, (label, var) in enumerate((("Superbill batches folder", self.sb_root),
                                            ("Office copy folder for\namended superbills", self.office),
                                            ("Billing folder (e.g. a\nShareFile drive), optional", self.billing)), start=9):
            ttk.Label(f, text=label).grid(row=row, column=0, sticky="w", **pad)
            ef = ttk.Frame(f)
            ef.grid(row=row, column=1, sticky="ew", **pad)
            ttk.Entry(ef, textvariable=var).pack(side="left", fill="x", expand=True)
            ttk.Button(ef, text="Browse…", command=lambda v=var: v.set(os.path.normpath(filedialog.askdirectory(parent=self) or v.get()))).pack(side="left", padx=(6, 0))
        ttk.Label(f, text="In these folders, {yyyy} {Month} {mm} are filled in from the date of service,\n"
                          "e.g. S:\\CLINIC\\{yyyy}\\{Month}\\Missing", foreground="gray").grid(row=12, column=1, sticky="w", padx=8)
        self.initials = tk.StringVar(value=cfg.get("initials", ""))
        ttk.Label(f, text="Initials on amendments").grid(row=13, column=0, sticky="w", **pad)
        ttk.Entry(f, textvariable=self.initials, width=8).grid(row=13, column=1, sticky="w", **pad)

        ttk.Separator(f).grid(row=14, column=0, columnspan=2, sticky="ew", pady=(10, 4))
        ttk.Label(f, text="Claude", font=("", 10, "bold")).grid(row=15, column=0, sticky="w", **pad)
        self.had_key = bool(ai.get_key())
        self.key = tk.StringVar()
        ttk.Label(f, text="Claude API key").grid(row=16, column=0, sticky="w", **pad)
        kf = ttk.Frame(f)
        kf.grid(row=16, column=1, sticky="ew", **pad)
        ttk.Entry(kf, textvariable=self.key, show="•", width=40).pack(side="left", fill="x", expand=True)
        self.key_state = ttk.Label(kf, text="saved in Windows Credential Manager" if self.had_key else "not set", foreground="gray")
        self.key_state.pack(side="left", padx=6)
        ttk.Button(kf, text="Test", command=self._test_ai).pack(side="left")
        self.read_chats = tk.BooleanVar(value=cfg["ai_read_chats"])
        ttk.Checkbutton(f, text="Claude reads pasted Teams chats", variable=self.read_chats).grid(row=17, column=1, sticky="w", **pad)
        self.daily = tk.StringVar(value=f"{float(cfg.get('ai_daily_limit', 5.0)):g}")
        ttk.Label(f, text="Daily limit ($)").grid(row=18, column=0, sticky="w", **pad)
        lf = ttk.Frame(f)
        lf.grid(row=18, column=1, sticky="w", **pad)
        ttk.Entry(lf, textvariable=self.daily, width=8).pack(side="left")
        spent = ai.Meter(master.store.meta_get, master.store.meta_set, 0).spent() if hasattr(master, "store") else 0.0
        ttk.Label(lf, text=f"spent today: ${spent:.2f} · when it's reached, the built-in rules take over (0 = no limit)",
                  foreground="gray").pack(side="left", padx=6)

        b = ttk.Frame(f)
        b.grid(row=19, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(b, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(b, text="Save", command=self._save).pack(side="right", padx=6)
        self.grab_set()

    def _add_folder(self):
        p = filedialog.askdirectory(parent=self, title="Choose a folder with patient records")
        if p:
            self.folders.insert("end", os.path.normpath(p))


    def _current_key(self) -> str:
        return self.key.get().strip() or ai.get_key()

    def _test_ai(self):
        key = self._current_key()
        if not key:
            messagebox.showwarning("Claude", "Paste the API key from the Claude Console first.", parent=self)
            return
        self.key_state.configure(text="testing with made-up data…")

        def work():
            try:
                reqs = ai.ClaudeReader(key).test()
                r = reqs[0] if reqs else {}
                msg = (f"Connected.\n\nSample request read as:\n{r.get('kind')} · {r.get('patient_first')} {r.get('patient_last')} · "
                       f"acct {r.get('account')} · DOS {', '.join(r.get('dos', []))}\n\n{r.get('summary', '')}") if r else "Connected, but no request came back."
                ok = True
            except ai.AIError as ex:
                msg, ok = str(ex), False
            self.master.ui(lambda: (self.key_state.configure(text="works" if ok else "failed"),
                                   (messagebox.showinfo if ok else messagebox.showerror)("Claude test", msg, parent=self)))

        threading.Thread(target=work, daemon=True).start()

    def _save(self):
        split = lambda s: [x.strip() for x in s.split(",") if x.strip()]  # noqa: E731
        if self.key.get().strip() and not self.cfg.get("ai_baa_confirmed"):
            if not messagebox.askyesno("Claude", "Claude will read patient information.\n\nConfirm that the practice's BAA with "
                                       "Anthropic is signed and HIPAA is enabled on the API organization.", parent=self):
                return
            self.cfg["ai_baa_confirmed"] = True
        if self.key.get().strip():
            try:
                ai.set_key(self.key.get().strip())
            except Exception as ex:
                messagebox.showerror("Claude", f"Couldn't save the API key: {ex}", parent=self)
                return
        self.cfg.update(
            search_folders=list(self.folders.get(0, "end")), missing_folder=self.missing.get().strip(),
            per_patient_subfolder=self.subfolder.get(), staff_names=split(self.staff.get()),
            signature=self.sig.get().strip(), reindex_hours=max(1, int(self.hours.get() or 12)),
            skip_folders=split(self.skip.get()), superbill_root=self.sb_root.get().strip(),
            office_copy_folder=self.office.get().strip(), billing_folder=self.billing.get().strip(),
            initials=self.initials.get().strip().lstrip("*"),
            ai_read_chats=self.read_chats.get(),
            ai_daily_limit=_money(self.daily.get(), self.cfg.get("ai_daily_limit", 5.0)),
        )
        config.save(self.cfg)
        self.on_save(self.cfg)
        self.destroy()


class DraftDialog(tk.Toplevel):
    """Ask Claude to draft a reply, an appeal letter, or an explanation for the selected request."""

    def __init__(self, master, reader: "ai.ClaudeReader", item: dict):
        super().__init__(master)
        self.title(f"Draft with Claude · {item.get('name') or item.get('kind')}")
        self.transient(master)
        self.geometry("820x640")
        self.reader, self.item = reader, item
        f = ttk.Frame(self, padding=10)
        f.pack(fill="both", expand=True)
        top = ttk.Frame(f)
        top.pack(fill="x")
        ttk.Label(top, text="Draft:").pack(side="left")
        self.task = tk.StringVar(value="Appeal letter" if item.get("kind") in ("Appeal / downcode", "Denial") else "Reply to billing")
        ttk.Combobox(top, textvariable=self.task, values=list(ai.DRAFT_TASKS), state="readonly", width=20).pack(side="left", padx=6)
        self.go = ttk.Button(top, text="Draft", command=self._run)
        self.go.pack(side="left")
        self.state = ttk.Label(top, foreground="gray")
        self.state.pack(side="left", padx=8)
        ttk.Label(f, text="Supporting text (optional) - paste the visit note or EOB text here:").pack(anchor="w", pady=(8, 2))
        self.support = tk.Text(f, height=8, wrap="word", relief="solid", borderwidth=1)
        self.support.pack(fill="both", expand=False)
        ttk.Label(f, text="Draft (edit before using):").pack(anchor="w", pady=(8, 2))
        self.out = tk.Text(f, wrap="word", relief="solid", borderwidth=1)
        self.out.pack(fill="both", expand=True)
        b = ttk.Frame(f)
        b.pack(fill="x", pady=(6, 0))
        ttk.Button(b, text="Close", command=self.destroy).pack(side="right")
        ttk.Button(b, text="Copy", command=self._copy).pack(side="right", padx=6)

    def _run(self):
        self.go.configure(state="disabled")
        self.state.configure(text="Claude is writing…")
        task, support = self.task.get(), self.support.get("1.0", "end")

        def work():
            try:
                text, err = self.reader.draft(task, self.item, support), ""
            except ai.AIError as ex:
                text, err = "", str(ex)
            self.master.ui(lambda: self._done(text, err))

        threading.Thread(target=work, daemon=True).start()

    def _done(self, text: str, err: str):
        if not self.winfo_exists():
            return
        self.go.configure(state="normal")
        self.state.configure(text=err or "Done - review before sending.")
        if text:
            self.out.delete("1.0", "end")
            self.out.insert("1.0", text)

    def _copy(self):
        self.clipboard_clear()
        self.clipboard_append(self.out.get("1.0", "end").strip())
        self.state.configure(text="Copied.")


class AskDialog(tk.Toplevel):
    """A back-and-forth conversation with Claude about one case, with documents attached from this PC."""

    def __init__(self, master, reader: "ai.ClaudeReader", item: dict, attach: list[str]):
        super().__init__(master)
        name = item.get("name") or item.get("kind") or "case"
        self.title(f"Ask Claude · {name}")
        self.transient(master)
        self.geometry("900x740")
        self.reader, self.item, self.history = reader, item, []
        self.pending: list[str] = []
        f = ttk.Frame(self, padding=10)
        f.pack(fill="both", expand=True)
        facts = " · ".join(x for x in (item.get("kind"), name, f"Acct {item['account']}" if item.get("account") else "",
                                       f"DOS {item['dos_display']}" if item.get("dos_display") else "") if x)
        ttk.Label(f, text=facts or "No request selected - ask a general question.", font=("", 11, "bold")).pack(anchor="w")

        af = ttk.LabelFrame(f, text="Documents to send with the next question (read on this computer)", padding=6)
        af.pack(fill="x", pady=(8, 6))
        self.files = tk.Listbox(af, height=4)
        self.files.pack(side="left", fill="both", expand=True)
        bf = ttk.Frame(af)
        bf.pack(side="left", fill="y", padx=(6, 0))
        ttk.Button(bf, text="Add file…", command=self._browse).pack(fill="x")
        ttk.Button(bf, text="Add selected match", command=self._add_matches).pack(fill="x", pady=4)
        ttk.Button(bf, text="Remove", command=self._remove).pack(fill="x")
        for p in attach:
            self._add(p)

        self.chat = tk.Text(f, wrap="word", relief="solid", borderwidth=1, state="disabled")
        self.chat.pack(fill="both", expand=True)
        self.chat.tag_configure("you", foreground="#1d4ed8", font=("", 10, "bold"))
        self.chat.tag_configure("claude", foreground="#7c3aed", font=("", 10, "bold"))
        self.chat.tag_configure("note", foreground="#6b7280")
        self.chat.tag_configure("err", foreground="#b91c1c")
        self._say("note", "Examples: \"Why was this denied?\"  \"Does the note support 99215?\"  \"Write the appeal.\"  "
                          "\"What should we tell the patient?\"\n")

        qf = ttk.Frame(f)
        qf.pack(fill="x", pady=(6, 0))
        self.q = tk.Text(qf, height=3, wrap="word", relief="solid", borderwidth=1)
        self.q.pack(side="left", fill="x", expand=True)
        for seq in ("<Control-Return>", "<Command-Return>"):
            self.q.bind(seq, lambda e: (self._send(), "break")[1])
        self.go = ttk.Button(qf, text="Ask", command=self._send)
        self.go.pack(side="left", padx=(6, 0), fill="y")
        b = ttk.Frame(f)
        b.pack(fill="x", pady=(6, 0))
        self.state = ttk.Label(b, foreground="gray", text="Ctrl+Enter to send")
        self.state.pack(side="left")
        ttk.Button(b, text="Close", command=self.destroy).pack(side="right")
        ttk.Button(b, text="Copy last answer", command=self._copy).pack(side="right", padx=6)
        self.last = ""
        self.q.focus_set()

    def _add(self, path: str):
        if path and path not in self.pending:
            self.pending.append(path)
            self.files.insert("end", os.path.basename(path))

    def _browse(self):
        for p in filedialog.askopenfilenames(parent=self, title="Attach documents"):
            self._add(p)

    def _add_matches(self):
        hits = self.master._selected_hits()
        if not hits:
            self.state.configure(text="Select a file in the main window's match list first.")
        for h in hits:
            self._add(h.path)

    def _remove(self):
        for i in reversed(self.files.curselection()):
            self.files.delete(i)
            del self.pending[i]

    def _say(self, tag: str, text: str):
        self.chat.configure(state="normal")
        self.chat.insert("end", text, tag)
        self.chat.configure(state="disabled")
        self.chat.see("end")

    def _send(self):
        q = self.q.get("1.0", "end").strip()
        if not q or str(self.go.cget("state")) == "disabled":
            return
        files = list(self.pending)
        self._say("you", "\nYou: ")
        self._say("", q + "\n")
        if files:
            self._say("note", "  attached: " + ", ".join(os.path.basename(p) for p in files) + "\n")
        self.q.delete("1.0", "end")
        self.go.configure(state="disabled")
        self.state.configure(text="Claude is reading…")

        def work():
            try:
                answer, history = self.reader.ask(self.history, q, self.item, files)
                err = ""
            except ai.AIError as ex:
                answer, history, err = "", self.history, str(ex)
            self.master.ui(lambda: self._done(answer, history, err, files))

        threading.Thread(target=work, daemon=True).start()

    def _done(self, answer: str, history: list, err: str, sent: list[str]):
        if not self.winfo_exists():
            return
        self.go.configure(state="normal")
        if err:
            self._say("err", f"Couldn't get an answer: {err}\n")
            self.state.configure(text="Nothing was lost - fix the problem and ask again.")
            return
        self.history, self.last = history, answer
        for p in sent:  # sent once; Claude keeps them for the rest of the conversation
            i = self.pending.index(p)
            self.files.delete(i)
            del self.pending[i]
        self._say("claude", "\nClaude: ")
        self._say("", answer + "\n")
        self.state.configure(text="Review before acting on it.")

    def _copy(self):
        self.clipboard_clear()
        self.clipboard_append(self.last)
        self.state.configure(text="Copied.")


class AgentDialog(tk.Toplevel):
    """Claude works the request end to end; staff watch, approve actions, and get the result."""
    ICONS = {"tool": "Searching:", "read": "Reading:", "status": "-", "think": "Thinking:"}

    def __init__(self, master, reader: "ai.ClaudeReader", item: dict):
        super().__init__(master)
        from . import agent
        self.app, self.item = master, item
        self.title(f"Claude is working · {item.get('name') or item.get('kind')}")
        self.transient(master)
        self.geometry("920x760")
        self.cancel = threading.Event()
        self.answer, self.decided = False, threading.Event()
        f = ttk.Frame(self, padding=10)
        f.pack(fill="both", expand=True)
        facts = " · ".join(x for x in (item.get("kind"), item.get("name"), f"Acct {item['account']}" if item.get("account") else "",
                                       f"DOS {item['dos_display']}" if item.get("dos_display") else "") if x)
        ttk.Label(f, text=facts, font=("", 11, "bold")).pack(anchor="w")
        self.log = tk.Text(f, wrap="word", relief="solid", borderwidth=1, state="disabled")
        self.log.pack(fill="both", expand=True, pady=(8, 6))
        for tag, opts in {"step": {"foreground": "#374151"}, "think": {"foreground": "#6b7280"},
                          "head": {"font": ("", 11, "bold"), "foreground": "#7c3aed"}, "ok": {"foreground": "#047857"},
                          "err": {"foreground": "#b91c1c"}}.items():
            self.log.tag_configure(tag, **opts)

        self.ask = ttk.Frame(f, padding=8, relief="ridge")
        self.ask_text = ttk.Label(self.ask, justify="left", wraplength=820)
        self.ask_text.pack(anchor="w")
        ab = ttk.Frame(self.ask)
        ab.pack(anchor="e", pady=(6, 0))
        ttk.Button(ab, text="Skip", command=lambda: self._decide(False)).pack(side="right")
        ttk.Button(ab, text="Approve", command=lambda: self._decide(True)).pack(side="right", padx=6)

        b = ttk.Frame(f)
        b.pack(fill="x")
        self.state = ttk.Label(b, foreground="gray", text="Working…")
        self.state.pack(side="left")
        ttk.Button(b, text="Close", command=self._close).pack(side="right")
        self.done_btn = ttk.Button(b, text="Mark done", command=self._mark_done, state="disabled")
        self.done_btn.pack(side="right", padx=6)
        self.letter_btn = ttk.Button(b, text="Copy letter", command=lambda: self._copy(self.result.letter), state="disabled")
        self.letter_btn.pack(side="right")
        self.reply_btn = ttk.Button(b, text="Copy reply", command=lambda: self._copy(self.result.reply_to_billing), state="disabled")
        self.reply_btn.pack(side="right", padx=6)
        self.protocol("WM_DELETE_WINDOW", self._close)

        self.result = agent.Result()
        run = agent.Agent(reader, master.store, master.cfg, item, self._event, self._approve, self.cancel)
        threading.Thread(target=self._work, args=(run,), daemon=True).start()

    # worker-thread callbacks -------------------------------------------------
    def _event(self, kind: str, text: str):
        self.app.ui(lambda: self._write(f"{self.ICONS.get(kind, '•')} {text}\n", "think" if kind == "think" else "step"))

    def _approve(self, kind: str, text: str) -> bool:
        self.decided.clear()
        self.app.ui(lambda: self._show_ask(text))
        while not self.decided.wait(0.5):
            if self.cancel.is_set():
                return False
        return self.answer

    def _work(self, run):
        try:
            res, err = run.run(), ""
        except ai.AIError as ex:
            res, err = run.result, str(ex)
        self.app.ui(lambda: self._finished(res, err))

    # UI thread ----------------------------------------------------------------
    def _write(self, text: str, tag: str = ""):
        if not self.winfo_exists():
            return
        self.log.configure(state="normal")
        self.log.insert("end", text, tag)
        self.log.configure(state="disabled")
        self.log.see("end")

    def _show_ask(self, text: str):
        if not self.winfo_exists():
            return
        self.ask_text.configure(text="Claude wants to: " + text)
        self.ask.pack(fill="x", pady=(0, 6), before=self.state.master)
        self.state.configure(text="Waiting for your approval…")

    def _decide(self, yes: bool):
        self.answer = yes
        self.ask.pack_forget()
        self._write(("Approved\n" if yes else "Skipped\n"), "ok" if yes else "err")
        self.state.configure(text="Working…")
        self.decided.set()

    def _finished(self, res, err: str):
        if not self.winfo_exists():
            return
        self.result = res
        if err:
            self._write(f"\nCouldn't finish: {err}\n", "err")
            self.state.configure(text="Stopped.")
            return
        self._write("\nResult\n", "head")
        self._write(res.summary + "\n")
        if res.findings:
            self._write("\nEvidence\n", "head")
            self._write("".join(f"- {x}\n" for x in res.findings))
        if res.copied:
            self._write("\nCopied to the Missing Folder: " + ", ".join(res.copied) + "\n", "ok")
        if res.rules_added:
            self._write("\nAdded to the playbook: " + "; ".join(res.rules_added) + "\n", "ok")
        if res.reply_to_billing:
            self._write("\nReply to billing\n", "head")
            self._write(res.reply_to_billing + "\n")
            self.reply_btn.configure(state="normal")
        if res.letter:
            self._write("\nLetter\n", "head")
            self._write(res.letter + "\n")
            self.letter_btn.configure(state="normal")
        self.done_btn.configure(state="normal")
        self.state.configure(text=f"Claude recommends: {res.status}. Review before sending anything.")
        self.app.after_agent(self.item, res)

    def _copy(self, text: str):
        self.clipboard_clear()
        self.clipboard_append(text)
        self.state.configure(text="Copied - paste it where it goes.")

    def _mark_done(self):
        self.app.set_item_status(self.item.get("key"), "Done")
        self.state.configure(text="Marked done.")

    def _close(self):
        self.cancel.set()
        self.decided.set()
        self.destroy()


class PlaybookDialog(tk.Toplevel):
    """Edit the clinic playbook: rules, people and procedures Claude uses on every request. No patient information."""

    def __init__(self, master):
        super().__init__(master)
        self.title("Clinic Playbook")
        self.transient(master)
        self.geometry("820x680")
        f = ttk.Frame(self, padding=10)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text="What Claude should know about the clinic. Rules, people and procedures only - "
                          "never patient names, accounts or dates.", foreground="gray").pack(anchor="w")
        self.text = tk.Text(f, wrap="word", relief="solid", borderwidth=1, undo=True)
        self.text.pack(fill="both", expand=True, pady=6)
        self.text.insert("1.0", ai.load_playbook() or ai.PLAYBOOK_TEMPLATE)
        b = ttk.Frame(f)
        b.pack(fill="x")
        ttk.Button(b, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(b, text="Save", command=self._save).pack(side="right", padx=6)

    def _save(self):
        ai.save_playbook(self.text.get("1.0", "end").rstrip() + "\n")
        self.destroy()


# --------------------------------------------------------------------------- main window

class App(tk.Tk):
    ITEM_COLS = [("status", "Status", 80), ("received", "Received", 110), ("sender", "From", 120),
                 ("kind", "Request", 150), ("name", "Patient", 170), ("account", "Acct #", 70),
                 ("dos_display", "DOS", 90), ("payers", "Payer", 100)]
    FILE_COLS = [("kind", "Type", 150), ("name", "File", 320), ("modified", "Date", 100), ("folder", "Folder", 420)]

    def __init__(self, startup_note: str = ""):
        super().__init__()
        _window_icon(self)
        self.title(f"DizzyDoctor {VERSION} — classic tools")
        self.geometry("1400x820")
        self.minsize(1000, 600)
        self.cfg = config.load()
        from . import mips
        mips.FOLDER = self.cfg.get("mips_folder") or mips.DEFAULT_FOLDER
        self.store = Store(config.DB_PATH)
        self.items: dict[str, dict] = {}
        self.current: dict | None = None
        self.hits: dict[str, object] = {}
        self.jobs: queue.Queue = queue.Queue()
        self.index_cancel = threading.Event()
        self.indexing = False
        self.syncing = False
        self.mapping = False
        self.map_again = False
        self.attach_seq = 0
        self.sort_col, self.sort_rev = "received", True
        self.publishing = False
        self.offered_data = ""
        self.client = shared.is_client(self.cfg)

        self._style()
        self._build()
        self._load_items()
        if startup_note and not startup_note.startswith("Clinic data from"):   # that one is already in the status line
            self.status.set(startup_note + "  ·  " + self.status.get())
        self.after(100, self._poll)
        self.after(400, self._startup)

    # ---------------------------------------------------------------- layout
    def _style(self):
        s = ttk.Style(self)
        if "vista" in s.theme_names():
            s.theme_use("vista")
        s.configure("Treeview", rowheight=24)
        s.configure("Big.TButton", padding=(10, 6))

    def _build(self):
        from .dashboard_ui import AQUA, MIDNIGHT, BalancesTab, Dashboard, SuperbillsTab
        from .mips_ui import MipsTab
        head = tk.Frame(self, background=MIDNIGHT, padx=14, pady=9)
        head.pack(fill="x")
        tk.Label(head, text="DizzyDoctor", font=("Segoe UI", 15, "bold"), fg="white", bg=MIDNIGHT).pack(side="left")
        tk.Label(head, text="Ian Purcell, M.D., APC", font=("Segoe UI", 10), fg=AQUA, bg=MIDNIGHT).pack(side="left", padx=(10, 0))
        ttk.Button(head, text="Settings", command=self.open_settings).pack(side="right")
        self.index_btn = ttk.Button(head, text="Re-scan drive", command=self.start_index)
        self.index_btn.pack(side="right", padx=6)
        ttk.Button(head, text="Note", command=self.open_note).pack(side="right", padx=(6, 12))
        ttk.Button(head, text="Open chart", command=self.quick_find).pack(side="right", padx=(6, 0))
        self.quick = tk.StringVar()
        qe = ttk.Entry(head, textvariable=self.quick, width=34)
        qe.pack(side="right")
        qe.bind("<Return>", lambda e: self.quick_find())
        tk.Label(head, text="Find patient (name / Pt # / DOB):", fg="white", bg=MIDNIGHT).pack(side="right", padx=6)

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=6, pady=(6, 0))
        self.dash = Dashboard(self.nb, self)
        self.sb_tab = SuperbillsTab(self.nb, self)
        self.bal_tab = BalancesTab(self.nb, self)
        self.mips_tab = MipsTab(self.nb, self)
        billing = self.billing_tab = ttk.Frame(self.nb)
        self.nb.add(self.dash, text="  Dashboard  ")
        self.nb.add(self.sb_tab, text="  Superbills  ")
        self.nb.add(self.bal_tab, text="  Balances  ")
        self.nb.add(self.mips_tab, text="  MIPS 2026  ")
        self.nb.add(billing, text="  Billing requests  ")
        self._build_billing(billing)

        self.status = tk.StringVar()
        ttk.Label(self, textvariable=self.status, relief="sunken", anchor="w", padding=(8, 2)).pack(fill="x", side="bottom")
        self._update_index_status()
        self.after(300, self._link_tick)

    def _build_billing(self, parent):
        """The billing team's requests (pasted from the Teams chat), with the documents for each one."""
        bar = ttk.Frame(parent, padding=(4, 8, 4, 4))
        bar.pack(fill="x")
        ttk.Button(bar, text="Paste requests from Teams", command=self.paste_from_clipboard).pack(side="left")
        ttk.Label(bar, text="Show:").pack(side="left", padx=(20, 4))
        self.view = tk.StringVar(value=VIEWS[0])
        cb = ttk.Combobox(bar, textvariable=self.view, values=VIEWS, state="readonly", width=20)
        cb.pack(side="left")
        cb.bind("<<ComboboxSelected>>", lambda e: self._refresh_items())

        main = ttk.PanedWindow(parent, orient="horizontal")
        main.pack(fill="both", expand=True, padx=4, pady=4)

        left = ttk.Frame(main)
        self.tree = self._make_tree(left, self.ITEM_COLS, sortable=True)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._on_select())
        for tag, color in (("Answered", "#1d4ed8"), ("Done", "#9ca3af"), ("ours", "#6b7280")):
            self.tree.tag_configure(tag, foreground=color)
        ib = ttk.Frame(left)
        ib.pack(fill="x", pady=(4, 0))
        ttk.Button(ib, text="Mark done", command=lambda: self.set_status("Done")).pack(side="left")
        ttk.Button(ib, text="Reopen", command=lambda: self.set_status("Open")).pack(side="left", padx=4)
        ttk.Button(ib, text="Remove", command=self.remove_item).pack(side="left")
        self.count_lbl = ttk.Label(ib, foreground="gray")
        self.count_lbl.pack(side="right")
        main.add(left, weight=3)

        right = ttk.PanedWindow(main, orient="vertical")
        main.add(right, weight=4)

        det = ttk.Frame(right, padding=(6, 0))
        self.msg = tk.Text(det, height=8, wrap="word", relief="solid", borderwidth=1, font=("Segoe UI", 10) if IS_WIN else None)
        self.msg.pack(fill="both", expand=True)
        self.msg.tag_configure("hdr", foreground="#6b7280")
        self.msg.tag_configure("ans", foreground="#1d4ed8")
        self.msg.tag_configure("ai", foreground="#7c3aed")
        self.msg.tag_configure("warn", foreground="#b45309")
        self.msg.configure(state="disabled")

        fields = ttk.Frame(det)
        fields.pack(fill="x", pady=6)
        self.f_first, self.f_last, self.f_acct, self.f_dob, self.f_dos = (tk.StringVar() for _ in range(5))
        for col, (label, var, w) in enumerate((("First", self.f_first, 12), ("Last", self.f_last, 16), ("Acct #", self.f_acct, 9),
                                               ("DOB", self.f_dob, 11), ("DOS", self.f_dos, 24))):
            ttk.Label(fields, text=label).grid(row=0, column=col * 2, padx=(0 if col == 0 else 8, 3))
            e = ttk.Entry(fields, textvariable=var, width=w)
            e.grid(row=0, column=col * 2 + 1)
            e.bind("<Return>", lambda ev: self.run_search())
        ttk.Button(fields, text="Find records", command=self.run_search).grid(row=0, column=10, padx=(10, 0))
        right.add(det, weight=2)

        fr = ttk.Frame(right, padding=(6, 4, 6, 0))
        self.files = self._make_tree(fr, self.FILE_COLS)
        self.files.configure(selectmode="extended")
        self.files.bind("<Double-1>", lambda e: self.open_selected())
        self.files.tag_configure("strong", foreground="#047857")
        fb = ttk.Frame(fr)
        fb.pack(fill="x", pady=(4, 0))
        ttk.Button(fb, text="Open", command=self.open_selected).pack(side="left")
        ttk.Button(fb, text="Show in folder", command=lambda: self.open_selected(reveal=True)).pack(side="left", padx=4)
        ttk.Button(fb, text="Copy to Missing Folder", command=self.copy_to_missing).pack(side="left")
        ttk.Button(fb, text="Copy reply text", command=self.copy_reply).pack(side="left", padx=4)
        ttk.Button(fb, text="Superbill…", command=self.open_superbill).pack(side="left")
        ttk.Button(fb, text="Patient chart", command=self.open_record).pack(side="left", padx=4)
        self.files.tag_configure("attached", foreground="#1d4ed8")
        self.files_lbl = ttk.Label(fb, foreground="gray")
        self.files_lbl.pack(side="right")
        right.add(fr, weight=3)
        self.bind_all("<Control-Shift-V>", lambda e: self.paste_from_clipboard())
        self.bind_all("<Control-Shift-v>", lambda e: self.paste_from_clipboard())

    def _make_tree(self, parent, cols, sortable=False) -> ttk.Treeview:
        box = ttk.Frame(parent)
        box.pack(fill="both", expand=True)
        tree = ttk.Treeview(box, columns=[c for c, _, _ in cols], show="headings")
        for key, title, width in cols:
            tree.heading(key, text=title, command=(lambda k=key: self._sort(k)) if sortable else "")
            tree.column(key, width=width, stretch=key in ("name", "folder"))
        ys = ttk.Scrollbar(box, orient="vertical", command=tree.yview)
        xs = ttk.Scrollbar(box, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        tree.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        xs.grid(row=1, column=0, sticky="ew")
        box.rowconfigure(0, weight=1)
        box.columnconfigure(0, weight=1)
        return tree

    # ---------------------------------------------------------------- startup / indexing
    def _startup(self):
        if self.client:
            # This PC loads the main PC's published data when it starts (main()), so it doesn't scan the drive,
            # read Outlook or map patients itself; it only checks the server now and then for newer data.
            self.after(20 * 60 * 1000, self.check_for_new_data)
            return
        if shared.is_main(self.cfg):
            self.after(60 * 60 * 1000, self._hourly)
        if not self.cfg["search_folders"]:
            messagebox.showinfo("Welcome", "First, choose the folder(s) on the clinic drive where patient records are kept.\n\n"
                                "The app builds a private file-name index on this PC so searches are instant.", parent=self)
            self.open_settings()
            return
        n, when, roots = self.store.index_info()
        stale = not when or datetime.now() - when > timedelta(hours=self.cfg["reindex_hours"])
        # One at a time: the Outlook copy first (about a minute), then the drive scan, then the patient map.
        # Running both at once made the first start of 1.5.0 look stuck.
        need_scan = stale or roots != self.cfg["search_folders"] or n == 0
        self.start_outlook_sync(then=self.start_index if need_scan else None)

    # ---------------------------------------------------------------- patient map
    def start_mapping(self):
        """Rebuild the patient directory + file links in the background, once the scan and sync are both done."""
        if self.indexing or self.syncing:
            self.map_again = True  # runs when the other job reports back
            return
        if self.mapping:
            self.map_again = True
            return
        self.mapping, self.map_again = True, False

        def work():
            from . import balances, people
            try:
                got = balances.import_folder(self.store, self.cfg.get("veradigm_results_folder") or balances.DEFAULT_FOLDER)
                if got:
                    log.info("Veradigm balances: %s new snapshots imported", got)
            except Exception:
                log.exception("Veradigm balance import failed")
            try:
                from . import veradigm_reports
                got = veradigm_reports.import_folder(self.store, self.cfg.get("veradigm_reports_folder")
                                                     or veradigm_reports.DEFAULT_FOLDER)
                if got:
                    log.info("Veradigm reports: %s vouchers imported", got)
            except Exception:
                log.exception("Veradigm report import failed")
            try:
                n, links = people.build(self.store, progress=lambda m: self.jobs.put(("status", m)))
                log.info("Patient map: %s patients, %s file links", n, links)
                self.jobs.put(("mapped", n, links))
            except Exception as ex:
                self.jobs.put(("mapped", -1, 0))
                log.exception("Patient map failed")
                self.jobs.put(("status", f"Patient map not built: {ex}"))

        threading.Thread(target=work, daemon=True).start()

    # ---------------------------------------------------------------- clinic-wide install (shared.py)
    def maybe_publish(self, force: bool = False):
        """Main PC: copy the fresh data to the server for the other PCs (when it changed, at most hourly)."""
        if not shared.is_main(self.cfg) or self.publishing or self.indexing or self.syncing or self.mapping:
            return
        share = shared.share_folder(self.cfg)
        if not os.path.isdir(share):
            log.warning("Can't reach %s - clinic data not published", share)
            return
        try:
            import json
            fp = shared.fingerprint(self.store.reader())
            last = json.loads(self.store.meta_get("shared_published") or "{}")
        except Exception:
            log.exception("Couldn't check the published data")
            return
        if not force:
            if last.get("fingerprint") == fp:
                return
            mins = float(self.cfg.get("publish_minutes", 60) or 60)
            if last.get("at") and datetime.now() - datetime.fromisoformat(last["at"]) < timedelta(minutes=mins):
                return
        self.publishing = True

        def work():
            import json
            import time
            try:
                t0 = time.time()
                info = shared.publish(config.DB_PATH, share)
                self.store.meta_set("shared_published", json.dumps({"at": datetime.now().isoformat(timespec="seconds"),
                                                                    "fingerprint": info["fingerprint"], "file": info["file"]}))
                log.info("Published clinic data %s (%.0f MB) in %.0f s", info["file"], info["size"] / 1e6, time.time() - t0)
                self.jobs.put(("status", f"Clinic data published to the server for the other PCs ({info['size'] / 1e6:,.0f} MB)."))
            except Exception as ex:
                log.exception("Publishing clinic data failed")
                self.jobs.put(("status", f"Couldn't publish the clinic data to the server: {ex}"))
            finally:
                self.publishing = False

        threading.Thread(target=work, daemon=True).start()

    def _hourly(self):
        """Main PC: pick up Outlook changes, new Veradigm files and the map through the day, then publish."""
        if not (self.syncing or self.indexing or self.mapping):
            self.start_outlook_sync(then=self.start_mapping)
        self.after(60 * 60 * 1000, self._hourly)

    def check_for_new_data(self, reschedule: bool = True):
        """Other PCs: copy newer published data down in the background and offer to load it."""
        def work():
            try:
                info = shared.prefetch(self.cfg, config.DB_PATH)
                if info:
                    self.ui(lambda: self._offer_new_data(info))
                elif not reschedule:
                    self.jobs.put(("status", "This PC already has the newest clinic data."))
            except Exception as ex:
                log.exception("Checking for new clinic data failed")
                if not reschedule:
                    self.jobs.put(("status", f"Couldn't check the server: {ex}"))

        threading.Thread(target=work, daemon=True).start()
        if reschedule:
            self.after(20 * 60 * 1000, self.check_for_new_data)

    def _offer_new_data(self, info: dict):
        if self.offered_data == info["stamp"]:
            return
        self.offered_data = info["stamp"]
        when = shared._when(info)
        if messagebox.askyesno("New clinic data", f"Newer clinic data is ready (from {when}).\n\n"
                               "Load it now? DizzyDoctor closes and opens again in a few seconds.", parent=self):
            self.restart_app()
        else:
            self.status.set(f"Newer clinic data ({when}) is ready - it loads the next time DizzyDoctor starts.")

    def restart_app(self):
        shared.restart(after_pid=os.getpid())
        self.destroy()

    # ---------------------------------------------------------------- the main window opens tabs here
    TABS = ("dashboard", "superbills", "balances", "mips", "billing")

    def show_tab(self, tab: str) -> None:
        """Show a tab (or one of the worklist windows) and come to the front: the main window's top bar asks for it."""
        frames = {"dashboard": self.dash, "superbills": self.sb_tab, "balances": self.bal_tab, "mips": self.mips_tab,
                  "billing": self.billing_tab}
        if tab in frames:
            self.nb.select(frames[tab])
        elif tab == "today":
            self.open_today()
        elif tab == "auth":
            self.open_auth_worklist()
        elif tab == "patients":
            self.open_patients()
        self.deiconify()
        self.lift()
        self.attributes("-topmost", True)            # past other apps' windows; then back to normal stacking
        self.after(200, lambda: self.attributes("-topmost", False))
        self.focus_force()

    def _link_tick(self) -> None:
        try:
            classic_link.beat(config.DATA_DIR)
            req = classic_link.take_request(config.DATA_DIR)
            if req is not None:
                self.show_tab(str(req.get("tab") or ""))
        except Exception:
            log.exception("Office window link failed")
        self.after(1500, self._link_tick)

    def destroy(self):
        classic_link.closed(config.DATA_DIR)
        super().destroy()

    def open_today(self):
        from .worklists_ui import TodayWindow
        TodayWindow(self)

    def open_auth_worklist(self):
        from .worklists_ui import AuthWorklistWindow
        AuthWorklistWindow(self)

    def open_patients(self):
        from .patients_ui import PatientsWindow
        PatientsWindow(self)

    # ---------------------------------------------------------------- Outlook cache
    def start_outlook_sync(self, force: bool = False, then=None):
        """Copy Outlook calendars into the local cache in the background (full weekly, else only changes)."""
        from . import outlook_cache, schedule
        if self.syncing or schedule.outlook_unavailable_reason():
            if then and not self.syncing:
                then()  # no Outlook on this PC: whatever was waiting on the sync goes ahead
            return
        full_at = self.store.meta_get("outlook_full_sync_at")
        full = force or not full_at or datetime.now() - datetime.fromisoformat(full_at) > timedelta(days=7)
        self.syncing = True

        def work():
            try:
                n = outlook_cache.sync(self.store, full=full, progress=lambda m: self.jobs.put(("status", m)))
                self.jobs.put(("synced", n, then))
            except Exception as ex:  # Outlook closed, profile prompt...
                log.exception("Outlook sync failed")
                self.jobs.put(("synced", -1, then))
                self.jobs.put(("status", f"Outlook not read: {ex}"))

        threading.Thread(target=work, daemon=True).start()

    def start_index(self):
        if self.indexing:
            self.index_cancel.set()
            return
        if not self.cfg["search_folders"]:
            self.open_settings()
            return
        missing = [p for p in self.cfg["search_folders"] if not os.path.isdir(p)]
        if missing:
            self.status.set("Can't reach: " + "; ".join(missing) + " - is the drive connected?")
        self.indexing = True
        self.index_cancel.clear()
        self.index_btn.configure(text="Stop scan")

        def work():
            try:
                n = self.store.rebuild_index(self.cfg["search_folders"], self.cfg["skip_folders"],
                                             progress=lambda c, f: self.jobs.put(("progress", c, f)), cancel=self.index_cancel)
                log.info("Drive scan: %s files in %s", n, self.cfg["search_folders"])
                self.jobs.put(("indexed", n))
            except Exception as ex:  # keep the UI alive whatever happens on the share
                log.exception("Drive scan failed")
                self.jobs.put(("error", f"Scan failed: {ex}"))

        threading.Thread(target=work, daemon=True).start()

    def _update_index_status(self):
        from . import outlook_cache
        n, when, _ = self.store.index_info()
        appts, synced = outlook_cache.status(self.store)
        from . import people
        ol = f" · Outlook: {appts:,} bookings" + (f" (synced {datetime.fromisoformat(synced):%m/%d %I:%M %p})" if synced else "") if appts else ""
        pts = people.count(self.store)
        pm = f" · {pts:,} patients mapped" if pts else ""
        origin = ""
        if shared.is_client(self.cfg):
            m = shared.marker(config.DB_PATH)
            origin = f"Clinic data from {shared._when(m)} · " if m.get("created") else "No clinic data loaded from the server yet · "
        self.status.set(origin + f"{n:,} files indexed" + (f" · last scan {when:%m/%d %I:%M %p}" if when else " · drive not scanned yet") + ol + pm)
        if getattr(self, "dash", None) is not None:
            self.dash.refresh()

    def _poll(self):
        try:
            while True:
                job = self.jobs.get_nowait()
                kind = job[0]
                if kind == "progress":
                    self.status.set(f"Scanning drive… {job[1]:,} files  ·  {job[2]}")
                elif kind == "indexed":
                    self.indexing = False
                    self.index_btn.configure(text="Re-scan drive")
                    self._update_index_status()
                    if job[1] < 0:
                        self.status.set("Scan stopped. " + self.status.get())
                    else:
                        self.start_mapping()
                        if self.current:
                            self.run_search()
                elif kind == "error":
                    self.indexing = False
                    self.index_btn.configure(text="Re-scan drive")
                    self.status.set(job[1])
                elif kind == "hits":
                    self._show_hits(*job[1:])
                elif kind == "status":
                    self.status.set(job[1])
                elif kind == "mapped":
                    self.mapping = False
                    if job[1] >= 0:
                        self._update_index_status()
                    if self.map_again:
                        self.start_mapping()
                    elif job[1] >= 0:
                        self.maybe_publish()  # main PC: the refreshed data goes to the server for the other PCs
                elif kind == "synced":
                    self.syncing = False
                    from . import people
                    if job[1] >= 0:
                        self._update_index_status()
                    if job[2]:
                        job[2]()  # e.g. the drive scan that waits for the sync at startup (it then maps)
                    if (job[1] > 0 or self.map_again or (job[1] == 0 and not people.count(self.store))) and not self.mapping:
                        self.start_mapping()  # new or changed bookings (or no map yet)
                    elif not self.mapping:
                        self.maybe_publish()
                elif kind == "attached":
                    self._show_attached(*job[1:])
                elif kind == "call":
                    job[1]()
                elif kind == "imported":
                    self._apply_requests(*job[1:])
        except queue.Empty:
            pass
        self.after(100, self._poll)

    # ---------------------------------------------------------------- importing chat text
    def paste_from_clipboard(self):
        try:
            text = self.clipboard_get()
        except tk.TclError:
            text = ""
        if not text.strip():
            messagebox.showinfo("Nothing to paste", "In Teams, click in the chat, press Ctrl+A then Ctrl+C, then click this button again.", parent=self)
            return
        self.import_text(text)

    def import_file(self):
        p = filedialog.askopenfilename(parent=self, filetypes=[("Text", "*.txt"), ("All files", "*.*")])
        if p:
            with open(p, encoding="utf-8", errors="replace") as f:
                self.import_text(f.read())

    def ui(self, fn):
        """Run fn on the UI thread. Worker threads must use this instead of touching widgets."""
        self.jobs.put(("call", fn))


    def _reader(self, for_patient_data: bool = True):
        """A Claude client, or None until a key is saved (which confirms the BAA)."""
        key = ai.get_key()
        if not key or (for_patient_data and not self.cfg["ai_baa_confirmed"]):
            return None
        meter = ai.Meter(self.store.meta_get, self.store.meta_set, self.cfg.get("ai_daily_limit", 5.0),
                         on_change=lambda spent, limit: self.jobs.put(
                             ("status", f"Claude today: ${spent:.2f}" + (f" of ${limit:.2f}" if limit else ""))))
        return ai.ClaudeReader(key, self.store.get_ai, self.store.put_ai, self.cfg["staff_names"], self.cfg["signature"],
                               meter=meter)

    def import_text(self, text: str):
        reader = self._reader() if self.cfg["ai_read_chats"] else None
        if reader is None:
            self._apply_requests(parse_requests(text, today=date.today()), "")
            return
        self.status.set("Claude is reading the chat…")

        def work():
            try:
                reqs, note = parse_requests(text, today=date.today(), ai=reader.extract), " · read by Claude"
            except (ai.AIError, ValueError, KeyError) as ex:
                reqs, note = parse_requests(text, today=date.today()), f" · Claude unavailable ({ex}); used built-in rules"
            self.jobs.put(("imported", reqs, note))

        threading.Thread(target=work, daemon=True).start()

    def _apply_requests(self, reqs, note: str):
        new = 0
        for r in reqs:
            d = request_to_dict(r)
            existed = r.key in self.items
            status, note = self.store.upsert_item(r.key, d)
            if d["answer"] and status == "Open":
                status = "Answered"
                self.store.set_status(r.key, status)
            d.update(key=r.key, status=status, note=note)
            self.items[r.key] = d
            new += not existed
        self._refresh_items()
        self.status.set(f"Read {len(reqs)} item(s) from the chat · {new} new{note}")

    def _load_items(self):
        self.items = {d["key"]: d for d in self.store.all_items()}
        self._refresh_items()

    # ---------------------------------------------------------------- worklist
    def _is_ours(self, d: dict) -> bool:
        staff = {s.lower() for s in self.cfg["staff_names"]}
        return d.get("sender", "").lower() in staff

    def _visible(self, d: dict) -> bool:
        v, st = self.view.get(), d["status"]
        if v == "Open requests":
            return st == "Open" and not self._is_ours(d)
        if v == "All open (incl. ours)":
            return st == "Open"
        if v == "Answered":
            return st == "Answered"
        if v == "Done":
            return st == "Done"
        return True

    def _sort(self, col):
        self.sort_rev = not self.sort_rev if self.sort_col == col else False
        self.sort_col = col
        self._refresh_items()

    def _refresh_items(self):
        sel = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        rows = [d for d in self.items.values() if self._visible(d)]
        rows.sort(key=lambda d: str(d.get(self.sort_col, "")), reverse=self.sort_rev)
        for d in rows:
            rec = datetime.fromisoformat(d["received"]).strftime("%a %m/%d %I:%M%p") if d.get("received") else ""
            vals = [d["status"], rec, d["sender"], d["kind"], d["name"], d["account"], d["dos_display"], ", ".join(d["payers"])]
            tags = [d["status"]] + (["ours"] if self._is_ours(d) else [])
            self.tree.insert("", "end", iid=d["key"], values=vals, tags=tags)
        keep = [k for k in sel if self.tree.exists(k)]
        if keep:
            self.tree.selection_set(keep)
        open_n = sum(1 for d in self.items.values() if d["status"] == "Open" and not self._is_ours(d))
        self.count_lbl.configure(text=f"{len(rows)} shown · {open_n} open requests")

    def _on_select(self):
        sel = self.tree.selection()
        if not sel:
            return
        d = self.items[sel[0]]
        if self.current is d:
            return
        self.current = d
        self.msg.configure(state="normal")
        self.msg.delete("1.0", "end")
        self.msg.insert("end", f"{d['sender']}  ·  {d['when']}\n", "hdr")
        self.msg.insert("end", d["text"] + "\n")
        extra = [x for x in (("Codes: " + ", ".join(d["codes"])) if d["codes"] else "",
                              ("Visit/provider: " + d["visit_code"]) if d["visit_code"] else "",
                              ("Amounts: $" + ", $".join(d["amounts"])) if d["amounts"] else "") if x]
        if extra:
            self.msg.insert("end", "\n" + "   ·   ".join(extra) + "\n", "hdr")
        if d.get("summary"):
            self.msg.insert("end", f"\nSummary: {d['summary']}\n", "ai")
        if d.get("missing"):
            self.msg.insert("end", "Needs attention: " + "; ".join(d["missing"]) + "\n", "warn")
        if d.get("suggested_reply"):
            self.msg.insert("end", f"Suggested reply: {d['suggested_reply']}\n", "ai")
        if d.get("answer"):
            self.msg.insert("end", f"\nReply from {d['answered_by']}:\n{d['answer']}\n", "ans")
        if d.get("note"):
            self.msg.insert("end", f"\nNote: {d['note']}\n", "hdr")
        self.msg.configure(state="disabled")
        self.f_first.set(d["first"])
        self.f_last.set(d["last"])
        self.f_acct.set(d["account"])
        self.f_dob.set(date.fromisoformat(d["dob"]).strftime("%m/%d/%Y") if d["dob"] else "")
        self.f_dos.set(", ".join(date.fromisoformat(x).strftime("%m/%d/%Y") for x in d["dos"]))
        self.run_search()

    def set_status(self, status: str):
        for k in self.tree.selection():
            self.store.set_status(k, status)
            self.items[k]["status"] = status
        self._refresh_items()

    def remove_item(self):
        sel = self.tree.selection()
        if sel and messagebox.askyesno("Remove", f"Remove {len(sel)} item(s) from the list?", parent=self):
            for k in sel:
                self.store.delete_item(k)
                self.items.pop(k, None)
            self.current = None
            self._refresh_items()

    # ---------------------------------------------------------------- searching the drive
    def chart_context(self):
        """What the chart screen needs: the clinic's data through the app."""
        from .charting.providers import AppProvider
        from .charting.server import ChartContext
        return ChartContext(self.cfg, config.DATA_DIR, self.store, provider=AppProvider(self.store, self.cfg))

    def open_note(self, patient=None, note_path: str = ""):
        """The chart screen: a new progress note on the clinic's form, or an existing note."""
        from .charting.server import launch
        params = {}
        if note_path:
            params["path"] = note_path
        elif patient is not None:
            params = {"name": "1", "last": patient.last, "first": patient.first, "id": patient.id,
                      "dob": patient.dob.strftime("%m/%d/%Y") if patient.dob else "", "acct": patient.acct or ""}
        try:
            launch(self.chart_context(), **params)
        except Exception as ex:
            log.exception("Couldn't open the chart screen")
            messagebox.showerror("Note", f"Couldn't open the chart screen: {ex}", parent=self)

    def quick_find(self):
        """Header search: one patient opens their chart; several open All patients, filtered to the search."""
        q = self.quick.get().strip()
        if not q:
            self.open_patients()
            return
        try:
            found = people.search(self.store, q, limit=50)
        except Exception:
            found = []
        if len(found) == 1:
            from .record_ui import PatientRecord
            p = found[0]
            PatientRecord(self, p.first, p.last, p.dob, p.acct, patient_id=p.id)
            return
        from .patients_ui import PatientsWindow
        PatientsWindow(self).q.set(q)

    def open_record(self):
        """Complete record for the patient in the search boxes (every visit, note and document)."""
        from .record_ui import PatientRecord
        first, last = self.f_first.get().strip(), self.f_last.get().strip()
        acct = re.sub(r"\D", "", self.f_acct.get())
        if not (last or acct):
            messagebox.showinfo("Patient record", "Type a patient name (or account #) first.", parent=self)
            return
        dobs = parse_dates(self.f_dob.get())
        PatientRecord(self, first.title(), last.title(), dobs[0] if dobs else None, acct)

    def run_search(self):
        first, last, acct = self.f_first.get().strip(), self.f_last.get().strip(), re.sub(r"\D", "", self.f_acct.get())
        dos, dobs = parse_dates(self.f_dos.get()), parse_dates(self.f_dob.get())
        hints = (self.current or {}).get("hints", [])
        terms = search_terms(first, last, acct)
        if not terms:
            self._show_hits([], "Enter an account # or last name to search.")
            return
        self.files_lbl.configure(text="Searching…")
        self.attach_seq += 1
        seq = self.attach_seq

        def work():
            rows = self.store.find_files(terms)
            hits = rank(rows, first, last, acct, dos, dobs[0] if dobs else None, hints)
            self.jobs.put(("hits", hits, ""))
            if dos and (last or acct) and self.cfg.get("superbill_root"):
                self._attach_visit(seq, first, last, acct, dos[0], dobs[0] if dobs else None,
                                   (self.current or {}).get("visit_code", ""))

        threading.Thread(target=work, daemon=True).start()

    def _attach_visit(self, seq, first, last, acct, dos, dob, code):
        """Background: find the visit's superbill page + progress note and pin them to the file list."""
        from . import attach, notes
        found = []
        try:
            if last:
                note = notes.note_for_visit(notes.find_notes(self.store, self.cfg, last, first), dos)
                if note:
                    found.append(("note", note.path, f"{note.kind.capitalize()} progress note {dos:%m/%d/%y}"))
            res = attach.find_superbill(self.store, self.cfg, last, first, dos, dob, acct, code,
                                        progress=lambda m: self.jobs.put(("status", m)))
            if res.path:
                why = f"Superbill {os.path.basename(res.batch)} p{res.page}" + ("" if res.confident else " - CHECK: matched by schedule position only")
                found.insert(0, ("sb", res.path, why))
            self.jobs.put(("status", " · ".join(res.notes[-2:])))
        except Exception as ex:
            self.jobs.put(("status", f"Superbill lookup failed: {ex}"))
        self.jobs.put(("attached", seq, found))

    def _show_attached(self, seq, found):
        if seq != self.attach_seq:
            return  # the user has moved on to another request
        for i, (kind, path, why) in enumerate(found):
            iid = f"att{i}"
            if self.files.exists(iid):
                self.files.delete(iid)
            st = os.stat(path)
            h = Hit(100, path, os.path.basename(path), os.path.dirname(path), st.st_size, st.st_mtime, why)
            self.hits[iid] = h
            label = ("Superbill" + (" - check page" if "CHECK" in why else "")) if kind == "sb" else "Progress note"
            self.files.insert("", i, iid=iid, values=(label, h.name, datetime.fromtimestamp(h.mtime).strftime("%m/%d/%Y"), h.folder),
                              tags=("attached",))
        if found:
            self.files.selection_set(f"att0")
            self.files_lbl.configure(text=f"Visit documents attached · {len(self.hits) - len(found)} other file(s) for this patient")

    def _show_hits(self, hits, msg):
        self.files.delete(*self.files.get_children())
        self.hits = {}
        for i, h in enumerate(hits):
            iid = str(i)
            self.hits[iid] = h
            self.files.insert("", "end", iid=iid, values=(people.kind_of(h.path), h.name, datetime.fromtimestamp(h.mtime).strftime("%m/%d/%Y"), h.folder),
                              tags=("strong",) if h.score >= 70 else ())
        if hits:
            self.files.selection_set("0")
        n, when, _ = self.store.index_info()
        self.files_lbl.configure(text=msg or (f"{len(hits)} file(s) for this patient" if hits else
                                               ("No files found for this patient." if n else "Drive not scanned yet - click Re-scan drive.")))

    def _selected_hits(self):
        return [self.hits[i] for i in self.files.selection() if i in self.hits]

    def open_selected(self, reveal=False):
        for h in self._selected_hits()[:5]:
            try:
                open_path(h.path, reveal)
            except OSError as ex:
                messagebox.showerror("Can't open", str(ex), parent=self)

    def copy_paths_to_missing(self, paths: list[str], first: str, last: str, acct: str, parent=None) -> list[str]:
        """Copy files into the Missing Folder (optionally a per-patient subfolder). Returns the new names."""
        parent = parent or self
        dest = self.cfg["missing_folder"]
        if not dest or not os.path.isdir(dest):
            messagebox.showwarning("Missing Folder", "Set the Missing Folder location in Settings first.", parent=parent)
            return []
        if self.cfg["per_patient_subfolder"]:
            name = ", ".join(x for x in (last.strip().title(), first.strip().title()) if x)
            dest = os.path.join(dest, re.sub(r'[<>:"/\\|?*]', "", f"{name} {re.sub(r'[^0-9]', '', acct)}".strip()))
            os.makedirs(dest, exist_ok=True)
        copied = []
        for path in paths:
            base, ext = os.path.splitext(os.path.basename(path))
            target, n = os.path.join(dest, base + ext), 1
            while os.path.exists(target):
                target, n = os.path.join(dest, f"{base} ({n}){ext}"), n + 1
            try:
                shutil.copy2(path, target)
                copied.append(os.path.basename(target))
            except OSError as ex:
                messagebox.showerror("Copy failed", f"{os.path.basename(path)}\n{ex}", parent=parent)
        return copied

    def copy_to_missing(self):
        hits = self._selected_hits()
        if not hits:
            return
        copied = self.copy_paths_to_missing([h.path for h in hits], self.f_first.get(), self.f_last.get(), self.f_acct.get())
        if not copied:
            return
        self.files_lbl.configure(text=f"Copied {len(copied)} file(s) to the Missing Folder")
        cur = self.current
        if cur and cur.get("key"):
            note = (cur.get("note", "") + f"\n{datetime.now():%m/%d %I:%M%p} copied: " + ", ".join(copied)).strip()
            self.store.set_status(cur["key"], cur["status"], note)
            cur["note"] = note
            if messagebox.askyesno("Copied", f"Copied {len(copied)} file(s).\n\nCopy a reply for Teams to the clipboard and mark this request done?", parent=self):
                self._to_clipboard(reply_text(cur, copied, self.cfg["signature"]))
                self.set_status("Done")

    def copy_reply(self):
        cur = self.current or {}
        uploaded = ["yes"] if "copied:" in cur.get("note", "") else []
        text = cur.get("suggested_reply") if cur.get("suggested_reply") and not uploaded else reply_text(cur, uploaded, self.cfg["signature"])
        self._to_clipboard(text if cur else "")
        self.files_lbl.configure(text="Reply copied - paste it into Teams (Ctrl+V)")

    def _to_clipboard(self, text: str):
        self.clipboard_clear()
        self.clipboard_append(text)

    # ---------------------------------------------------------------- superbill workflow
    def open_superbill(self):
        cur = self.current
        if not cur or not (cur.get("dos") or self.f_dos.get().strip()):
            messagebox.showinfo("Superbill", "Select a request with a date of service (or type one in the DOS box) first.", parent=self)
            return
        if not self.cfg.get("superbill_root"):
            messagebox.showinfo("Superbill", "Set the superbill folder in Settings first.", parent=self)
            self.open_settings()
            return
        item = dict(cur)
        # Use what's in the search boxes, so a quick-find or a corrected name works too.
        item.update(first=self.f_first.get().strip(), last=self.f_last.get().strip(),
                    account=re.sub(r"\D", "", self.f_acct.get()))
        dos = parse_dates(self.f_dos.get())
        if dos:
            item["dos"] = [d.isoformat() for d in dos]
            item["dos_display"] = ", ".join(d.strftime("%m/%d/%Y") for d in dos)
        dob = parse_dates(self.f_dob.get())
        item["dob"] = dob[0].isoformat() if dob else ""
        from .superbill_ui import SuperbillDialog  # PyMuPDF/OCR load only when needed
        SuperbillDialog(self, item)

    def after_amendment(self, item: dict, prov, att, saved: list[str], sent_to_billing: bool):
        key = item.get("key")
        if not key or key not in self.items:
            return
        d = self.items[key]
        line = (f"{datetime.now():%m/%d %I:%M%p} amended S/B: {prov.name}" + (f", attending {att.name}" if att else "")
                + ("; saved: " + "; ".join(os.path.basename(p) for p in saved) if saved else ""))
        note = (d.get("note", "") + "\n" + line).strip()
        status = "Done" if sent_to_billing else d["status"]  # done once billing has the copy
        self.store.set_status(key, status, note)
        d.update(note=note, status=status)
        self._refresh_items()

    def open_agent(self):
        cur = self.current
        if not cur or not cur.get("key"):
            messagebox.showinfo("Work it", "Select a request in the list first.", parent=self)
            return
        reader = self._reader()
        if reader is None:
            messagebox.showinfo("Work it", "Add the Claude API key in Settings first.", parent=self)
            return
        AgentDialog(self, reader, cur)

    def after_agent(self, item: dict, res):
        """Keep Claude's outcome with the request: a note line and the suggested reply."""
        key = item.get("key")
        if not key or key not in self.items:
            return
        d = self.items[key]
        note = (d.get("note", "") + f"\n{datetime.now():%m/%d %I:%M%p} Claude: {res.summary}").strip()
        self.store.set_status(key, d["status"], note)
        d.update(note=note, suggested_reply=res.reply_to_billing or d.get("suggested_reply", ""))

    def set_item_status(self, key: str | None, status: str):
        if key and key in self.items:
            self.store.set_status(key, status)
            self.items[key]["status"] = status
            self._refresh_items()

    def open_ask(self):
        reader = self._reader()
        if reader is None:
            messagebox.showinfo("Ask Claude", "Add the Claude API key in Settings first.", parent=self)
            return
        cur = self.current if self.current and self.current.get("key") else {}
        AskDialog(self, reader, cur, [h.path for h in self._selected_hits()[:3]] if cur else [])

    def open_draft(self):
        cur = self.current
        if not cur or not cur.get("key"):
            messagebox.showinfo("Draft with Claude", "Select a request in the list first.", parent=self)
            return
        reader = self._reader()
        if reader is None:
            messagebox.showinfo("Draft with Claude", "Add the Claude API key in Settings first.", parent=self)
            return
        DraftDialog(self, reader, cur)

    def open_settings(self):
        def saved(cfg):
            changed = cfg["search_folders"] != self.cfg["search_folders"]
            self.cfg = cfg
            self._refresh_items()
            if changed or self.store.index_info()[0] == 0:
                self.start_index()
        SettingsDialog(self, self.cfg, saved)


def _setup_log():
    """app.log next to the index (%LOCALAPPDATA%\\DizzyDoctor): sync/scan/map timings and errors, no patient data."""
    import logging.handlers
    try:
        os.makedirs(config.DATA_DIR, exist_ok=True)
        h = logging.handlers.RotatingFileHandler(os.path.join(config.DATA_DIR, "app.log"), maxBytes=1_000_000,
                                                 backupCount=2, encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(h)
        log.setLevel(logging.INFO)
    except OSError:
        pass


def _window_icon(win) -> None:
    """The DizzyDoctor icon on Tk windows and every window opened after them. Without it Windows shows Tk's feather;
    a Mac shows the app bundle's icon either way."""
    ico = config.asset("DizzyDoctor.ico") if sys.platform.startswith("win") else ""
    if ico:
        try:
            win.iconbitmap(default=ico)
        except tk.TclError:
            pass


def _with_window(title: str, fn):
    """Run fn(progress) in the background behind a small progress window; returns fn's result (or raises its error)."""
    root = tk.Tk()
    _window_icon(root)
    root.title("DizzyDoctor")
    root.resizable(False, False)
    frame = ttk.Frame(root, padding=18)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text=title, font=("Segoe UI", 11)).pack(anchor="w")
    bar = ttk.Progressbar(frame, length=380, mode="indeterminate")
    bar.pack(pady=(10, 4))
    bar.start(12)
    detail = ttk.Label(frame, text="", foreground="gray")
    detail.pack(anchor="w")
    root.update_idletasks()
    root.geometry(f"+{(root.winfo_screenwidth() - root.winfo_reqwidth()) // 2}+{(root.winfo_screenheight() - root.winfo_reqheight()) // 3}")
    box: dict = {}
    prog: dict = {}

    def progress(done, total):
        prog["v"] = (done, total)

    def work():
        try:
            box["result"] = fn(progress)
        except BaseException as ex:          # handed back to the caller below
            box["error"] = ex

    t = threading.Thread(target=work, daemon=True)
    t.start()

    def poll():
        if "v" in prog:
            done, total = prog["v"]
            if str(bar.cget("mode")) != "determinate":
                bar.stop()
                bar.configure(mode="determinate", maximum=max(total, 1))
            bar.configure(value=done)
            detail.configure(text=f"{done / 1e6:,.0f} of {total / 1e6:,.0f} MB")
        if t.is_alive():
            root.after(150, poll)
        else:
            root.destroy()

    root.after(150, poll)
    root.mainloop()
    if "error" in box:
        raise box["error"]
    return box.get("result")


def main():
    _setup_log()
    log.info("DizzyDoctor %s started", VERSION)
    if IS_WIN:
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    if "--after" in sys.argv:                      # restarted to load new data: let the old window close first
        try:
            shared.wait_for_exit(int(sys.argv[sys.argv.index("--after") + 1]))
        except (IndexError, ValueError):
            pass
    cfg = config.load()
    upd = None
    if "--no-update" not in sys.argv:              # the updater couldn't swap the .exe: just open this version
        try:
            upd = shared.newer_build(cfg)
        except Exception:
            log.exception("Couldn't check the server for a newer version")
        busy = shared.others_running(sys.executable) if upd else 0
        if busy:                                   # e.g. the superbill mapper: the .exe can't be replaced now
            log.info("Update to %s waits: %s other DizzyDoctor process(es) running on this PC", upd["version"], busy)
            upd = None
    if upd:
        try:
            source = upd.get("source", "the clinic server")
            script = _with_window(f"Updating DizzyDoctor to version {upd['version']} from {source}…",
                                  lambda progress: shared.stage_update(upd, progress))
            shared.run_updater(script)
            log.info("Updating %s -> %s", VERSION, upd["version"])
            return                                 # the updater swaps the .exe once this one has closed, then starts it
        except Exception:
            log.exception("Update failed - starting %s", VERSION)
    note = ""
    if shared.is_client(cfg):
        try:
            note = _with_window("Loading the clinic's data from the server…",
                                lambda progress: shared.pull(cfg, config.DB_PATH, progress))
        except Exception as ex:
            log.exception("Loading the published clinic data failed")
            note = f"Couldn't load the clinic data from the server ({ex}) - showing what this PC had."
    app = App(note)
    tab = sys.argv[sys.argv.index("--tab") + 1] if "--tab" in sys.argv[:-1] else ""
    if tab:                                        # opened from the main window's top bar on this tab
        app.after(400, lambda: app.show_tab(tab))
    app.mainloop()
