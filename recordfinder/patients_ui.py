"""All patients: the directory built from Outlook's full history, searchable by name, DOB or account #.

Double-click a patient to open their complete record (every visit and every file on the drive that names them).
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from . import balances, people


class PatientsWindow(tk.Toplevel):
    COLS = [("name", "Patient", 260), ("dob", "DOB", 90), ("acct", "Pt #", 80), ("first", "First visit", 90),
            ("last", "Last visit", 90), ("visits", "Bookings", 75), ("files", "Files", 60), ("bal", "Balance (Veradigm)", 150)]

    def __init__(self, app):
        super().__init__(app)
        self.app, self.store = app, app.store
        self.rows: dict[str, people.Patient] = {}
        self._after = None
        self.title("All patients")
        self.geometry("980x680")
        top = ttk.Frame(self, padding=(10, 8))
        top.pack(fill="x")
        ttk.Label(top, text="Find:").pack(side="left")
        self.q = tk.StringVar()
        e = ttk.Entry(top, textvariable=self.q, width=40)
        e.pack(side="left", padx=6)
        e.focus_set()
        ttk.Label(top, text="name (Smith, Smith J, John Smith), DOB (01/02/1950) or Pt #", foreground="gray").pack(side="left")
        self.count = ttk.Label(self, foreground="gray", padding=(10, 0))
        self.count.pack(fill="x")
        box = ttk.Frame(self, padding=(8, 4))
        box.pack(fill="both", expand=True)
        self.tree = app._make_tree(box, self.COLS)
        for key, _t, _w in self.COLS:
            self.tree.heading(key, command=lambda k=key: self._sort(k))
        self.tree.tag_configure("nofiles", foreground="#6b7280")
        self.tree.bind("<Double-1>", lambda ev: self._open())
        self.tree.bind("<Return>", lambda ev: self._open())
        bar = ttk.Frame(self, padding=(8, 4))
        bar.pack(fill="x")
        ttk.Button(bar, text="Open record", command=self._open).pack(side="left")
        self.note = ttk.Label(bar, foreground="gray")
        self.note.pack(side="left", padx=10)
        self.q.trace_add("write", lambda *_: self._schedule())
        e.bind("<Return>", lambda ev: self._open_first())
        e.bind("<Down>", lambda ev: self._focus_list())
        self._search()

    def _schedule(self):
        if self._after:
            self.after_cancel(self._after)
        self._after = self.after(180, self._search)

    def _search(self):
        self._after = None
        total = people.count(self.store)
        if not total:
            self.count.configure(text="The patient map isn't built yet. It builds by itself after the drive scan and "
                                      "the Outlook sync (a few minutes the first time).")
            self.note.configure(text="")
            return
        q = self.q.get()
        try:
            found = people.search(self.store, q)
        except Exception as ex:  # the map is being rebuilt this second
            self.count.configure(text=f"Couldn't search: {ex}")
            return
        self.tree.delete(*self.tree.get_children())
        self.rows = {}
        try:
            bals = balances.latest_totals(self.store)
        except Exception:
            bals = {}
        for p in found:
            b = bals.get((p.acct or "").lstrip("0"))
            bal = "" if not b else (f"${b[0]:,.2f} self" if b[0] > 0 else f"credit ${-b[0]:,.2f}" if b[0] < 0 else "$0 self")                 + (f" · as of {b[2][5:7]}/{b[2][8:10]}" if b else "")
            iid = str(p.id)
            self.rows[iid] = p
            self.tree.insert("", "end", iid=iid, tags=() if p.files else ("nofiles",), values=(
                p.name, f"{p.dob:%m/%d/%Y}" if p.dob else "", p.acct,
                f"{p.first_visit:%m/%d/%Y}" if p.first_visit else "", f"{p.last_visit:%m/%d/%Y}" if p.last_visit else "",
                p.bookings, p.files, bal))
        shown = f"{len(found):,}" + ("+" if len(found) >= 400 else "")
        self.count.configure(text=f"{total:,} patients in the directory · showing {shown}"
                                  + ("" if q.strip() else " (most recent visits first)"))
        mapped = self.store.meta_get("patients_mapped_at")
        self.note.configure(text=f"Map built {mapped.replace('T', ' ')}" if mapped else "")

    def _sort(self, key):
        items = [(self.tree.set(i, key), i) for i in self.tree.get_children("")]
        num = key in ("visits", "files")
        if key == "bal":
            num = False
        date_col = key in ("dob", "first", "last")

        def k(t):
            v = t[0]
            if num:
                return int(v or 0)
            if date_col and v:
                m, d, y = v.split("/")
                return f"{y}{m}{d}"
            return v.lower()
        rev = getattr(self, "_rev", {}).get(key, False)
        items.sort(key=k, reverse=not rev if (num or date_col) else rev)
        for n, (_v, i) in enumerate(items):
            self.tree.move(i, "", n)
        self._rev = {key: not rev}

    def _focus_list(self):
        kids = self.tree.get_children()
        if kids:
            self.tree.focus_set()
            self.tree.selection_set(kids[0])
            self.tree.focus(kids[0])

    def _open_first(self):
        kids = self.tree.get_children()
        if len(kids) == 1:
            self.tree.selection_set(kids[0])
            self._open()
        else:
            self._focus_list()

    def _open(self):
        from .record_ui import PatientRecord
        sel = self.tree.selection()
        if not sel:
            return
        p = self.rows[sel[0]]
        PatientRecord(self.app, p.first, p.last, p.dob, p.acct, patient_id=p.id)
