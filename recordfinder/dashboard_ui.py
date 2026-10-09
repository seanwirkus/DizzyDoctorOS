"""The main window's tabs: Dashboard, Superbills and Balances."""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from datetime import date, datetime, timedelta
from tkinter import filedialog, messagebox, ttk

from . import balances, people, superbill_map, veradigm_reports, worklists

MIDNIGHT, INK, SLATE, TEAL, AQUA, MIST, PAPER = "#00253a", "#0b1f2c", "#46606f", "#036c5f", "#37a4b0", "#eef3f3", "#f7f4ee"


def _pid_alive(pid) -> bool:
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, int(pid))   # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        k32.GetExitCodeProcess(h, ctypes.byref(code))
        k32.CloseHandle(h)
        return code.value == 259                       # STILL_ACTIVE
    except Exception:
        return False


def next_clinic_day(d: date | None = None) -> date:
    d = (d or date.today()) + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


class Card(tk.Frame):
    def __init__(self, master, title: str, action=None, action_text: str = ""):
        super().__init__(master, background="white", highlightbackground="#d5dee2", highlightthickness=1, padx=16, pady=12)
        tk.Label(self, text=title.upper(), font=("Segoe UI", 9, "bold"), fg=SLATE, bg="white").pack(anchor="w")
        self.value = tk.Label(self, text="…", font=("Segoe UI", 22, "bold"), fg=INK, bg="white")
        self.value.pack(anchor="w")
        self.detail = tk.Label(self, text="", font=("Segoe UI", 9), fg=SLATE, bg="white", justify="left", anchor="w",
                               wraplength=360)
        self.detail.pack(anchor="w", fill="x")
        if action:
            ttk.Button(self, text=action_text, command=action).pack(anchor="w", pady=(10, 0))

    def set(self, value: str, detail: str = ""):
        self.value.configure(text=value)
        self.detail.configure(text=detail)


class Dashboard(tk.Frame):
    """Home: the everyday screens one click away, and how complete each piece of the clinic map is."""

    def __init__(self, master, app):
        super().__init__(master, background=PAPER)
        self.app, self.store = app, app.store
        self.jobs: queue.Queue = queue.Queue()
        acts = tk.Frame(self, background=PAPER)
        acts.pack(fill="x", padx=18, pady=(16, 6))
        for text, cmd in (("All patients", app.open_patients), ("Today", app.open_today),
                          ("Auth worklist", app.open_auth_worklist),
                          ("Superbills", lambda: app.nb.select(app.sb_tab)),
                          ("Balances", lambda: app.nb.select(app.bal_tab)),
                          ("MIPS 2026", lambda: app.nb.select(app.mips_tab))):
            ttk.Button(acts, text=text, style="Big.TButton", command=cmd).pack(side="left", padx=(0, 10))
        grid = tk.Frame(self, background=PAPER)
        grid.pack(fill="both", expand=True, padx=12, pady=6)
        self.cards: dict[str, Card] = {}
        if app.client:   # this PC loads the main PC's published data instead of reading Outlook / the drive itself
            outlook = ("outlook", "Schedule (Outlook)", lambda: app.check_for_new_data(reschedule=False), "Check for new data")
            drive = ("drive", "Clinic drive", lambda: app.check_for_new_data(reschedule=False), "Check for new data")
        else:
            outlook = ("outlook", "Schedule (Outlook)", lambda: app.start_outlook_sync(), "Sync Outlook now")
            drive = ("drive", "Clinic drive", app.start_index, "Re-scan the drive")
        specs = [("patients", "Patients", app.open_patients, "Open the directory"), outlook, drive,
                 ("superbills", "Superbills", lambda: app.nb.select(app.sb_tab), "Open Superbills"),
                 ("veradigm", "Balances (Veradigm)", lambda: app.nb.select(app.bal_tab), "Open Balances"),
                 ("next", "Next clinic day", app.open_today, "Open Today"),
                 ("mips", "MIPS 2026 - rows still blank", lambda: app.nb.select(app.mips_tab), "Open MIPS")]
        for i, (key, title, cmd, label) in enumerate(specs):
            c = Card(grid, title, cmd, label)
            c.grid(row=i // 3, column=i % 3, sticky="nsew", padx=6, pady=6)
            self.cards[key] = c
        for col in range(3):
            grid.columnconfigure(col, weight=1)
        self.after(300, self.refresh)
        self.after(200, self._poll)

    def _poll(self):
        try:
            while True:
                key, value, detail = self.jobs.get_nowait()
                self.cards[key].set(value, detail)
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(300, self._poll)

    def refresh(self):
        """Fast counts now; the next clinic day (which reads auths) in the background."""
        from . import outlook_cache
        db = self.store.reader()
        try:
            n = people.count(self.store)
            links = db.execute("SELECT COUNT(*) FROM file_links").fetchone()[0] if n else 0
            mapped = self.store.meta_get("patients_mapped_at")
            self.cards["patients"].set(f"{n:,}", f"{links:,} documents linked to patients"
                                                 + (f" · map built {mapped[5:10].replace('-', '/')} {mapped[11:16]}" if mapped else ""))
        except Exception as ex:
            self.cards["patients"].set("-", f"not mapped yet ({ex})")
        try:
            appts, synced = outlook_cache.status(self.store)
            first = db.execute("SELECT MIN(start) FROM appts").fetchone()[0] or ""
            self.cards["outlook"].set(f"{appts:,}", "bookings" + (f" back to {first[:4]}" if first else "")
                                      + (f" · synced {synced[5:10].replace('-', '/')} {synced[11:16]}" if synced else ""))
        except Exception:
            self.cards["outlook"].set("-", "not synced yet")
        n_files, when, roots = self.store.index_info()
        self.cards["drive"].set(f"{n_files:,}", "files indexed on " + ", ".join(roots or ["-"])
                                + (f" · scanned {when:%m/%d %I:%M %p}" if when else ""))
        s = superbill_map.status(self.store)
        by = s["by_status"]
        prog = s["progress"]
        running = not self.app.client and prog.get("state") == "running" and _pid_alive(prog.get("pid", 0))
        self.cards["superbills"].set(f"{s['placed']:,}", f"superbills placed · {by.get('mapped', 0):,} of {s['batches']:,} "
                                     "batches mapped" + (f"\n{prog.get('text', '')}" if running else ""))
        vouchers, open_pts, newest = veradigm_reports.status(self.store)
        fi = len(balances.latest_totals(self.store))
        self.cards["veradigm"].set(f"{open_pts:,}", "patients with an open balance"
                                   + (f" (report {newest[5:7]}/{newest[8:10]}/{newest[:4]})" if newest else " - no report imported yet")
                                   + f" · {fi:,} Financial Inquiry checks")
        day = next_clinic_day()
        self.cards["next"].set(f"{day:%a %m/%d}", "checking the schedule…")

        def work():
            try:
                rows = worklists.day(self.store, day)
                pts = [r for r in rows if r.patient]
                auth = sum(1 for r in pts if r.auth_needed and r.auth_status != "OK")
                selfb = sum(1 for r in pts if r.balance and r.balance.self > 0)
                abn = sum(1 for r in pts if "abn" in " ".join(r.categories).lower())
                hdr = sum(1 for r in pts if any(t == "Check header" for t, _x in r.alerts))
                self.jobs.put(("next", f"{len(pts)} patients", f"{day:%A %m/%d} · {auth} auth problems · {selfb} self "
                                                              f"balances · {abn} ABN forms · {hdr} headers to check"))
            except Exception as ex:
                self.jobs.put(("next", f"{day:%a %m/%d}", f"couldn't check: {ex}"))
            try:
                from .mips_ui import dashboard_summary
                self.jobs.put(("mips", *dashboard_summary()))
            except Exception as ex:
                self.jobs.put(("mips", "-", f"couldn't read the MIPS workbooks ({ex})"))

        threading.Thread(target=work, daemon=True).start()


class SuperbillsTab(ttk.Frame):
    """Map every scanned superbill batch to its patients (a background job), and find a patient's superbills."""

    YEAR_COLS = [("year", "Year", 80), ("batches", "Batches", 90), ("mapped", "Mapped", 90), ("placed", "Superbills placed", 140)]
    HIT_COLS = [("day", "Date of service", 110), ("code", "Batch", 90), ("page", "Page", 60), ("sure", "Placed by", 150),
                ("batch", "Batch file", 480)]

    def __init__(self, master, app):
        super().__init__(master, padding=12)
        self.app, self.store = app, app.store
        self.review_jobs: queue.Queue = queue.Queue()
        self.review_running = False
        head = ttk.Frame(self)
        head.pack(fill="x")
        ttk.Label(head, text="Superbill map", font=("Segoe UI", 13, "bold")).pack(side="left")
        self.start_btn = ttk.Button(head, text="▶ Map superbills", command=self.start)
        self.start_btn.pack(side="left", padx=(16, 6))
        ttk.Button(head, text="Stop", command=self.stop).pack(side="left")
        self.review_btn = ttk.Button(head, text="Build manual review packet…", command=self.check_batch_pdfs)
        self.review_btn.pack(side="left", padx=(14, 0))
        self.state = ttk.Label(self, foreground=SLATE, font=("Segoe UI", 10))
        self.state.pack(anchor="w", pady=(6, 2))
        self.review_state = ttk.Label(self, foreground=SLATE, font=("Segoe UI", 10))
        self.review_state.pack(anchor="w", pady=(0, 2))
        ttk.Label(self, text="Reads every scanned batch in the superbill folder once (in the background, several pages at "
                             "a time) and places each patient on their superbill page using that day's schedule. It can "
                             "be stopped any time and continues where it left off; charts then show each visit's "
                             "superbill at once.", foreground="gray", wraplength=1100, justify="left").pack(anchor="w")
        body = ttk.PanedWindow(self, orient="horizontal")
        body.pack(fill="both", expand=True, pady=(10, 0))
        left = ttk.Frame(body)
        self.years = app._make_tree(left, self.YEAR_COLS)
        body.add(left, weight=1)
        right = ttk.Frame(body)
        f = ttk.Frame(right)
        f.pack(fill="x")
        ttk.Label(f, text="Find a patient's superbills - last name").pack(side="left")
        self.q_last = tk.StringVar()
        ttk.Entry(f, textvariable=self.q_last, width=18).pack(side="left", padx=4)
        ttk.Label(f, text="DOB").pack(side="left")
        self.q_dob = tk.StringVar()
        e = ttk.Entry(f, textvariable=self.q_dob, width=11)
        e.pack(side="left", padx=4)
        e.bind("<Return>", lambda ev: self.find())
        ttk.Button(f, text="Find", command=self.find).pack(side="left")
        self.hits = app._make_tree(right, self.HIT_COLS)
        self.hits.bind("<Double-1>", lambda ev: self.open_hit())
        self.hit_rows: dict[str, superbill_map.Sheet] = {}
        body.add(right, weight=3)
        self.after(500, self.refresh)
        self.after(250, self._poll_review)

    def _poll_review(self):
        try:
            while True:
                kind, data = self.review_jobs.get_nowait()
                if kind == "status":
                    self.review_state.configure(text=data)
                elif kind == "done":
                    pdf, csv_path, summary = data
                    self.review_running = False
                    self.review_btn.configure(state="normal")
                    self.review_state.configure(text=summary)
                    from .app import open_path
                    open_path(pdf)
                    messagebox.showinfo("Superbill batch check",
                                        f"{summary}\n\nReview PDF:\n{pdf}\n\nSpreadsheet list:\n{csv_path}", parent=self)
                elif kind == "error":
                    self.review_running = False
                    self.review_btn.configure(state="normal")
                    self.review_state.configure(text="Batch check stopped with an error.")
                    messagebox.showerror("Superbill batch check", data, parent=self)
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(250, self._poll_review)

    def check_batch_pdfs(self):
        if self.review_running:
            return
        from . import ocr, superbill_check
        reason = ocr.unavailable_reason()
        if reason:
            messagebox.showerror("Superbill batch check", f"The checker can't read scans on this PC: {reason}.", parent=self)
            return
        initial = self.app.cfg.get("superbill_root") or os.getcwd()
        if not os.path.isdir(initial):
            initial = os.getcwd()
        picked = filedialog.askopenfilenames(parent=self, title="Choose superbill batches",
                                             initialdir=initial, filetypes=[("PDF files", "*.pdf")])
        files = superbill_check.batch_files(list(picked))
        if not files:
            return
        out_dir = os.path.dirname(os.path.abspath(files[0]))
        self.review_running = True
        self.review_btn.configure(state="disabled")
        self.review_state.configure(text=f"Starting check of {len(files)} batch{'es' if len(files) != 1 else ''}…")

        def work():
            try:
                workers = max(1, min(6, (os.cpu_count() or 2) - 1))
                results = superbill_check.check_batches(files, self.store, self.app.cfg, workers,
                                                         progress=lambda m: self.review_jobs.put(("status", m)))
                pdf, csv_path = superbill_check.review_paths(out_dir, results)
                sheets = superbill_check.write_manual_review(results, pdf)
                superbill_check.write_manual_csv(results, csv_path)
                summary = (f"Prepared a manual review queue for {len(results)} batches and {sheets} superbills. "
                           "It does not interpret pen marks or draw automatic finding boxes.")
                self.review_jobs.put(("done", (pdf, csv_path, summary)))
            except Exception as ex:
                self.review_jobs.put(("error", str(ex)))

        threading.Thread(target=work, daemon=True).start()

    def _running(self) -> bool:
        prog = superbill_map.status(self.store)["progress"]
        return not self.app.client and prog.get("state") == "running" and _pid_alive(prog.get("pid", 0))

    def refresh(self):
        s = superbill_map.status(self.store)
        by, prog = s["by_status"], s["progress"]
        running = self._running()
        waiting = by.get("waiting", 0) + by.get("error", 0)
        text = (f"{s['placed']:,} superbills placed · {by.get('mapped', 0):,} of {s['batches']:,} batches mapped"
                + (f" · {waiting:,} still to read" if waiting else ""))
        if running:
            text += f"\n▶ {prog.get('text', 'running')}"
        elif prog.get("state") in ("done", "stopped"):
            text += f"\n{'Finished' if prog['state'] == 'done' else 'Stopped'}: {prog.get('text', '')}"
        self.state.configure(text=text)
        self.start_btn.configure(state="disabled" if running else "normal")
        self.years.delete(*self.years.get_children())
        for y, n, mapped, placed in s["years"]:
            self.years.insert("", "end", values=(y, f"{n:,}", f"{mapped or 0:,}", f"{placed or 0:,}"))
        if self.winfo_exists():
            self.after(4000 if running else 20000, self.refresh)

    def start(self):
        if self.app.client:
            messagebox.showinfo("Superbills", "The superbill map is built on the main PC. It comes to this PC with the "
                                              "clinic data, so nothing needs to run here.", parent=self)
            return
        if self._running():
            return
        if not self.app.cfg.get("superbill_root"):
            messagebox.showinfo("Superbills", "Set the superbill folder in Settings first.", parent=self)
            return
        from . import shared
        workers = str(max(2, min(8, (os.cpu_count() or 4) // 3)))
        if getattr(sys, "frozen", False):
            # its own copy of the program, started as its own program: it never locks the app's .exe (updates) or
            # shares its temp folder (closing the app while it maps)
            cmd = [shared.mapper_exe(), "--map-superbills", "--workers", workers]
        else:
            cmd = [sys.executable, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py"),
                   "--map-superbills", "--workers", workers]
        subprocess.Popen(cmd, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), env=shared.fresh_env())
        self.state.configure(text="Starting the superbill map…")
        self.after(3000, self.refresh)

    def stop(self):
        self.store.meta_set("sb_stop", "1")
        self.state.configure(text="Stopping after the batches being read now…")

    def find(self):
        from .superbills import _fold
        last = _fold(self.q_last.get().replace(" ", ""))
        dobs = people._dobs(self.q_dob.get())
        self.hits.delete(*self.hits.get_children())
        self.hit_rows = {}
        if not last or not dobs:
            self.state.configure(text="Type the last name and DOB (MM/DD/YYYY).")
            return
        for i, sh in enumerate(superbill_map.for_patient(self.store, last, dobs[0].isoformat())):
            iid = str(i)
            self.hit_rows[iid] = sh
            self.hits.insert("", "end", iid=iid, values=(f"{sh.day:%m/%d/%Y}", sh.code, sh.page,
                                                         "printed name/DOB" if sh.sure else f"schedule order ({sh.how})",
                                                         os.path.basename(sh.batch)))

    def open_hit(self):
        from . import attach
        from .app import open_path
        sel = self.hits.selection()
        if not sel:
            return
        sh = self.hit_rows[sel[0]]
        name = f"{self.q_last.get().strip().title()} {sh.code} {sh.day:%m.%d.%y} SB p{sh.page}.pdf"
        try:
            open_path(attach.extract_page(sh.batch, sh.page - 1, os.path.join(attach.attachments_dir(), name)))
        except Exception as ex:
            messagebox.showerror("Superbill", f"Couldn't open the page: {ex}", parent=self)


class BalancesTab(ttk.Frame):
    """Open balances from the Veradigm reports, with the name Veradigm and Outlook each have for the patient."""

    BAL_COLS = [("acct", "Pt #", 70), ("vname", "Veradigm name", 190), ("oname", "Outlook name", 190), ("dob", "DOB", 88),
                ("open", "Open (all payers)", 120), ("n", "Visits open", 80), ("oldest", "Oldest visit", 95),
                ("a0", "0-30", 80), ("a1", "31-60", 80), ("a2", "61-90", 80), ("a3", "Over 90", 85), ("self", "Self (last check)", 120)]
    NAME_COLS = [("acct", "Pt #", 70), ("vname", "Veradigm name", 220), ("oname", "Outlook name", 260), ("dob", "DOB", 88),
                 ("issue", "What to check", 260)]
    VIEWS = ["Open balances", "Over 90 days", "Credits", "Name check (Veradigm vs Outlook)"]

    def __init__(self, master, app):
        super().__init__(master, padding=12)
        self.app, self.store = app, app.store
        self.jobs: queue.Queue = queue.Queue()
        head = ttk.Frame(self)
        head.pack(fill="x")
        ttk.Label(head, text="Balances", font=("Segoe UI", 13, "bold")).pack(side="left")
        ttk.Button(head, text="Import Veradigm reports", command=self.import_reports).pack(side="left", padx=(16, 6))
        ttk.Label(head, text="Show:").pack(side="left", padx=(12, 4))
        self.view = tk.StringVar(value=self.VIEWS[0])
        cb = ttk.Combobox(head, textvariable=self.view, values=self.VIEWS, state="readonly", width=32)
        cb.pack(side="left")
        cb.bind("<<ComboboxSelected>>", lambda e: self.show())
        ttk.Label(head, text="Find:").pack(side="left", padx=(12, 4))
        self.q = tk.StringVar()
        self.q.trace_add("write", lambda *_: self.show())
        ttk.Entry(head, textvariable=self.q, width=22).pack(side="left")
        ttk.Button(head, text="Copy list", command=self.copy).pack(side="right")
        self.summary = ttk.Label(self, foreground=SLATE, font=("Segoe UI", 10))
        self.summary.pack(anchor="w", pady=(6, 2))
        ttk.Label(self, text="From Veradigm's Revenue Detail Report (saved in Desktop\\ALL): what is still open on each visit, "
                             "insurance and patient together, aged by date of service. \"Self (last check)\" is the patient's "
                             "own balance from the last Financial Inquiry run. Double-click a patient for the chart.",
                  foreground="gray", wraplength=1200, justify="left").pack(anchor="w")
        self.box = ttk.Frame(self)
        self.box.pack(fill="both", expand=True, pady=(8, 0))
        self.tree = None
        self.rows: list = []
        self.by_iid: dict = {}
        self.after(700, self.show)
        self.after(200, self._poll)

    def _poll(self):
        try:
            while True:
                kind, msg = self.jobs.get_nowait()
                self.summary.configure(text=msg)
                if kind == "done":
                    self.show()
                    self.app.dash.refresh()
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(300, self._poll)

    def import_reports(self):
        if self.app.client:
            messagebox.showinfo("Balances", "Veradigm reports are read on the main PC. The balances come to this PC with "
                                            "the clinic data - use \"Check for new data\" on the Dashboard for the newest.",
                                parent=self)
            return
        self.summary.configure(text="Reading Veradigm reports in Desktop\\ALL…")
        folder = self.app.cfg.get("veradigm_reports_folder") or veradigm_reports.DEFAULT_FOLDER

        def work():
            try:
                n = veradigm_reports.import_folder(self.store, folder, progress=lambda m: self.jobs.put(("status", m)))
                balances.import_folder(self.store, self.app.cfg.get("veradigm_results_folder") or balances.DEFAULT_FOLDER)
                self.jobs.put(("done", f"{n:,} vouchers imported" if n else "No new Veradigm reports in Desktop\\ALL."))
            except Exception as ex:
                self.jobs.put(("status", f"Couldn't import: {ex}"))

        threading.Thread(target=work, daemon=True).start()

    def _make(self, cols):
        for w in self.box.winfo_children():
            w.destroy()
        self.tree = self.app._make_tree(self.box, cols)
        self.tree.bind("<Double-1>", lambda e: self.open_chart())
        self.tree.tag_configure("old", background="#fde8e4")
        self.tree.tag_configure("credit", background="#e3f4ec")

    def _directory(self) -> dict:
        try:
            got = self.store.reader().execute("SELECT ltrim(acct,'0'), last, first, dob, id FROM patients WHERE acct != ''").fetchall()
        except Exception:
            return {}
        out: dict = {}
        for a, last, first, dob, pid in got:
            out.setdefault(a, []).append((f"{last}, {first}", dob, pid))
        return out

    def show(self):
        view = self.view.get()
        q = self.q.get().strip().lower()
        d = self._directory()
        self.by_iid = {}
        if view.startswith("Name check"):
            self._make(self.NAME_COLS)
            rows = veradigm_reports.cross_check(self.store)
            rows = [r for r in rows if not q or q in f"{r.acct} {r.veradigm} {r.outlook}".lower()]
            for i, r in enumerate(rows[:3000]):
                iid = str(i)
                self.by_iid[iid] = (r.acct, d.get(r.acct, [("", "", None)])[0][2])
                self.tree.insert("", "end", iid=iid, values=(r.acct, r.veradigm, r.outlook,
                                                             f"{r.dob[5:7]}/{r.dob[8:10]}/{r.dob[:4]}" if r.dob else "", r.issue))
            self.summary.configure(text=f"{len(rows):,} patient numbers to check between Veradigm and the Outlook headers")
            return
        self._make(self.BAL_COLS)
        totals = veradigm_reports.open_totals(self.store)
        fi = balances.latest_totals(self.store)
        rows = list(totals.values())
        if view == "Over 90 days":
            rows = [r for r in rows if r.aging[3] > 0.004]
        elif view == "Credits":
            rows = [r for r in rows if r.total < 0]
        if q:
            rows = [r for r in rows if q in f"{r.acct} {r.name} {' '.join(x[0] for x in d.get(r.acct, []))}".lower()]
        rows.sort(key=lambda r: (r.total if view == "Credits" else -r.total))
        for i, r in enumerate(rows[:3000]):
            iid = str(i)
            oname, dob, pid = (d.get(r.acct) or [("", "", None)])[0]
            self.by_iid[iid] = (r.acct, pid)
            selfbal = fi.get(r.acct)
            self.tree.insert("", "end", iid=iid, tags=("credit",) if r.total < 0 else ("old",) if r.aging[3] > 0.004 else (),
                             values=(r.acct, r.name, oname, f"{dob[5:7]}/{dob[8:10]}/{dob[:4]}" if dob else "",
                                     f"${r.total:,.2f}", r.vouchers, f"{r.oldest:%m/%d/%Y}" if r.oldest else "",
                                     *(f"${x:,.2f}" for x in r.aging),
                                     f"${selfbal[0]:,.2f} ({selfbal[2][5:7]}/{selfbal[2][8:10]})" if selfbal else ""))
        total = sum(r.total for r in rows)
        newest = max((r.as_of for r in totals.values() if r.as_of), default=None)
        self.summary.configure(text=(f"{len(rows):,} patients · ${total:,.2f} open" + (" (showing the first 3,000)" if len(rows) > 3000 else "")
                                     + (f" · Veradigm report of {newest:%m/%d/%Y}" if newest else
                                        " · no Veradigm report imported yet - save a Revenue Detail Report (Summarize by "
                                        "Voucher) in Desktop\\ALL and click Import")))

    def open_chart(self):
        from .record_ui import PatientRecord
        sel = self.tree.selection() if self.tree else ()
        if not sel or sel[0] not in self.by_iid:
            return
        acct, pid = self.by_iid[sel[0]]
        found = people.lookup(self.store, "", account=acct)
        if pid is None and len(found) == 1:
            pid = found[0].id
        if pid is None:
            messagebox.showinfo("Balances", f"No patient in the directory carries Pt # {acct} in an Outlook header yet.",
                                parent=self)
            return
        p = next((x for x in found if x.id == pid), None) or (found[0] if found else None)
        if p:
            PatientRecord(self.app, p.first, p.last, p.dob, p.acct, patient_id=p.id, tab="balance")

    def copy(self):
        if not self.tree:
            return
        cols = [self.tree.heading(c, "text") for c in self.tree["columns"]]
        lines = ["\t".join(cols)] + ["\t".join(str(v) for v in self.tree.item(i, "values")) for i in self.tree.get_children()]
        self.clipboard_clear()
        self.clipboard_append("\n".join(lines) + "\n")
        self.summary.configure(text=f"{len(lines) - 1:,} rows copied - paste into Excel")
