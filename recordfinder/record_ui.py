"""Patient record window: every visit (Outlook + progress notes) and every document for one patient.

Visits come from the local Outlook cache (all calendars) and the progress-note files; documents
come from the drive index. Selecting a visit attaches its superbill page and progress note
automatically (found in the scanned batch, extracted as its own PDF).
"""
from __future__ import annotations

import os
import queue
import re
import threading
import tkinter as tk
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from tkinter import messagebox, ttk

from . import attach, auths, balances, eligibility, notes, outlook_cache, people, rules
from .matcher import Hit, rank, search_terms
from .superbills import _fold
from .superbill_ui import _png_photo, render_png


@dataclass
class Visit:
    day: date
    bookings: list = field(default_factory=list)     # outlook_cache.Booking
    notes: list = field(default_factory=list)        # notes.Note

    @property
    def seen(self) -> bool:
        return any(b.seen for b in self.bookings) or any(n.kind == "final" for n in self.notes)

    def where(self) -> str:
        return ", ".join(dict.fromkeys(b.cal for b in self.bookings)) or "-"

    def tags(self) -> str:
        return ", ".join(dict.fromkeys(c for b in self.bookings for c in b.categories))


def build_visits(bookings, note_files) -> list[Visit]:
    by_day: dict[date, Visit] = {}
    for b in bookings:
        by_day.setdefault(b.start.date(), Visit(b.start.date())).bookings.append(b)
    for n in note_files:
        if n.dos:
            by_day.setdefault(n.dos, Visit(n.dos)).notes.append(n)
    return sorted(by_day.values(), key=lambda v: v.day, reverse=True)


HEADER_BG, INK, SLATE = "#eef3f3", "#0b1f2c", "#46606f"          # DizzyDoctor mist / ink / slate
CHIP = {"red": ("#fde8e4", "#9a3412"), "amber": ("#fff4dc", "#92600a"), "green": ("#e3f4ec", "#036c5f"),
        "blue": ("#e3f1fb", "#00537f"), "grey": ("#e9edef", "#46606f")}
# Document groups, in the order they appear in the chart; each file's kind (from its folder) picks its group.
GROUPS = [("Visit documents", ()), ("Progress notes", ("Progress note",)), ("Prepped notes", ("Prepped note",)),
          ("Vestibular testing", ("VNG", "VEMP", "Chair report")), ("Neuro testing", ("EMG/NCV", "EEG")),
          ("Referrals & authorizations", ("Referral", "Outgoing referral", "Auth", "Auth letter")),
          ("Faxes", ("Fax",)), ("Records", ("Records", "Records request")), ("Billing", ("Billing (MPS)", "Superbill")),
          ("Other files", ())]
_PHONE = re.compile(r"\bPh(?:one)?\.?\s*[:#]?\s*(\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4})", re.I)
_ID = re.compile(r"\bID\s*[:#]\s*([A-Za-z0-9\-]{4,})")
_EMAIL = re.compile(r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+")
_FAX = re.compile(r"\bFax\s*[:#]?\s*(\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4})", re.I)


def group_of(kind: str) -> str:
    base = kind.split(" (same name)")[0]
    return next((g for g, kinds in GROUPS if base in kinds), "Other files")


def header_facts(bookings) -> dict:
    """Phone, member IDs, e-mail, referral fax, plans, complaint and referral from the newest Outlook header that has
    them (each fact from the newest booking that carries it)."""
    facts: dict = {}
    for b in sorted(bookings, key=lambda b: b.start, reverse=True):
        s = " ".join((b.subject or "").split())
        if "DOB" not in s.upper():
            continue
        info = outlook_cache.parse_subject(s)
        ids = _ID.findall(s)
        for key, val in (("phone", (_PHONE.search(s) or [None, ""])[1]), ("email", (_EMAIL.search(s) or [""])[0]),
                         ("fax", (_FAX.search(s) or [None, ""])[1]), ("insurance", info.get("insurance")),
                         ("secondary", info.get("secondary")), ("complaint", info.get("complaint")),
                         ("referral", info.get("referral")), ("account", info.get("account")),
                         ("id1", ids[0] if ids else ""), ("id2", ids[1] if len(ids) > 1 else "")):
            if val and not facts.get(key):
                facts[key] = val.strip()
    return facts


class PatientRecord(tk.Toplevel):
    VISIT_COLS = [("date", "Date", 90), ("where", "Calendar", 150), ("tags", "Type / tags", 170), ("seen", "Seen", 70),
                  ("note", "Note", 60), ("sb", "Superbill", 80)]
    DOC_COLS = [("name", "File", 330), ("date", "Date", 85), ("folder", "Folder", 360)]
    AUTH_COLS = [("status", "Status", 90), ("number", "Auth #", 110), ("from", "From", 85), ("to", "To", 85),
                 ("codes", "Codes (units)", 330), ("where", "Written on", 190)]
    ITEM_COLS = [("svc", "Date of service", 100), ("voucher", "Voucher", 90), ("provider", "Provider", 95),
                 ("chg", "Charge", 80), ("pmts", "Payments", 80), ("balance", "Balance", 80), ("payor", "Payer", 150),
                 ("billed", "Billed", 90), ("age", "Age (days)", 75)]

    def __init__(self, app, first: str = "", last: str = "", dob: date | None = None, account: str = "",
                 patient_id: int | None = None, tab: str = ""):
        super().__init__(app)
        self.app, self.cfg, self.store = app, app.cfg, app.store
        self.first, self.last, self.dob, self.account = first, last, dob, account
        self.patient_id = patient_id
        self.linked, self.next_visit = [], None
        self.sheets: dict = {}                   # visit day -> superbill_map.Sheet
        self.vd_open = (None, [])                # (OpenBalance, open vouchers) from the Veradigm reports
        self.jobs: queue.Queue = queue.Queue()
        self.cancel = threading.Event()
        self.visits: list[Visit] = []
        self.docs: dict[str, tuple] = {}         # iid -> (kind, path)
        self.attached: dict[date, attach.Attached] = {}
        self.photo = None
        self.title(f"Patient chart - {last}, {first}".strip(" ,-"))
        self.geometry("1400x860")
        self.protocol("WM_DELETE_WINDOW", self._close)
        self._build()
        if tab in ("auth", "balance"):
            self.tabs.select(1 if tab == "auth" else 2)
        self.after(100, self._poll)
        threading.Thread(target=self._load, daemon=True).start()

    # ---------------------------------------------------------------- layout
    def _build(self):
        card = tk.Frame(self, background=HEADER_BG, padx=14, pady=10)
        card.pack(fill="x")
        top = tk.Frame(card, background=HEADER_BG)
        top.pack(fill="x")
        self.header = tk.Label(top, text="Loading…", font=("Segoe UI", 15, "bold"), background=HEADER_BG, foreground=INK)
        self.header.pack(side="left")
        ttk.Button(top, text="Refresh Outlook", command=self._refresh_outlook).pack(side="right")
        self.ident = tk.Label(card, text="", font=("Segoe UI", 10), background=HEADER_BG, foreground=INK, anchor="w", justify="left")
        self.ident.pack(fill="x", pady=(2, 0))
        self.coverage = tk.Label(card, text="", font=("Segoe UI", 10), background=HEADER_BG, foreground=SLATE, anchor="w", justify="left")
        self.coverage.pack(fill="x")
        self.care = tk.Label(card, text="", font=("Segoe UI", 10), background=HEADER_BG, foreground=SLATE, anchor="w", justify="left")
        self.care.pack(fill="x")
        self.chips = tk.Frame(card, background=HEADER_BG)
        self.chips.pack(fill="x", pady=(6, 0))
        self.sub = ttk.Label(self, foreground="gray", padding=(10, 2))
        self.sub.pack(fill="x")

        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill="both", expand=True, padx=8, pady=(0, 6))
        chart = ttk.Frame(self.tabs)
        self.tabs.add(chart, text="  Chart  ")
        self.auth_tab = ttk.Frame(self.tabs, padding=8)
        self.tabs.add(self.auth_tab, text="  Authorizations  ")
        self.bal_tab = ttk.Frame(self.tabs, padding=8)
        self.tabs.add(self.bal_tab, text="  Balance  ")
        self._build_auth_tab()
        self._build_balance_tab()

        main = ttk.PanedWindow(chart, orient="horizontal")
        main.pack(fill="both", expand=True, pady=6)
        left = ttk.PanedWindow(main, orient="vertical")
        main.add(left, weight=3)

        vf = ttk.Frame(left)
        self.vtree = self.app._make_tree(vf, self.VISIT_COLS)
        self.vtree.bind("<<TreeviewSelect>>", lambda e: self._on_visit())
        self.vtree.tag_configure("noshow", foreground="#9ca3af")
        left.add(vf, weight=2)

        df = ttk.Frame(left)
        self.dtree = self.app._make_tree(df, self.DOC_COLS)
        self.dtree.configure(selectmode="extended", show="tree headings")
        self.dtree.heading("#0", text="Documents")
        self.dtree.column("#0", width=210, stretch=False)
        self.dtree.tag_configure("group", font=("Segoe UI", 9, "bold"))
        self.dtree.bind("<<TreeviewSelect>>", lambda e: self._on_doc())
        self.dtree.bind("<Double-1>", lambda e: self._open())
        self.dtree.tag_configure("attached", foreground="#047857")
        bar = ttk.Frame(df)
        bar.pack(fill="x", pady=(4, 0))
        ttk.Button(bar, text="Open", command=self._open).pack(side="left")
        ttk.Button(bar, text="Show in folder", command=lambda: self._open(reveal=True)).pack(side="left", padx=4)
        ttk.Button(bar, text="Copy to Missing Folder", command=self._copy_missing).pack(side="left")
        ttk.Button(bar, text="Fix superbill…", command=self._fix_superbill).pack(side="left", padx=4)
        ttk.Button(bar, text="New note", command=self._new_note).pack(side="left")
        ttk.Button(bar, text="Edit note", command=self._edit_note).pack(side="left", padx=4)
        self.status = ttk.Label(bar, foreground="gray")
        self.status.pack(side="right")
        left.add(df, weight=3)

        right = ttk.Frame(main)
        self.canvas = tk.Label(right, text="Select a visit or a document", background="#f3f4f6", anchor="center")
        self.canvas.pack(fill="both", expand=True)
        self.info = tk.Text(right, height=9, wrap="word", relief="solid", borderwidth=1, font=("Segoe UI", 9))
        self.info.pack(fill="x", pady=(6, 0))
        main.add(right, weight=2)

    # ---------------------------------------------------------------- charting
    def _new_note(self):
        from types import SimpleNamespace
        self.app.open_note(SimpleNamespace(last=self.last, first=self.first, dob=self.dob, acct=self.account, id=self.patient_id))

    def _edit_note(self):
        """Open the selected document in the chart screen when it is a progress note."""
        sel = self.dtree.selection()
        path = self.docs.get(sel[0], ("", ""))[1] if sel else ""
        if not path or not path.lower().endswith(".pdf"):
            messagebox.showinfo("Note", "Select a progress note in the document list first.", parent=self)
            return
        self.app.open_note(note_path=path)

    def _build_auth_tab(self):
        f = self.auth_tab
        self.auth_head = ttk.Label(f, text="Reading the appointment notes…", font=("Segoe UI", 10, "bold"))
        self.auth_head.pack(anchor="w")
        box = ttk.Frame(f)
        box.pack(fill="both", expand=True, pady=(6, 0))
        self.atree = self.app._make_tree(box, self.AUTH_COLS)
        for tag, (bg, fg) in (("Active", CHIP["green"]), ("Expired", CHIP["grey"]), ("Upcoming", CHIP["blue"])):
            self.atree.tag_configure(tag, foreground=fg)
        ttk.Label(f, text="Auth notes (denied, pending, needed)", font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(8, 0))
        self.anotes = tk.Text(f, height=5, wrap="word", relief="solid", borderwidth=1, font=("Segoe UI", 9))
        self.anotes.pack(fill="x")
        ttk.Label(f, text="Authorization letters and referrals on the drive (double-click to open)",
                  font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(8, 0))
        lb = ttk.Frame(f)
        lb.pack(fill="both", expand=True)
        self.aletters = self.app._make_tree(lb, [("kind", "What", 130), ("name", "File", 420), ("date", "Date", 90)])
        self.aletters.bind("<Double-1>", lambda e: self._open_letter())
        self.letter_paths: dict[str, str] = {}

    def _build_balance_tab(self):
        f = self.bal_tab
        self.bal_head = ttk.Label(f, text="", font=("Segoe UI", 11, "bold"))
        self.bal_head.pack(anchor="w")
        self.bal_sub = ttk.Label(f, text="", foreground="gray")
        self.bal_sub.pack(anchor="w")
        ag = ttk.Frame(f)
        ag.pack(fill="x", pady=(8, 0))
        self.aging = self.app._make_tree(ag, [("who", "", 110)] + [(k, k, 95) for k in ("0-30", "31-60", "61-90", "Over 90", "Total")])
        self.aging.configure(height=3)
        ttk.Label(f, text="Open items (Veradigm Financial Inquiry)", font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(10, 0))
        it = ttk.Frame(f)
        it.pack(fill="both", expand=True)
        self.items_tree = self.app._make_tree(it, self.ITEM_COLS)
        self.bal_hist = ttk.Label(f, text="", foreground="gray")
        self.bal_hist.pack(anchor="w", pady=(6, 0))

    def _chip(self, text: str, tone: str):
        bg, fg = CHIP[tone]
        tk.Label(self.chips, text=text, background=bg, foreground=fg, font=("Segoe UI", 9, "bold"),
                 padx=8, pady=2).pack(side="left", padx=(0, 6))

    # ---------------------------------------------------------------- loading
    def _load(self):
        try:
            bookings = outlook_cache.find_patient(self.store, self.last, self.first, self.dob, self.account)
            note_files = notes.find_notes(self.store, self.cfg, self.last, self.first) if self.last else []
            summary = outlook_cache.summary(bookings)
            # Files come from the patient map (every naming style, same-name patients kept apart). Only a patient
            # who isn't in the map yet falls back to the old name search, which also lists other patients'
            # files that share the last name.
            pid, same = self.patient_id, []
            if pid is None:
                found = people.lookup(self.store, self.last, self.first, self.dob, self.account)
                if len(found) == 1:
                    pid = found[0].id
                elif len(found) > 1:
                    same = found
            linked = people.files_for(self.store, pid) if pid else []
            hits = []
            if not pid:
                terms = search_terms(self.first, self.last, self.account)
                rows = self.store.find_files(terms) if terms else []
                hits = rank(rows, self.first, self.last, self.account, [], self.dob, [])
            facts = header_facts(bookings)
            acct = self.account or facts.get("account") or ""
            try:
                balances.import_folder(self.store, self.cfg.get("veradigm_results_folder") or balances.DEFAULT_FOLDER)
            except Exception:
                pass  # the balance runs' folder isn't reachable: show what's already imported
            bal = balances.latest(self.store, acct) if acct else None
            dob_iso = self.dob.isoformat() if self.dob else (max(summary["dobs"], key=summary["dobs"].get) if summary["dobs"] else "")
            try:
                from . import superbill_map
                self.sheets = {}
                for sh in superbill_map.for_patient(self.store, _fold(self.last.replace(" ", "")), dob_iso):
                    self.sheets.setdefault(sh.day, sh)   # the mapped superbill page for each visit day
            except Exception:
                self.sheets = {}
            try:
                from . import veradigm_reports
                self.vd_open = veradigm_reports.open_for(self.store, acct) if acct else (None, [])
            except Exception:
                self.vd_open = (None, [])
            self.jobs.put(("loaded", bookings, note_files, summary, hits, linked, same, facts, bal))
        except Exception as ex:
            self.jobs.put(("error", f"Couldn't load the record: {ex}"))
            return
        # Authorizations need each appointment's notes from Outlook: loaded second, so the chart shows first.
        try:
            dob = self.dob.isoformat() if self.dob else (max(summary["dobs"], key=summary["dobs"].get) if summary["dobs"] else "")
            found, auth_notes = auths.for_patient(self.store, _fold(self.last.replace(" ", "")), _fold(self.first), dob, acct,
                                                  progress=lambda m: self.jobs.put(("status", m)))
            self.jobs.put(("auths", found, auth_notes, bookings, facts))
        except Exception as ex:
            self.jobs.put(("auths_error", str(ex)))

    def _poll(self):
        try:
            while True:
                job = self.jobs.get_nowait()
                getattr(self, "_job_" + job[0])(*job[1:])
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(120, self._poll)

    def _job_error(self, msg):
        self.status.configure(text=msg)

    def _job_status(self, msg):
        self.status.configure(text=msg)

    def _job_loaded(self, bookings, note_files, summary, hits, linked=(), same=(), facts=None, bal=None):
        facts = facts or {}
        self.visits = build_visits(bookings, note_files)
        self.linked = linked
        dobs = summary["dobs"]
        dob = max(dobs, key=dobs.get) if dobs else (self.dob.isoformat() if self.dob else "")
        if dob and not self.dob:
            self.dob = date.fromisoformat(dob)
        self.account = self.account or facts.get("account", "")
        self.header.configure(text=f"{self.last}, {self.first}".strip(", "))
        ident = []
        if dob:
            d = date.fromisoformat(dob)
            t = date.today()
            ident.append(f"DOB {d:%m/%d/%Y} ({t.year - d.year - ((t.month, t.day) < (d.month, d.day))} y)")
        if self.account:
            ident.append(f"Pt # {self.account}")
        if facts.get("phone"):
            ident.append(f"Ph {facts['phone']}")
        if facts.get("email"):
            ident.append(facts["email"])
        self.ident.configure(text="   ·   ".join(ident))
        cov = []
        if facts.get("insurance") or summary["insurance"]:
            cov.append(f"Primary: {facts.get('insurance') or summary['insurance']}" + (f"  ID {facts['id1']}" if facts.get("id1") else ""))
        if facts.get("secondary"):
            cov.append(f"Secondary: {facts['secondary']}" + (f"  ID {facts['id2']}" if facts.get("id2") else ""))
        if facts.get("referral"):
            cov.append(f"Ref: {facts['referral']}" + (f"  Fax {facts['fax']}" if facts.get("fax") else ""))
        self.coverage.configure(text="   ·   ".join(cov) or "No insurance in the Outlook headers")
        now = datetime.now()
        upcoming = sorted((b for b in bookings if b.start >= now and b.seen), key=lambda b: b.start)
        seen = [b for b in bookings if b.start < now and b.seen]
        cals = [c for c, _n in Counter(b.cal for b in seen).most_common(3)]
        care = []
        if facts.get("complaint"):
            care.append(f"Cc: {facts['complaint']}")
        if cals:
            care.append("Seen on: " + ", ".join(cals))
        if seen:
            care.append(f"Last visit {max(b.start for b in seen):%m/%d/%Y}")
        if upcoming:
            care.append(f"Next: {upcoming[0].start:%m/%d/%Y %I:%M %p} ({upcoming[0].cal})")
        self.care.configure(text="   ·   ".join(care))
        self.next_visit = upcoming[0] if upcoming else None

        for w in self.chips.winfo_children():
            w.destroy()
        self._show_balance(bal)
        if dob:
            chk = eligibility.latest(self.store, _fold(self.last.replace(" ", "")), dob)
            if chk and chk.fresh:
                self._chip(f"Eligibility {chk.label()}" + (f" - {chk.detail}" if chk.detail else ""),
                           "green" if chk.status == "Active" else "red")
        if len(dobs) > 1:
            self._chip(f"{len(dobs)} DOBs in Outlook for this name", "amber")
        if same:
            self._chip(f"{len(same)} patients named {self.first} {self.last} - add the DOB", "amber")
        if self.next_visit and any("abn" in c.lower() for c in self.next_visit.categories):
            self._chip("ABN form on next visit", "blue")
        n_notes = sum(1 for n in note_files if n.kind == "final")
        self.sub.configure(text=f"{summary['bookings']} bookings ({summary['seen']} seen) · {n_notes} progress notes · "
                                f"{len(linked)} documents on the drive")
        self.vtree.delete(*self.vtree.get_children())
        for i, v in enumerate(self.visits):
            has_note = "final" if any(n.kind == "final" for n in v.notes) else ("draft" if v.notes else "")
            seen = "Upcoming" if v.day > date.today() else ("Seen" if v.seen else "No-show / cxl")
            sh = self.sheets.get(v.day)
            self.vtree.insert("", "end", iid=str(i), values=(v.day.strftime("%m/%d/%Y"), v.where(), v.tags(), seen, has_note,
                                                               (f"p{sh.page}" + ("" if sh.sure else " ?")) if sh else ""),
                              tags=() if v.seen else ("noshow",))
        self.dtree.delete(*self.dtree.get_children())
        self.docs = {}
        for n in note_files:
            self._add_doc(("Progress note" if n.kind == "final" else "Prepped note"), n.path, n.dos)
        shown = {os.path.normcase(p) for _k, p in self.docs.values()}
        for path, _name, _folder, _size, mtime, how in linked:
            if os.path.normcase(path) not in shown:
                shown.add(os.path.normcase(path))
                kind = people.kind_of(path) + (" (same name)" if "same name" in how else "")
                self._add_doc(kind, path, datetime.fromtimestamp(mtime).date())
        for h in hits:
            if os.path.normcase(h.path) not in shown:
                self._add_doc("File", h.path, datetime.fromtimestamp(h.mtime).date())
        self.status.configure(text=f"{len(self.visits)} visits · {len(self.docs)} documents")
        past = [i for i, v in enumerate(self.visits) if v.day <= date.today() and v.seen]
        if past:
            self.vtree.selection_set(str(past[0]))

    OPEN_GROUPS = {"Visit documents", "Progress notes", "Vestibular testing", "Neuro testing", "Referrals & authorizations"}

    def _add_doc(self, kind, path, when, first=False, attached=False, group=None):
        g = group or group_of(kind)
        gid = "g:" + g
        if not self.dtree.exists(gid):
            order = [x for x, _k in GROUPS]
            at = sum(1 for x in order[:order.index(g)] if self.dtree.exists("g:" + x))
            self.dtree.insert("", at, iid=gid, text=g, open=g in self.OPEN_GROUPS, tags=("group",), values=("", "", ""))
        iid = f"d{len(self.docs)}"
        self.docs[iid] = (kind, path)
        self.dtree.insert(gid, 0 if first else "end", iid=iid, text=kind, tags=("attached",) if attached else (),
                          values=(os.path.basename(path), when.strftime("%m/%d/%Y") if when else "", os.path.dirname(path)))
        self.dtree.item(gid, text=f"{g} ({len(self.dtree.get_children(gid))})")
        return iid

    # ---------------------------------------------------------------- balance / authorizations
    def _show_balance(self, bal):
        self.aging.delete(*self.aging.get_children())
        self.items_tree.delete(*self.items_tree.get_children())
        self.bal_hist.configure(text="")
        ob, open_vouchers = self.vd_open
        report_line = ""
        if ob is not None:
            report_line = (f"Veradigm Revenue Detail report of {ob.as_of:%m/%d/%Y}: ${ob.total:,.2f} open on {ob.vouchers} "
                           f"visit(s), insurance and patient together" if ob.as_of else "Veradigm report: nothing open")
        if bal is None:
            if ob is not None and ob.vouchers:
                tone = "red" if ob.aging[3] > 0.004 else "amber"
                self._chip(f"Open ${ob.total:,.2f} on {ob.vouchers} visit(s) (Veradigm, all payers)",
                           "green" if ob.total < 0 else tone)
                self.bal_head.configure(text=f"Open ${ob.total:,.2f} (insurance and patient together)")
                self.bal_sub.configure(text=report_line + ". The patient's own share shows once a Financial Inquiry "
                                                          "check has been run for them.")
                self.aging.insert("", "end", values=["All payers"] + [f"${x:,.2f}" for x in ob.aging] + [f"${ob.total:,.2f}"])
                for v in open_vouchers:
                    self.items_tree.insert("", "end", values=(f"{v.dos:%m/%d/%Y}", v.voucher, "", f"${v.charges:,.2f}",
                                                              f"${v.payments:,.2f}", f"${v.net:,.2f}", "(Revenue Detail)", "",
                                                              (ob.as_of - v.dos).days))
                return
            self._chip("No Veradigm balance on file", "grey")
            self.bal_head.configure(text="No Veradigm balance on file for this patient yet." if ob is None
                                    else "Nothing open in the Veradigm report.")
            self.bal_sub.configure(text=(report_line + ". " if report_line else "")
                                   + "Balances come in from the Veradigm reports and the open-balance runs."
                                   + ("" if self.account else " There's no Pt # in this patient's Outlook headers."))
            return
        if bal.self > 0:
            self._chip(f"Self balance ${bal.self:,.2f}", "red" if bal.self_over_90 > 0 else "amber")
        elif bal.credit:
            self._chip(f"Credit ${-bal.self:,.2f}", "green")
        else:
            self._chip("No self balance", "green")
        if bal.self_over_90 > 0:
            self._chip(f"Self over 90 days ${bal.self_over_90:,.2f} - collect in full", "red")
        if bal.insur_over_90 > 0:
            self._chip(f"Insurance over 90 days ${bal.insur_over_90:,.2f}", "amber")
        self.bal_head.configure(text=f"Self ${bal.self:,.2f}     Insurance ${bal.insur:,.2f}     Total ${bal.total:,.2f}")
        age = (date.today() - bal.as_of).days
        self.bal_sub.configure(text=f"From Veradigm on {bal.as_of:%m/%d/%Y}" + (" (today)" if not age else f" ({age} days ago)")
                                    + (f"   ·   Unassigned ${bal.unassigned:,.2f} is not a credit" if bal.unassigned else ""))
        for who, ag in (("Self", bal.self_aging), ("Insurance", bal.insur_aging)):
            self.aging.insert("", "end", values=[who] + [f"${x:,.2f}" for x in (list(ag) + [0.0] * 5)[:5]])
        for it in bal.items:
            self.items_tree.insert("", "end", values=[it.get(k, "") for k, _t, _w in self.ITEM_COLS])
        hist = balances.history(self.store, bal.acct)
        lines = [report_line] if report_line else []
        if len(hist) > 1:
            lines.append("Earlier checks: " + "   ".join(f"{h.as_of:%m/%d/%y} self ${h.self:,.2f}" for h in hist[1:8]))
        self.bal_hist.configure(text="\n".join(lines))

    def _job_auths(self, found, auth_notes, bookings, facts):
        self.atree.delete(*self.atree.get_children())
        today = date.today()
        for a in found:
            st = a.status(today)
            self.atree.insert("", "end", tags=(st,), values=(st, a.number, f"{a.start:%m/%d/%Y}", f"{a.end:%m/%d/%Y}",
                                                             ", ".join(a.codes) or "-", a.source))
        active = [a for a in found if a.status(today) == "Active"]
        self.auth_head.configure(text=(f"{len(found)} authorization(s) on file · {len(active)} active" if found
                                       else "No authorization written in this patient's appointment notes."))
        self.anotes.configure(state="normal")
        self.anotes.delete("1.0", "end")
        self.anotes.insert("end", "\n".join(f"{w}:  {n}" for w, n in auth_notes[-15:]) or "-")
        self.anotes.configure(state="disabled")
        self.aletters.delete(*self.aletters.get_children())
        self.letter_paths = {}
        for path, _name, _folder, _size, mtime, _how in self.linked:
            kind = people.kind_of(path)
            if kind in ("Auth", "Auth letter", "Outgoing referral", "Referral"):
                iid = f"l{len(self.letter_paths)}"
                self.letter_paths[iid] = path
                self.aletters.insert("", "end", iid=iid, values=(kind, os.path.basename(path),
                                                                 datetime.fromtimestamp(mtime).strftime("%m/%d/%Y")))
        nv = self.next_visit
        if nv and auths.needs_auth(nv.insurance or facts.get("insurance", ""), nv.secondary or facts.get("secondary", "")):
            cover = [a for a in found if a.covers(nv.start.date())]
            if cover:
                self._chip(f"Auth #{cover[0].number} covers next visit (through {cover[0].end:%m/%d/%y})", "green")
            else:
                self._chip(f"No auth on file for next visit {nv.start:%m/%d/%y}", "red")
        elif active:
            self._chip(f"Auth active through {max(a.end for a in active):%m/%d/%y}", "green")
        if any(re.search(r"need \w+ auth", n, re.I) for _w, n in auth_notes):
            self._chip("Note: auth needed (see Authorizations)", "amber")
        self.tabs.tab(1, text=f"  Authorizations ({len(active)} active)  " if found else "  Authorizations  ")
        self.status.configure(text=f"{len(self.visits)} visits · {len(self.docs)} documents · {len(found)} authorizations")

    def _job_auths_error(self, msg):
        self.auth_head.configure(text=f"Couldn't read the appointment notes from Outlook: {msg}")

    def _open_letter(self):
        from .app import open_path
        for iid in self.aletters.selection():
            try:
                open_path(self.letter_paths[iid])
            except (OSError, KeyError) as ex:
                messagebox.showerror("Can't open", str(ex), parent=self)

    # ---------------------------------------------------------------- visits
    def _on_visit(self):
        sel = self.vtree.selection()
        if not sel:
            return
        v = self.visits[int(sel[0])]
        self._show_visit_info(v)
        if v.day in self.attached or not v.seen or v.day > date.today():
            return
        self.vtree.set(sel[0], "sb", "looking…")
        dob = self.dob
        sh = self.sheets.get(v.day)
        if sh:   # already placed by the superbill map: just cut the page out, no OCR needed
            threading.Thread(target=self._attach_mapped, args=(sel[0], v, sh), daemon=True).start()
            return
        threading.Thread(target=self._attach, args=(sel[0], v, dob), daemon=True).start()

    def _attach_mapped(self, iid, v: Visit, sh):
        try:
            name = f"{self.last} {self.first} {sh.code} {v.day:%m.%d.%y} SB.pdf".strip()
            path = attach.extract_page(sh.batch, sh.page - 1, os.path.join(attach.attachments_dir(), name))
            how = "printed name/DOB" if sh.sure else f"schedule order ({sh.how}) - check the name"
            res = attach.Attached(path=path, batch=sh.batch, page=sh.page, score=90 if sh.sure else 45,
                                  reasons=[how], code=sh.code,
                                  notes=[f"Superbill map: {os.path.basename(sh.batch)} p{sh.page} ({how})"])
            self.jobs.put(("attached", iid, v, res))
        except Exception as ex:
            self.jobs.put(("status", f"Couldn't cut the superbill page: {ex}"))

    def _attach(self, iid, v: Visit, dob):
        try:
            res = attach.find_superbill(self.store, self.cfg, self.last, self.first, v.day, dob, self.account,
                                        cancel=self.cancel, progress=lambda m: self.jobs.put(("status", m)))
            self.jobs.put(("attached", iid, v, res))
        except Exception as ex:
            self.jobs.put(("status", f"Superbill lookup failed: {ex}"))

    def _job_attached(self, iid, v: Visit, res: attach.Attached):
        self.attached[v.day] = res
        if res.path:
            label = f"p{res.page}" + ("" if res.confident else " ?")
            self.vtree.set(iid, "sb", label)
            self.dtree.delete(*self.dtree.get_children("g:Visit documents")) if self.dtree.exists("g:Visit documents") else None
            doc = self._add_doc(f"Superbill {v.day:%m/%d/%y}" + ("" if res.confident else " - check page"), res.path, v.day,
                                attached=True, group="Visit documents")
            note = notes.note_for_visit(v.notes, v.day)
            if note:
                self._add_doc(f"Progress note {v.day:%m/%d/%y}", note.path, v.day, attached=True, group="Visit documents")
            self.dtree.see(doc)
            self.dtree.selection_set(doc)
        else:
            self.vtree.set(iid, "sb", "not found")
        self.status.configure(text=" · ".join(res.notes[-2:]))
        self._show_visit_info(v)

    def _show_visit_info(self, v: Visit):
        lines = [f"{v.day:%A %m/%d/%Y}"]
        for b in v.bookings:
            lines.append(f"• {b.cal} {b.start:%I:%M %p}  {', '.join(b.categories)}" + ("" if b.seen else "  (no-show / cancelled)"))
            if b.insurance:
                lines.append(f"   Ins: {b.insurance}" + (f" / {b.secondary}" if b.secondary else ""))
        note = notes.note_for_visit(v.notes, v.day)
        if note:
            nf = notes.fields(self.store, note.path)
            if nf:
                att = nf.attending(rules.roster_from(self.cfg))
                lines.append(f"• Note ({note.kind}): {', '.join(nf.visit) or '-'} · {nf.minutes or '?'} min · attending {att or '-'}"
                             + (" · G2211 statement" if nf.longitudinal else ""))
                if nf.date and nf.date != v.day.strftime("%m/%d/%Y"):
                    lines.append(f"   Check: the note's Date field says {nf.date}")
                lines += [f"   {x}" for x in nf.impressions[:6]]
        res = self.attached.get(v.day)
        if res:
            lines += [f"• {n}" for n in res.notes]
        self.info.configure(state="normal")
        self.info.delete("1.0", "end")
        self.info.insert("end", "\n".join(lines))
        self.info.configure(state="disabled")

    # ---------------------------------------------------------------- documents
    def _selected_docs(self):
        return [self.docs[i] for i in self.dtree.selection() if i in self.docs]

    def _on_doc(self):
        docs = self._selected_docs()
        if not docs:
            return
        path = docs[0][1]
        if not path.lower().endswith(".pdf"):
            self.canvas.configure(image="", text=os.path.basename(path))
            return
        try:
            self.photo = _png_photo(render_png(path, 0, height=640))
            self.canvas.configure(image=self.photo, text="")
        except Exception as ex:
            self.canvas.configure(image="", text=f"Can't preview: {ex}")

    def _open(self, reveal=False):
        from .app import open_path
        for _kind, path in self._selected_docs()[:5]:
            try:
                open_path(path, reveal)
            except OSError as ex:
                messagebox.showerror("Can't open", str(ex), parent=self)

    def _copy_missing(self):
        paths = [p for _k, p in self._selected_docs()]
        if paths:
            self.app.copy_paths_to_missing(paths, self.first, self.last, self.account, parent=self)

    def _fix_superbill(self):
        sel = self.vtree.selection()
        if not sel:
            messagebox.showinfo("Superbill", "Select a visit first.", parent=self)
            return
        v = self.visits[int(sel[0])]
        item = {"key": None, "first": self.first, "last": self.last, "account": self.account, "name": f"{self.first} {self.last}",
                "dob": self.dob.isoformat() if self.dob else "", "dos": [v.day.isoformat()], "dos_display": v.day.strftime("%m/%d/%Y"),
                "visit_code": (self.attached.get(v.day).code if self.attached.get(v.day) else ""), "kind": "Superbill"}
        from .superbill_ui import SuperbillDialog
        SuperbillDialog(self.app, item)

    def _refresh_outlook(self):
        self.app.start_outlook_sync(force=False, then=lambda: threading.Thread(target=self._load, daemon=True).start())
        self.status.configure(text="Refreshing Outlook…")

    def _close(self):
        self.cancel.set()
        self.destroy()
