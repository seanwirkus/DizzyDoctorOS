"""Superbill window: find the patient's superbill, show the evidence, and file an amended copy.

Nothing is changed until the person clicks "Amend && file". The original batch PDF is never modified.
"""
from __future__ import annotations

import base64
import os
import queue
import tempfile
import threading
import tkinter as tk
from datetime import date, datetime
from tkinter import messagebox, ttk

from . import amend, rules, workflow

PREVIEW_H = 760  # px


def _png_photo(png: bytes) -> tk.PhotoImage:
    return tk.PhotoImage(data=base64.b64encode(png).decode("ascii"))


def render_png(pdf_path: str, page_index: int, height: int = PREVIEW_H) -> bytes:
    import pymupdf
    with pymupdf.open(pdf_path) as doc:
        page = doc[page_index]
        zoom = height / page.rect.height
        return page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom)).tobytes("png")


def header_strips_png(matches, width: int = 820) -> bytes:
    """The handwritten name / DOB / DOS row of each candidate superbill, stacked with page numbers,
    so a person can pick the right one at a glance (OCR can't read the handwriting)."""
    import pymupdf
    docs, out = {}, pymupdf.open()
    strip_h = 0.075  # of the page height, starting at 4%
    rows = matches[:8]
    first = pymupdf.open(rows[0].batch.path)[rows[0].packet.superbill]
    w = first.rect.width
    h = first.rect.height * strip_h
    page = out.new_page(width=w, height=(h + 14) * len(rows))
    for i, m in enumerate(rows):
        d = docs.setdefault(m.batch.path, pymupdf.open(m.batch.path))
        src = d[m.packet.superbill]
        clip = pymupdf.Rect(0, src.rect.height * 0.04, src.rect.width, src.rect.height * (0.04 + strip_h))
        pix = src.get_pixmap(dpi=110, clip=clip)
        r = pymupdf.Rect(0, i * (h + 14) + 14, w, (i + 1) * (h + 14))
        page.insert_image(r, pixmap=pix)
        page.insert_text((4, r.y0 - 3), f"page {m.page}", fontsize=10, color=(0.85, 0, 0))
    zoom = width / w
    return page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom)).tobytes("png")


class SuperbillDialog(tk.Toplevel):
    def __init__(self, app, item: dict):
        super().__init__(app)
        self.app, self.item, self.cfg = app, item, app.cfg
        self.roster = rules.roster_from(self.cfg)
        self.inv: workflow.Investigation | None = None
        self.jobs: queue.Queue = queue.Queue()
        self.cancel = threading.Event()
        self.page_list: list[int] = []
        self.page_pos = 0
        self.photo = None
        self.amended_pdf = ""
        self.title(f"Superbill - {item.get('name') or 'patient'}  DOS {item.get('dos_display', '')}")
        self.geometry("1320x860")
        self.protocol("WM_DELETE_WINDOW", self._close)
        self._build()
        self.after(100, self._poll)
        threading.Thread(target=self._work, daemon=True).start()

    # ---------------------------------------------------------------- layout
    def _build(self):
        main = ttk.PanedWindow(self, orient="horizontal")
        main.pack(fill="both", expand=True, padx=8, pady=8)

        left = ttk.Frame(main)
        self.canvas = tk.Label(left, text="Looking for the superbill…", anchor="center", background="#f3f4f6")
        self.canvas.pack(fill="both", expand=True)
        nav = ttk.Frame(left)
        nav.pack(fill="x", pady=(6, 0))
        ttk.Button(nav, text="◀", width=3, command=lambda: self._step(-1)).pack(side="left")
        ttk.Button(nav, text="▶", width=3, command=lambda: self._step(1)).pack(side="left", padx=4)
        self.page_lbl = ttk.Label(nav, foreground="gray")
        self.page_lbl.pack(side="left", padx=6)
        ttk.Button(nav, text="Open batch PDF", command=self._open_batch).pack(side="right")
        main.add(left, weight=3)

        right = ttk.Frame(main, padding=(10, 0, 0, 0))
        main.add(right, weight=2)
        r = 0
        ttk.Label(right, text="What I checked", font=("Segoe UI", 10, "bold")).grid(row=r, column=0, columnspan=2, sticky="w"); r += 1
        self.notes = tk.Text(right, height=9, wrap="word", relief="solid", borderwidth=1, font=("Segoe UI", 9))
        self.notes.grid(row=r, column=0, columnspan=2, sticky="nsew", pady=(2, 8)); r += 1
        self.notes.tag_configure("dim", foreground="#6b7280")
        self.notes.tag_configure("warn", foreground="#b45309")

        ttk.Label(right, text="Superbill").grid(row=r, column=0, sticky="w")
        self.match_var = tk.StringVar()
        self.match_cb = ttk.Combobox(right, textvariable=self.match_var, state="readonly", width=48)
        self.match_cb.grid(row=r, column=1, sticky="ew", pady=2); r += 1
        self.match_cb.bind("<<ComboboxSelected>>", lambda e: self._show_match(self.match_cb.current()))

        names = [p.name for p in self.roster]
        ttk.Label(right, text="Rendering provider").grid(row=r, column=0, sticky="w")
        self.prov_var = tk.StringVar()
        ttk.Combobox(right, textvariable=self.prov_var, values=names, state="readonly", width=48).grid(row=r, column=1, sticky="ew", pady=2); r += 1
        ttk.Label(right, text="Attending physician").grid(row=r, column=0, sticky="w")
        self.att_var = tk.StringVar()
        ttk.Combobox(right, textvariable=self.att_var, values=["(none)"] + [p.name for p in self.roster if p.is_physician],
                     state="readonly", width=48).grid(row=r, column=1, sticky="ew", pady=2); r += 1
        ttk.Label(right, text="Medicare incident-to").grid(row=r, column=0, sticky="w")
        self.inc_var = tk.StringVar(value="Leave blank")
        ttk.Combobox(right, textvariable=self.inc_var, values=["Leave blank", "YES", "NO"], state="readonly", width=14).grid(row=r, column=1, sticky="w", pady=2); r += 1
        ttk.Label(right, text="Your initials").grid(row=r, column=0, sticky="w")
        self.init_var = tk.StringVar(value=(self.cfg.get("initials") or self.cfg.get("signature", "")).lstrip("*"))
        ttk.Entry(right, textvariable=self.init_var, width=8).grid(row=r, column=1, sticky="w", pady=2); r += 1

        self.why = tk.Text(right, height=6, wrap="word", relief="flat", background=self.cget("background"), font=("Segoe UI", 9))
        self.why.grid(row=r, column=0, columnspan=2, sticky="ew", pady=(6, 6)); r += 1

        of = self.cfg.get("office_copy_folder", "")
        self.save_office = tk.BooleanVar(value=bool(of))
        oc = ttk.Checkbutton(right, text=f"Save to office folder: {of or '(set it in Settings)'}", variable=self.save_office)
        oc.grid(row=r, column=0, columnspan=2, sticky="w"); r += 1
        if not of:
            oc.state(["disabled"])
        bf = self.cfg.get("billing_folder", "")
        self.save_bill = tk.BooleanVar(value=bool(bf))
        cb = ttk.Checkbutton(right, text=f"Copy to billing folder: {bf or '(set it in Settings)'}", variable=self.save_bill)
        cb.grid(row=r, column=0, columnspan=2, sticky="w"); r += 1
        if not bf:
            cb.state(["disabled"])

        btns = ttk.Frame(right)
        btns.grid(row=r, column=0, columnspan=2, sticky="ew", pady=(10, 0)); r += 1
        ttk.Button(btns, text="Preview amendment", command=self.preview).pack(side="left")
        self.file_btn = ttk.Button(btns, text="Amend && file", command=self.amend_and_file)
        self.file_btn.pack(side="left", padx=6)
        ttk.Button(btns, text="Close", command=self._close).pack(side="right")
        self.status = ttk.Label(right, foreground="gray", wraplength=460)
        self.status.grid(row=r, column=0, columnspan=2, sticky="w", pady=(8, 0))
        right.columnconfigure(1, weight=1)
        right.rowconfigure(1, weight=1)

    # ---------------------------------------------------------------- background work
    def _work(self):
        try:
            inv = workflow.investigate(self.item, self.cfg, self.app.store,
                                       progress=lambda m: self.jobs.put(("status", m)), cancel=self.cancel)
            self.jobs.put(("done", inv))
        except Exception as ex:  # keep the window usable whatever happens
            self.jobs.put(("error", str(ex)))

    def _poll(self):
        try:
            while True:
                kind, val = self.jobs.get_nowait()
                if kind == "status":
                    self.status.configure(text=val)
                elif kind == "error":
                    self.status.configure(text=f"Something went wrong: {val}")
                elif kind == "done":
                    self._show_investigation(val)
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(120, self._poll)

    # ---------------------------------------------------------------- showing results
    def _show_investigation(self, inv: workflow.Investigation):
        self.inv = inv
        self.notes.delete("1.0", "end")
        for n in inv.notes:
            self.notes.insert("end", "• " + n + "\n", "warn" if n.startswith(("Couldn't", "No ", "Can't", "Patient not", "Schedule not", "Superbill folder")) else ())
        a = inv.appointment
        if a is not None:
            i = a.info
            self.notes.insert("end", f"\nInsurance: {i.get('insurance') or '?'}" + (f" / {i['secondary']}" if i.get("secondary") else "")
                              + ("  (Medicare)" if a.medicare else "") + f"\nTags: {', '.join(a.categories) or '-'}\n", "dim")
        for prev in inv.previous:
            self.notes.insert("end", f"\nAlready amended {prev['created'][:16].replace('T', ' ')} by *{prev['initials']}: "
                              f"{prev['provider']} / {prev['attending'] or '-'}\n", "warn")
        self.match_cb["values"] = [f"{m.batch.name}  -  page {m.page}  (score {m.score})" for m in inv.matches] or ["(not found)"]
        d = inv.decision
        if d:
            self.prov_var.set(d.rendering.name if d.rendering else "")
            self.att_var.set(d.attending.name if d.attending else "(none)")
            self.why.delete("1.0", "end")
            self.why.insert("end", f"Suggested with {d.confidence} confidence:\n" + "\n".join("- " + r for r in d.reasons))
            if d.medicare:
                self.why.insert("end", "\n- Medicare patient: decide the incident-to row.")
        if inv.matches:
            self.match_cb.current(0)
            self._show_match(0)
            self.status.configure(text="Check the page and the choices, then click Amend & file.")
            if inv.best.weak:
                self.notes.insert("end", "\nOnly the schedule position points to this page. A superbill missing earlier in the "
                                         "day shifts the rest, so read the handwritten names below and pick the right page "
                                         "in the Superbill list.\n", "warn")
                self._show_strips()
        else:
            self.canvas.configure(text="Superbill not found - see notes", image="")
            self.status.configure(text="")

    def _show_strips(self):
        cands = sorted(self.inv.matches, key=lambda m: (m.batch.path, m.page))
        try:
            self.photo = _png_photo(header_strips_png(cands))
        except Exception as ex:
            self.status.configure(text=f"Can't show the candidates: {ex}")
            return
        self.canvas.configure(image=self.photo, text="")
        self.page_lbl.configure(text="Candidate superbills: name / DOB / DOS rows. Pick the right one in the Superbill list.")

    def _show_match(self, idx: int):
        if not self.inv or idx < 0 or idx >= len(self.inv.matches):
            return
        m = self.inv.matches[idx]
        self.page_list, self.page_pos = m.packet.pages, 0
        self.amended_pdf = ""
        self._render()

    def _step(self, d: int):
        if self.page_list:
            self.page_pos = max(0, min(len(self.page_list) - 1, self.page_pos + d))
            self.amended_pdf = ""
            self._render()

    def _render(self):
        m = self._match()
        if not m:
            return
        try:
            if self.amended_pdf:
                png, label = render_png(self.amended_pdf, 0), "Preview of the amended page (not saved yet)"
            else:
                pg = self.page_list[self.page_pos]
                png = render_png(m.batch.path, pg)
                label = f"{m.batch.name} - page {pg + 1}" + ("  (superbill)" if pg == m.packet.superbill else "") + \
                        f"   ·   packet page {self.page_pos + 1} of {len(self.page_list)}"
        except Exception as ex:
            self.canvas.configure(text=f"Can't show the page: {ex}", image="")
            return
        self.photo = _png_photo(png)
        self.canvas.configure(image=self.photo, text="")
        self.page_lbl.configure(text=label)

    def _match(self):
        if not self.inv or not self.inv.matches:
            return None
        i = self.match_cb.current()
        return self.inv.matches[i if i >= 0 else 0]

    def _open_batch(self):
        m = self._match()
        if m:
            from . import shared
            shared.open_file(m.batch.path)

    # ---------------------------------------------------------------- amending
    def _choices(self):
        m = self._match()
        prov = next((p for p in self.roster if p.name == self.prov_var.get()), None)
        att = next((p for p in self.roster if p.name == self.att_var.get()), None)
        if not m or not prov:
            messagebox.showwarning("Superbill", "Pick the superbill and the rendering provider first.", parent=self)
            return None
        if not prov.is_physician and att is None and not messagebox.askyesno(
                "No attending", "No attending physician is selected. Amend anyway?", parent=self):
            return None
        d = self.inv.decision if self.inv else None
        if d and d.medicare and not prov.is_physician and self.inc_var.get() not in ("YES", "NO"):
            messagebox.showwarning("Medicare", "This is a Medicare patient: choose YES or NO for\n"
                                               "\"Medicare incident-to met\" before amending.", parent=self)
            return None
        return m, prov, att

    def _make(self, m, prov, att) -> str:
        inv = self.inv
        a = inv.appointment
        evidence = f"Outlook sched {a.start:%m/%d} {a.start:%I:%M}".replace(" 0", " ") + f" ({a.calendar})" if a else ""
        st = amend.Stamp(initials=self.init_var.get().strip(), provider=prov.name,
                         attending=f"{att.name.split(',')[0]}, MD ({att.column})" if att and att.column else (att.name if att else ""),
                         evidence=evidence, account=inv.query.account,
                         dos=f"{inv.dos:%m/%d/%Y}" if inv.dos else "")
        out = os.path.join(tempfile.gettempdir(), "DizzyDoctor", f"amend-{datetime.now():%H%M%S}.pdf")
        inc = self.inc_var.get() if self.inc_var.get() in ("YES", "NO") else ""
        amend.amend_page(m.batch.path, m.packet.superbill, out, provider_surname=prov.surname,
                         roster_surnames=[p.surname for p in self.roster],
                         attending_column=att.column if att and not prov.is_physician else "",
                         attending_side=(att.side or self._default_side(att)) if att and not prov.is_physician else "",
                         incident_to=inc, stamp=st)
        return out

    def _default_side(self, att) -> str:
        physicians = [p for p in self.roster if p.is_physician]
        return "left" if physicians and physicians[0] is att else "right"

    def preview(self):
        ch = self._choices()
        if not ch:
            return
        try:
            self.amended_pdf = self._make(*ch)
        except amend.LayoutError as ex:
            messagebox.showerror("Can't mark this page", str(ex), parent=self)
            return
        self._render()
        self.status.configure(text="This is the preview. Nothing is saved until you click Amend & file.")

    def amend_and_file(self):
        ch = self._choices()
        if not ch:
            return
        m, prov, att = ch
        inv = self.inv
        try:
            pdf = self._make(m, prov, att)
        except amend.LayoutError as ex:
            messagebox.showerror("Can't mark this page", str(ex), parent=self)
            return
        last = inv.query.last or (inv.appointment.info.get("last", "") if inv.appointment else "")
        first = inv.query.first or (inv.appointment.info.get("first", "") if inv.appointment else "")
        day = inv.dos or m.batch.day
        saved, billed = [], False
        name = amend.billing_name(m.batch.code, day, last, first)
        try:
            if self.save_office.get() and self.cfg.get("office_copy_folder"):
                saved.append(amend.copy_to(pdf, amend.billing_folder(self.cfg["office_copy_folder"], day), name))
            if self.save_bill.get() and self.cfg.get("billing_folder"):
                saved.append(amend.copy_to(pdf, amend.billing_folder(self.cfg["billing_folder"], day), name))
                billed = True
        except OSError as ex:
            messagebox.showerror("Couldn't save", str(ex), parent=self)
            return
        self.app.store.add_amendment({
            "item_key": self.item.get("key") or "", "account": inv.query.account, "patient": f"{first} {last}".strip(),
            "dos": day.isoformat(), "batch": m.batch.path, "page": m.page, "provider": prov.name,
            "attending": att.name if att else "", "files": saved, "initials": self.init_var.get().strip()})
        self.amended_pdf = pdf
        self._render()
        where = "\n".join(saved) or "(nothing saved - both boxes were unticked)"
        if not billed and saved:
            billed = messagebox.askyesno(
                "Amended", f"Amended superbill saved:\n\n{where}\n\nUpload it to billing's ShareFile Missing folder as\n"
                           f"\"{name}\".\n\nMark this request done now?", parent=self)
        else:
            messagebox.showinfo("Amended", f"Amended superbill saved:\n\n{where}", parent=self)
        self.app.after_amendment(self.item, prov, att, saved, billed)

    def _close(self):
        self.cancel.set()
        self.destroy()
