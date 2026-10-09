"""MIPS 2026 tab: each quality measure's 2026 rules, where the office stands, and who still needs something."""
from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from . import mips, people

SLATE = "#46606f"
STATE_TEXT = {"pending": "blank", "check": "check", "not met": "Not Done", "met": "Done", "reason": "reason"}


class MipsTab(ttk.Frame):
    MEASURE_COLS = [("num", "#", 50), ("title", "Measure", 330), ("rows", "Rows", 60), ("met", "Done", 60),
                    ("reason", "With a reason", 95), ("not_met", "Not done", 75), ("blank", "Blank", 60),
                    ("complete", "Completeness", 100), ("rate", "Performance", 95), ("fix", "What to fix", 330)]
    TODO_COLS = [("num", "#", 50), ("patient", "Patient", 190), ("dob", "DOB", 88), ("pa", "PA", 60),
                 ("visit", "Visit in sheet", 95), ("state", "Sheet says", 90), ("need", "Needed", 330),
                 ("next", "Next visit", 150), ("note", "Office note", 260)]

    def __init__(self, master, app):
        super().__init__(master, padding=12)
        self.app, self.store = app, app.store
        self.jobs: queue.Queue = queue.Queue()
        self.statuses: list = []
        self.todos: list = []
        head = ttk.Frame(self)
        head.pack(fill="x")
        ttk.Label(head, text=f"MIPS {mips.YEAR}", font=("Segoe UI", 13, "bold")).pack(side="left")
        ttk.Button(head, text="Refresh", command=self.load).pack(side="left", padx=(16, 6))
        ttk.Button(head, text="Open the workbooks", command=self.open_folder).pack(side="left")
        self.where = ttk.Label(head, foreground=SLATE)
        self.where.pack(side="left", padx=12)
        rules = ttk.Frame(self)
        rules.pack(fill="x", pady=(6, 4))
        for line in mips.PROGRAM:
            ttk.Label(rules, text="•  " + line, foreground=SLATE, wraplength=1250, justify="left").pack(anchor="w")

        body = ttk.PanedWindow(self, orient="vertical")
        body.pack(fill="both", expand=True, pady=(6, 0))
        top = ttk.PanedWindow(body, orient="horizontal")
        left = ttk.Frame(top)
        self.measures = app._make_tree(left, self.MEASURE_COLS)
        self.measures.configure(height=6)
        self.measures.tag_configure("ok", background="#e3f4ec")
        self.measures.tag_configure("warn", background="#fff4dc")
        self.measures.bind("<<TreeviewSelect>>", lambda e: self._show_rules())
        top.add(left, weight=3)
        self.rules = tk.Text(top, height=10, wrap="word", relief="solid", borderwidth=1, font=("Segoe UI", 10))
        self.rules.tag_configure("h", font=("Segoe UI", 10, "bold"))
        self.rules.configure(state="disabled")
        top.add(self.rules, weight=2)
        body.add(top, weight=2)

        low = ttk.Frame(body)
        bar = ttk.Frame(low)
        bar.pack(fill="x", pady=(8, 4))
        ttk.Label(bar, text="Still to do this year", font=("Segoe UI", 11, "bold")).pack(side="left")
        ttk.Label(bar, text="Show:").pack(side="left", padx=(16, 4))
        self.which = tk.StringVar(value="All measures")
        cb = ttk.Combobox(bar, textvariable=self.which, state="readonly", width=24,
                          values=["All measures"] + [f"#{m.number}" for m in mips.MEASURES])
        cb.pack(side="left")
        cb.bind("<<ComboboxSelected>>", lambda e: self._show_todo())
        self.booked = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="Only patients with a visit booked", variable=self.booked,
                        command=self._show_todo).pack(side="left", padx=10)
        ttk.Button(bar, text="Copy list", command=self.copy).pack(side="right")
        self.count = ttk.Label(bar, foreground=SLATE)
        self.count.pack(side="right", padx=10)
        self.todo_tree = app._make_tree(low, self.TODO_COLS)
        self.todo_tree.tag_configure("soon", background="#fff4dc")
        self.todo_tree.bind("<Double-1>", lambda e: self.open_chart())
        ttk.Label(low, text="Blank rows, rows marked \"Done?\", and a \"Not Done\" on a once-a-year measure (a later visit "
                            "this year that documents it still counts). The patient's appointment in Today shows the "
                            "same reminder. Double-click for the chart.", foreground="gray").pack(anchor="w", pady=(4, 0))
        body.add(low, weight=3)
        self.by_iid: dict = {}
        self.after(900, self.load)
        self.after(200, self._poll)

    # ---------------------------------------------------------------- data
    def load(self):
        self.where.configure(text=f"Reading {mips.FOLDER}…")

        def work():
            try:
                st = mips.status()
                td = mips.todo(self.store)
                self.jobs.put(("done", st, td))
            except Exception as ex:
                self.jobs.put(("error", f"Couldn't read the MIPS workbooks in {mips.FOLDER}: {ex}", None))

        threading.Thread(target=work, daemon=True).start()

    def _poll(self):
        try:
            while True:
                kind, a, b = self.jobs.get_nowait()
                if kind == "done":
                    self.statuses, self.todos = a, b
                    self._show()
                else:
                    self.where.configure(text=a)
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(300, self._poll)

    def _show(self):
        self.where.configure(text=mips.FOLDER)
        self.measures.delete(*self.measures.get_children())
        for s in self.statuses:
            probs = s.problems
            self.measures.insert("", "end", iid=s.measure.number, tags=("warn" if probs else "ok",), values=(
                s.measure.number, s.measure.title, s.rows, s.met, s.reason, s.not_met, s.pending + s.check,
                f"{s.completeness:.0%}" if s.completeness is not None else "-",
                f"{s.rate:.0%}" if s.rate is not None else "-",
                "; ".join(probs) if probs else "on track"))
        if self.statuses and not self.measures.selection():
            self.measures.selection_set(self.statuses[0].measure.number)
        self._show_todo()
        if getattr(self.app, "dash", None) is not None:
            self.app.dash.refresh()

    def _show_rules(self):
        sel = self.measures.selection()
        m = mips.BY_NUMBER.get(sel[0]) if sel else None
        self.rules.configure(state="normal")
        self.rules.delete("1.0", "end")
        if m:
            self.rules.insert("end", f"#{m.number} {m.title}\n\n", "h")
            for label, text in (("Who counts", m.denominator), ("Meets the measure", m.met), ("How we fill it in", m.how),
                                ("Choices in the workbook", " / ".join(m.options)), ("At the visit", m.action)):
                self.rules.insert("end", label + ": ", "h")
                self.rules.insert("end", text + "\n\n")
        self.rules.configure(state="disabled")

    def _visible(self) -> list:
        want = self.which.get().lstrip("#")
        return [t for t in self.todos if (want == "All measures" or t.measure.number == want)
                and (t.next_visit is not None or not self.booked.get())]

    def _show_todo(self):
        self.todo_tree.delete(*self.todo_tree.get_children())
        self.by_iid = {}
        rows = self._visible()
        from datetime import datetime, timedelta
        soon = datetime.now() + timedelta(days=14)
        for i, t in enumerate(rows):
            e = t.entry
            iid = str(i)
            self.by_iid[iid] = t
            nxt = f"{t.next_visit:%m/%d/%y %I:%M %p} {t.next_cal}" if t.next_visit else "none booked"
            self.todo_tree.insert("", "end", iid=iid, tags=("soon",) if t.next_visit and t.next_visit <= soon else (),
                                  values=(t.measure.number, f"{e.last}, {e.first}", f"{e.dob:%m/%d/%Y}", e.pa,
                                          f"{e.visit:%m/%d/%Y}" if e.visit else "", e.result or "blank", t.measure.action,
                                          nxt, e.note))
        booked = sum(1 for t in rows if t.next_visit)
        self.count.configure(text=f"{len(rows)} to do · {booked} with a visit booked")

    # ---------------------------------------------------------------- actions
    def open_folder(self):
        try:
            from . import shared
            shared.open_file(mips.FOLDER)
        except OSError as ex:
            messagebox.showerror("MIPS", f"Couldn't open {mips.FOLDER}: {ex}", parent=self)

    def copy(self):
        rows = self._visible()
        lines = ["#\tPatient\tDOB\tPA\tVisit in sheet\tSheet says\tNeeded\tNext visit"]
        for t in rows:
            e = t.entry
            lines.append("\t".join([t.measure.number, f"{e.last}, {e.first}", f"{e.dob:%m/%d/%Y}", e.pa,
                                    f"{e.visit:%m/%d/%Y}" if e.visit else "", e.result or "blank", t.measure.action,
                                    f"{t.next_visit:%m/%d/%Y %I:%M %p} {t.next_cal}" if t.next_visit else ""]))
        self.clipboard_clear()
        self.clipboard_append("\n".join(lines))
        self.count.configure(text=f"{len(rows)} rows copied - paste into Excel")

    def open_chart(self):
        from .record_ui import PatientRecord
        sel = self.todo_tree.selection()
        if not sel or sel[0] not in self.by_iid:
            return
        e = self.by_iid[sel[0]].entry
        found = people.lookup(self.store, e.last, e.first, e.dob)
        p = found[0] if len(found) == 1 else None
        PatientRecord(self.app, e.first.title(), e.last.title(), e.dob, p.acct if p else "", patient_id=p.id if p else None)


def dashboard_summary() -> tuple[str, str]:
    """(value, detail) for the Dashboard card."""
    st = mips.status()
    open_rows = sum(s.pending + s.check for s in st)
    behind = [s.measure.number for s in st if s.problems and any(p.startswith(("completeness", "only")) for p in s.problems)]
    detail = (f"blank or unsure rows in {sum(1 for s in st if s.pending + s.check)} of {len(st)} measures"
              + (f" · below target: #{', #'.join(behind)}" if behind else " · every measure at 75%+ completeness"))
    return f"{open_rows:,}", detail
