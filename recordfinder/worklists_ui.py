"""Today (the clinic day, with what each appointment needs) and Auth worklist (upcoming HMO visits)."""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from datetime import date, datetime, timedelta
from tkinter import messagebox, ttk

from . import worklists

ROW_BG = {"red": "#fde8e4", "amber": "#fff4dc", "green": "", "blue": "", "": ""}


def _parse_day(s: str) -> date | None:
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            pass
    return None


class _Worklist(tk.Toplevel):
    """Shared plumbing: background loading, a result tree, the calendar filter and opening a chart."""
    COLS: list = []
    CHART_TAB = ""

    def __init__(self, app, title: str, geometry: str):
        super().__init__(app)
        self.app, self.store = app, app.store
        self.jobs: queue.Queue = queue.Queue()
        self.rows: list[worklists.Row] = []
        self.by_iid: dict[str, worklists.Row] = {}
        self.loading = False
        self.title(title)
        self.geometry(geometry)
        self.top = ttk.Frame(self, padding=(10, 8))
        self.top.pack(fill="x")
        self.summary = ttk.Label(self, font=("Segoe UI", 10, "bold"), padding=(10, 0))
        self.summary.pack(fill="x")
        box = ttk.Frame(self, padding=(8, 4))
        box.pack(fill="both", expand=True)
        self.tree = app._make_tree(box, self.COLS)
        for tone, bg in ROW_BG.items():
            if bg:
                self.tree.tag_configure(tone, background=bg)
        self.tree.tag_configure("block", foreground="#8a9aa3")
        self.tree.bind("<Double-1>", lambda e: self._open_chart())
        self.tree.bind("<Return>", lambda e: self._open_chart())
        bar = ttk.Frame(self, padding=(8, 4))
        bar.pack(fill="x")
        ttk.Button(bar, text="Open chart", command=self._open_chart).pack(side="left")
        self.bar = bar
        self.status = ttk.Label(bar, foreground="gray")
        self.status.pack(side="right")
        self.after(120, self._poll)

    def _calendar_box(self, around: date):
        ttk.Label(self.top, text="Calendar:").pack(side="left", padx=(14, 4))
        self.cal = tk.StringVar(value="All")
        cb = ttk.Combobox(self.top, textvariable=self.cal, width=22, state="readonly",
                          values=["All"] + worklists.calendars(self.store, around))
        cb.pack(side="left")
        cb.bind("<<ComboboxSelected>>", lambda e: self._show())

    def _calendars(self):
        return None  # loading covers every calendar; the filter only hides rows, so switching is instant

    def _poll(self):
        try:
            while True:
                kind, *args = self.jobs.get_nowait()
                if kind == "status":
                    self.status.configure(text=args[0])
                elif kind == "rows":
                    self.loading = False
                    self.rows = args[0]
                    self._show()
                elif kind == "error":
                    self.loading = False
                    self.status.configure(text=args[0])
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(150, self._poll)

    def _run(self, fn):
        if self.loading:
            return
        self.loading = True
        self.status.configure(text="Loading…")

        def work():
            try:
                self.jobs.put(("rows", fn(lambda m: self.jobs.put(("status", m)))))
            except Exception as ex:  # Outlook busy / closed: show it, keep the window usable
                self.jobs.put(("error", f"Couldn't load: {ex}"))

        threading.Thread(target=work, daemon=True).start()

    def _visible(self, r: worklists.Row) -> bool:
        return self.cal.get() in ("All", r.cal)

    def _open_chart(self):
        from .record_ui import PatientRecord
        sel = self.tree.selection()
        if not sel or sel[0] not in self.by_iid:
            return
        r = self.by_iid[sel[0]]
        if not r.patient:
            return
        PatientRecord(self.app, r.first, r.last, r.dob, r.acct, patient_id=worklists.patient_id(self.store, r),
                      tab=self.CHART_TAB)

    def _copy(self, text: str, what: str):
        self.clipboard_clear()
        self.clipboard_append(text)
        self.status.configure(text=f"{what} copied - paste it into Word, Teams or an e-mail")


class TodayWindow(_Worklist):
    COLS = [("time", "Time", 70), ("cal", "Calendar", 130), ("patient", "Patient", 210), ("dob", "DOB", 86),
            ("acct", "Pt #", 64), ("visit", "Visit / tags", 150), ("plan", "Plan", 140), ("portal", "Verify on", 135),
            ("elig", "Eligibility", 120), ("alerts", "Needs attention", 300)]

    def __init__(self, app, when: date | None = None):
        super().__init__(app, "Today", "1400x820")
        self.when = when or date.today()
        ttk.Button(self.top, text="◀", width=3, command=lambda: self._go(-1)).pack(side="left")
        self.day_lbl = ttk.Label(self.top, font=("Segoe UI", 13, "bold"), width=24, anchor="center")
        self.day_lbl.pack(side="left", padx=4)
        ttk.Button(self.top, text="▶", width=3, command=lambda: self._go(1)).pack(side="left")
        ttk.Button(self.top, text="Today", command=lambda: self._go(0, date.today())).pack(side="left", padx=(10, 2))
        ttk.Button(self.top, text="Tomorrow", command=lambda: self._go(0, self._next_clinic_day())).pack(side="left", padx=2)
        self._calendar_box(self.when)
        self.only = tk.BooleanVar(value=False)
        ttk.Checkbutton(self.top, text="Only what needs attention", variable=self.only, command=self._show).pack(side="left", padx=10)
        self.blocks = tk.BooleanVar(value=False)
        ttk.Checkbutton(self.top, text="Show blocks & notes", variable=self.blocks, command=self._show).pack(side="left")
        ttk.Button(self.top, text="Refresh", command=self._load).pack(side="right")
        ttk.Button(self.bar, text="Copy chart-pull list", command=self._copy_pull).pack(side="left", padx=6)
        ttk.Button(self.bar, text="Copy eligibility list", command=self._copy_elig).pack(side="left")
        ttk.Label(self.bar, text="Right-click a visit to record its eligibility check", foreground="gray").pack(side="left", padx=10)
        self.menu = tk.Menu(self, tearoff=False)
        self.menu.add_command(label="Eligible - coverage active", command=lambda: self._mark("Active"))
        self.menu.add_command(label="Inactive - coverage ended", command=lambda: self._mark("Inactive"))
        self.menu.add_command(label="Problem… (add a note)", command=lambda: self._mark("Problem"))
        self.menu.add_separator()
        self.menu.add_command(label="Open chart", command=self._open_chart)
        self.tree.bind("<Button-3>", self._popup)
        self._load()

    def _popup(self, ev):
        iid = self.tree.identify_row(ev.y)
        if iid and iid in self.by_iid and self.by_iid[iid].patient:
            self.tree.selection_set(iid)
            self.menu.tk_popup(ev.x_root, ev.y_root)

    def _mark(self, status: str):
        from tkinter import simpledialog
        from . import eligibility
        sel = self.tree.selection()
        if not sel or sel[0] not in self.by_iid:
            return
        r = self.by_iid[sel[0]]
        note = ""
        if status == "Problem":
            note = simpledialog.askstring("Eligibility problem", f"What's wrong with {r.name}'s coverage?", parent=self) or ""
            if not note:
                return
        by = (self.app.cfg.get("initials") or self.app.cfg.get("signature") or "").lstrip("*")
        eligibility.record(self.store, r.last_key, r.dob.isoformat(), status, plan=r.plan, detail=note,
                           by=f"*{by}" if by else "", acct=r.acct)
        r.elig = eligibility.latest(self.store, r.last_key, r.dob.isoformat())
        worklists.refresh_alerts(r)
        self._show()
        self.status.configure(text=f"{r.name}: eligibility {status.lower()} recorded {r.elig.checked:%m/%d/%Y}")

    @staticmethod
    def _next_clinic_day() -> date:
        d = date.today() + timedelta(days=1)
        while d.weekday() >= 5:
            d += timedelta(days=1)
        return d

    def _go(self, step: int, to: date | None = None):
        self.when = to or (self.when + timedelta(days=step))
        self._load()

    def _load(self):
        self.day_lbl.configure(text=f"{self.when:%A %m/%d/%Y}")
        when = self.when
        self._run(lambda progress: worklists.day(self.store, when, progress=progress))

    def _show(self):
        self.tree.delete(*self.tree.get_children())
        self.by_iid = {}
        shown = [r for r in self.rows if self._visible(r) and (r.patient or self.blocks.get())
                 and (r.needs_attention or not self.only.get())]
        for i, r in enumerate(shown):
            iid = str(i)
            self.by_iid[iid] = r
            tag = r.tone if r.patient else "block"
            elig = r.elig.label() if r.elig and r.elig.fresh else ("due" if r.patient and not r.portal.startswith("Cash") else "")
            self.tree.insert("", "end", iid=iid, tags=(tag,), values=(
                f"{r.start:%I:%M %p}".lstrip("0"), r.cal, r.name, f"{r.dob:%m/%d/%Y}" if r.dob else "", r.acct,
                r.visit, r.plan, r.portal, elig,
                "  ·  ".join(t for t, _tone in r.alerts if not t.startswith("Elig"))))
        pts = [r for r in self.rows if r.patient and self._visible(r)]
        count = lambda pred: sum(1 for r in pts if pred(r))  # noqa: E731
        self.summary.configure(text=(
            f"{len(pts)} patients   ·   {count(lambda r: r.auth_needed and r.auth_status != 'OK')} auth problems   ·   "
            f"{count(lambda r: r.balance and r.balance.self > 0)} self balances   ·   "
            f"{count(lambda r: r.balance and r.balance.credit)} credits   ·   "
            f"{count(lambda r: 'abn' in ' '.join(r.categories).lower())} ABN forms   ·   "
            f"{count(lambda r: any(t == 'Check header' for t, _x in r.alerts))} headers to check   ·   "
            f"{count(lambda r: r.mips)} MIPS to do   ·   "
            f"eligibility checked {count(lambda r: r.elig and r.elig.fresh)} of {len(pts)}"))
        self.status.configure(text=f"{self.when:%m/%d/%Y} · balances from the latest Veradigm check per patient · "
                                   "double-click a row for the chart")

    def _copy_elig(self):
        text = worklists.eligibility_list([r for r in self.rows if self._visible(r)])
        if not text:
            messagebox.showinfo("Eligibility list", "No patients on this day.", parent=self)
            return
        self._copy(text, f"Eligibility list for {self.when:%m/%d/%Y}")

    def _copy_pull(self):
        rows = [r for r in self.rows if self._visible(r)]
        text = worklists.pull_list(rows)
        if not text:
            messagebox.showinfo("Chart-pull list", "No patients on this day.", parent=self)
            return
        self._copy(text, f"Chart-pull list for {self.when:%m/%d/%Y}")


class AuthWorklistWindow(_Worklist):
    COLS = [("when", "Visit", 125), ("cal", "Calendar", 135), ("patient", "Patient", 215), ("dob", "DOB", 88),
            ("plan", "Plan", 160), ("status", "Authorization", 130), ("detail", "Details", 270), ("codes", "Codes (units)", 220),
            ("notes", "Notes", 220)]
    CHART_TAB = "auth"
    ORDER = {"No auth": 0, "Expired": 1, "Starts after visit": 2, "Test not on auth": 3, "Codes not listed": 4,
             "Ends soon": 5, "OK": 6}

    def __init__(self, app):
        super().__init__(app, "Authorization worklist", "1500x820")
        start = TodayWindow._next_clinic_day()
        ttk.Label(self.top, text="From").pack(side="left")
        self.start = tk.StringVar(value=f"{start:%m/%d/%Y}")
        ttk.Entry(self.top, textvariable=self.start, width=11).pack(side="left", padx=4)
        ttk.Label(self.top, text="for").pack(side="left")
        self.days = tk.IntVar(value=14)
        ttk.Spinbox(self.top, from_=1, to=62, textvariable=self.days, width=4).pack(side="left", padx=4)
        ttk.Label(self.top, text="days").pack(side="left")
        self._calendar_box(start)
        self.show_ok = tk.BooleanVar(value=False)
        ttk.Checkbutton(self.top, text="Also show visits that are fine", variable=self.show_ok, command=self._show).pack(side="left", padx=10)
        ttk.Button(self.top, text="Check", command=self._load).pack(side="right")
        ttk.Button(self.bar, text="Copy list", command=self._copy_list).pack(side="left", padx=6)
        ttk.Label(self.bar, text="Plans that need an auth: SCMG, Sharp, Scripps, Health Net, HMOs · auths are read from the "
                                 "appointment notes", foreground="gray").pack(side="left", padx=10)
        self._load()

    def _load(self):
        start = _parse_day(self.start.get())
        if not start:
            messagebox.showwarning("Auth worklist", "Type the start date as MM/DD/YYYY.", parent=self)
            return
        days = max(1, min(int(self.days.get() or 14), 62))
        self._run(lambda progress: worklists.auth_worklist(self.store, start, days, progress=progress))

    def _show(self):
        self.tree.delete(*self.tree.get_children())
        self.by_iid = {}
        shown = [r for r in self.rows if self._visible(r) and (r.auth_status != "OK" or r.notes or self.show_ok.get())]
        shown.sort(key=lambda r: (self.ORDER.get(r.auth_status, 9), r.start))
        for i, r in enumerate(shown):
            iid = str(i)
            self.by_iid[iid] = r
            tone = worklists.AUTH_TONES.get(r.auth_status, "")
            self.tree.insert("", "end", iid=iid, tags=(tone,), values=(
                f"{r.start:%a %m/%d %I:%M %p}", r.cal, r.name, f"{r.dob:%m/%d/%Y}" if r.dob else "", r.plan,
                r.auth_status, r.auth_detail, ", ".join(r.auth.codes) if r.auth else "", " | ".join(r.notes)))
        rows = [r for r in self.rows if self._visible(r)]
        counts = {s: sum(1 for r in rows if r.auth_status == s) for s in self.ORDER}
        parts = [f"{n} {s}" for s, n in counts.items() if n]
        noted = sum(1 for r in rows if r.notes)
        self.summary.configure(text=f"{len(rows)} HMO / medical-group visits   ·   " + "   ·   ".join(parts)
                                    + (f"   ·   {noted} with auth notes" if noted else ""))
        self.status.configure(text="double-click a visit for the patient's authorizations")

    def _copy_list(self):
        lines = [f"Authorization worklist {self.start.get()} + {self.days.get()} days", ""]
        for iid in self.tree.get_children():
            r = self.by_iid[iid]
            lines.append(f"{r.start:%m/%d %I:%M %p}  {r.cal:<16} {r.name:<28} DOB {r.dob:%m/%d/%Y}  {r.plan:<18} "
                         f"{r.auth_status}: {r.auth_detail}" + (f"  [{' | '.join(r.notes)}]" if r.notes else ""))
        self._copy("\n".join(lines) + "\n", "Auth worklist")
