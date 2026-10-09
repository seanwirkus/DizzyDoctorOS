"""The app's screens: a small local web server the window talks to.

It listens on 127.0.0.1 only, on a random port, and every request must carry the token the app
generated when it started (as a cookie set from the launch URL). Nothing is reachable from other
computers. The data behind the screens comes from a provider (providers.py): the clinic's own
data through the rest of the app, or a made-up demo clinic.
"""
from __future__ import annotations

import json
import logging
import mimetypes
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from datetime import date, datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

log = logging.getLogger("recordfinder.charting")

from . import coding, compose, dictation, distributions, note, phrases, scope_import, scribe, story, superbill, users, visits
from .version_info import VERSION

def _web_dir() -> str:
    """Chart files sit next to this module. A frozen Mac app also keeps them under sys._MEIPASS."""
    bundled = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
    if os.path.isdir(bundled):
        return bundled
    root = getattr(sys, "_MEIPASS", "")
    if root:
        alt = os.path.join(root, "recordfinder", "charting", "web")
        if os.path.isdir(alt):
            return alt
    return bundled


WEB_DIR = _web_dir()
MIME = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8",
        ".mjs": "text/javascript; charset=utf-8", ".png": "image/png", ".svg": "image/svg+xml", ".json": "application/json",
        ".woff2": "font/woff2", ".ico": "image/x-icon"}
TEXT_DOCS = (".txt", ".md", ".csv", ".log", ".json")
OFFICE_TABS = ("billing", "superbills", "balances", "mips", "dashboard", "today", "auth", "patients")


class ChartContext:
    """What the screens need from the rest of the app."""

    def __init__(self, cfg: dict | None = None, data_dir: str = "", store=None, find_patient=None, patient_notes=None, provider=None):
        self.cfg = cfg or {}
        self.data_dir = data_dir or tempfile.gettempdir()
        self.store = store
        self.provider = provider
        # older callers pass the two callbacks instead of a provider
        self.find_patient = find_patient or (provider.search_patients if provider else None)
        self.patient_notes = patient_notes or (provider.patient_notes if provider else None)
        self.launch_classic = None
        self.window = None
        self.token = secrets.token_urlsafe(24)
        self.previews: dict[str, str] = {}
        self.print_lock = threading.RLock()
        self.recent: list[dict] = []
        self.users = users.UserStore(os.path.join(self.data_dir, "users.db"))
        self.sessions: dict[str, tuple[int, float]] = {}

    def status(self) -> dict:
        base = {"clinic": "", "bookings": 0, "synced": "", "demo": False, "initials": ""}
        if self.provider and hasattr(self.provider, "status"):
            try:
                base.update(self.provider.status())
            except Exception:
                pass
        base.update({"version": VERSION, "dictation": dictation.available(), "visit_timer": bool(self.cfg.get("visit_timer", False)), "practice_directory": str(self.cfg.get("practice_directory") or ""),
                     "model": self.cfg.get("dictation_model", "small.en"),
                     "notes_root": note.notes_root(self.cfg), "today": date.today().strftime("%m/%d/%Y"),
                     "classic": bool(self.launch_classic), "platform": sys.platform, "recent": self.recent[:12]})
        return base

    def remember(self, patient: dict) -> None:
        key = (str(patient.get("id") or ""), patient.get("last", ""), patient.get("first", ""))
        self.recent = [p for p in self.recent if (str(p.get("id") or ""), p.get("last", ""), p.get("first", "")) != key]
        self.recent.insert(0, patient)
        del self.recent[12:]


def _quiet(fn, default):
    """Provider calls against an empty clinic should come back empty, not as a stack trace."""
    try:
        value = fn()
    except Exception:
        log.exception("chart data request failed")
        return default
    return default if value is None else value


def _known_roots(ctx: ChartContext) -> list[str]:
    roots: list[str] = []
    if ctx.data_dir:
        roots.append(ctx.data_dir)
    roots.append(tempfile.gettempdir())
    cfg = ctx.cfg or {}
    for key in ("notes_folders", "search_folders"):
        val = cfg.get(key) or []
        if isinstance(val, str):
            val = [val]
        roots.extend(v for v in val if v)
    for key in ("missing_folder", "superbill_root", "office_copy_folder", "prepped_notes_folder",
                "veradigm_reports_folder", "mips_folder", "share_folder"):
        if cfg.get(key):
            roots.append(str(cfg[key]))
    prov = getattr(ctx, "provider", None)
    for attr in ("root", "notes_dir"):
        val = getattr(prov, attr, None) if prov else None
        if val:
            roots.append(val)
    try:
        roots.append(note.notes_root(cfg))
    except Exception:
        pass
    tmpl = cfg.get("note_template") or ""
    if tmpl:
        roots.append(os.path.dirname(os.path.abspath(tmpl)))
    return roots


def _allowed_file(ctx: ChartContext, raw: str) -> str | None:
    """A real file under a clinic root. Empty paths, missing files, and anything outside those roots are refused."""
    if not raw or "\x00" in raw:
        return None
    try:
        real = os.path.realpath(raw)
    except (OSError, ValueError):
        return None
    if not os.path.isfile(real):
        return None
    real_n = os.path.normcase(real)
    for root in _known_roots(ctx):
        if not root:
            continue
        try:
            base = os.path.normcase(os.path.realpath(root))
        except (OSError, ValueError):
            continue
        if os.path.isfile(base) and real_n == base:
            return real
        if not os.path.isdir(base):
            continue
        try:
            if os.path.commonpath([real_n, base]) == base:
                return real
        except ValueError:
            continue
    return None


class Handler(BaseHTTPRequestHandler):
    ctx: ChartContext = None  # type: ignore[assignment]
    server_version = "DizzyDoctor"

    # ---- plumbing
    def log_message(self, fmt, *args):  # quiet
        pass

    def _authed(self) -> bool:
        q = parse_qs(urlparse(self.path).query)
        if q.get("t", [""])[0] == self.ctx.token:
            return True
        return f"ddos_token={self.ctx.token}" in self.headers.get("Cookie", "")

    def _send(self, status: int, body: bytes, ctype: str = "application/json", extra: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, status: int = 200, extra: dict | None = None):
        self._send(status, json.dumps(obj, default=str).encode("utf-8"), extra=extra)

    def _body(self) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def _query(self) -> dict:
        return {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}

    def _provider(self, name: str, *args):
        p = self.ctx.provider
        if p is None or not hasattr(p, name):
            return None
        return getattr(p, name)(*args)

    def _cookies(self) -> dict[str, str]:
        out = {}
        for part in self.headers.get("Cookie", "").split(";"):
            if "=" in part:
                key, value = part.strip().split("=", 1)
                out[key] = value
        return out

    def _session_user(self) -> dict | None:
        sid = self._cookies().get("ddos_session", "")
        session = self.ctx.sessions.get(sid) if sid else None
        if not session:
            return None
        uid, expires = session
        if expires <= time.time():
            self.ctx.sessions.pop(sid, None)
            return None
        user = self.ctx.users.user(uid)
        if not user or not user.get("active"):
            self.ctx.sessions.pop(sid, None)
            return None
        self.ctx.sessions[sid] = (uid, time.time() + 12 * 3600)
        return user

    def _permission(self, method: str, path: str) -> str:
        if path in ("/api/today", "/api/schedule"):
            return "schedule.view"
        if path == "/api/users":
            return "users.manage"
        if path == "/api/settings":
            return "settings.manage"
        if path == "/api/classic":
            return "billing.view"
        if path in ("/api/patients", "/api/chart", "/api/snapshot"):
            return "patients.view"
        if path in ("/api/notes", "/api/note", "/api/doc", "/api/report", "/api/drafts", "/api/print-log", "/api/printers"):
            return "notes.view"
        if path == "/api/superbill" or path == "/api/superbills" or path.startswith("/api/superbill/"):
            return "notes.view"
        if method == "POST" and path.startswith("/api/"):
            return "notes.create"
        return ""

    def _forbidden_permission(self, method: str, path: str) -> bool:
        permission = self._permission(method, path)
        if permission and not self.ctx.users.allowed(getattr(self, "current_user", None), permission):
            self._json({"error": f"Your account does not have {permission} permission."}, 403)
            return True
        return False

    # ---- routes
    def do_GET(self):
        path = urlparse(self.path).path
        q = self._query()
        if not self._authed():
            return self._send(HTTPStatus.FORBIDDEN, b"forbidden", "text/plain")
        if path == "/api/auth/status":
            user = self._session_user()
            return self._json({"authenticated": bool(user), "user": user})
        if path.startswith("/api/"):
            self.current_user = self._session_user()
            if not self.current_user:
                return self._json({"error": "Sign in to DizzyDoctor."}, 401)
            if self._forbidden_permission("GET", path):
                return None
        try:
            return self._get(path, q)
        except Exception as ex:  # noqa: BLE001 - shown on screen
            return self._json({"error": f"{type(ex).__name__}: {ex}"}, 500)

    def _get(self, path: str, q: dict):
        if path == "/":
            with open(os.path.join(WEB_DIR, "index.html"), "rb") as f:
                return self._send(200, f.read(), MIME[".html"], {"Set-Cookie": f"ddos_token={self.ctx.token}; Path=/; SameSite=Strict"})
        if path.startswith("/static/"):
            fn = os.path.normpath(os.path.join(WEB_DIR, path[len("/static/"):]))
            if not fn.startswith(WEB_DIR) or not os.path.isfile(fn):
                return self._send(404, b"not found", "text/plain")
            with open(fn, "rb") as f:
                return self._send(200, f.read(), MIME.get(os.path.splitext(fn)[1], mimetypes.guess_type(fn)[0] or "application/octet-stream"))
        if path == "/api/schema":
            return self._json({**note.SCHEMA, "visit_types": visits.VISIT_TYPES, "visit_sources": visits.SOURCES})
        if path == "/api/phrases":
            return self._json({k: v for k, v in vars(phrases).items() if k.isupper()})
        if path == "/api/status":
            return self._json({**self.ctx.status(), "user": self.current_user})
        if path == "/api/users":
            return self._json(self.ctx.users.list_state())
        if path == "/api/patients":
            rows = _quiet(lambda: self.ctx.find_patient(q.get("q", "")) if self.ctx.find_patient else [], [])
            return self._json((rows or [])[:40])
        if path == "/api/notes":
            files = _quiet(lambda: self.ctx.patient_notes(q) if self.ctx.patient_notes else [], [])
            return self._json([{"path": n.path, "dos": n.dos.isoformat() if n.dos else "", "kind": n.kind, "name": os.path.basename(n.path)} for n in files or []])
        if path == "/api/today":
            raw_day = q.get("date", "")
            day = note.parse_date(raw_day) if raw_day else None
            if day is None and raw_day:
                try:
                    day = date.fromisoformat(raw_day)
                except ValueError:
                    day = None
            day = day or date.today()
            return self._json({"date": day.isoformat(), "rows": _quiet(lambda: self._provider("today", day), []) or []})
        if path == "/api/schedule":
            def calendar_date(value: str):
                try:
                    return note.parse_date(value) or date.fromisoformat(value)
                except (TypeError, ValueError):
                    return None
            start = calendar_date(q.get("start", "")) or date.today()
            end = calendar_date(q.get("end", ""))
            end = end or start
            if end <= start:
                from datetime import timedelta
                end = start + timedelta(days=1)
            if (end - start).days > 42:
                return self._json({"error": "Calendar ranges are limited to 42 days."}, 400)
            data = _quiet(lambda: self._provider("schedule", start, end), None)
            return self._json(data or {"start": start.isoformat(), "end": end.isoformat(), "providers": [], "rows": []})
        if path == "/api/chart":
            chart = _quiet(lambda: self._provider("chart", q), None) or {"patient": q, "visits": [], "documents": [], "notes": [], "facts": {}}
            self.ctx.remember(chart.get("patient") or q)
            return self._json(chart)
        if path == "/api/snapshot":
            return self._json(_quiet(lambda: self._provider("snapshot", q), {}) or {})
        if path == "/api/note":
            p = q.get("path", "")
            if p and os.path.isfile(p):
                real = os.path.normcase(os.path.realpath(p))
                roots = [os.path.normcase(os.path.realpath(root)) for root in self.ctx.cfg.get("notes_folders") or [] if root]
                read_only = any(real == root or real.startswith(root + os.sep) for root in roots)
                values = note.read(p)
                values.update(distributions.read_sidecar(p))     # map marks kept beside the PDF (no box on the form)
                return self._json({"values": values, "path": p, "read_only": read_only})
            return self._json({"values": note.blank(), "path": ""})
        if path == "/api/doc":
            p = _allowed_file(self.ctx, q.get("path", ""))
            if not p:
                return self._json({"error": "That file isn't reachable from this computer."}, 404)
            ext = os.path.splitext(p)[1].lower()
            if q.get("info"):
                if ext == ".pdf":
                    import pymupdf
                    with pymupdf.open(p) as doc:
                        return self._json({"pages": len(doc), "kind": "pdf", "name": os.path.basename(p)})
                if ext in TEXT_DOCS:
                    with open(p, encoding="utf-8", errors="replace") as f:
                        return self._json({"pages": 1, "kind": "text", "name": os.path.basename(p), "text": f.read(200_000)})
                return self._json({"pages": 0, "kind": "other", "name": os.path.basename(p)})
            if ext == ".pdf":
                return self._send(200, note.render_page(p, int(q.get("page", "0")), float(q.get("zoom", "1.5"))), "image/png")
            if ext in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
                with open(p, "rb") as f:
                    return self._send(200, f.read(), mimetypes.guess_type(p)[0] or "application/octet-stream")
            return self._json({"error": "No preview for this file type."}, 415)
        if path == "/api/report":             # a report's bottom line (Impression / Interpretation), for the study rows
            p = _allowed_file(self.ctx, q.get("path", ""))
            if not p:
                return self._json({"error": "That file isn't reachable from this computer."}, 404)
            from .providers import report_result
            return self._json({"name": os.path.basename(p), "result": _quiet(lambda: report_result(p), "") or ""})
        if path == "/api/preview":
            pdf = self.ctx.previews.get(q.get("id", ""))
            if not pdf or not os.path.isfile(pdf):
                return self._send(404, b"", "text/plain")
            return self._send(200, note.render_page(pdf, int(q.get("page", "0")), float(q.get("zoom", "1.5"))), "image/png")
        if path == "/api/printers":
            from .. import printer as pr_mod
            return self._json(pr_mod.get_printers())
        if path == "/api/print-log":
            log_path = os.path.join(self.ctx.data_dir, "print-log.jsonl")
            events = []
            with self.ctx.print_lock:
                if os.path.isfile(log_path):
                    with open(log_path, encoding="utf-8", errors="replace") as f:
                        for line in f:
                            try:
                                row = json.loads(line)
                            except ValueError:
                                continue
                            if isinstance(row, dict):
                                events.append(row)
            # One row per print attempt, with the provider's final confirmation replacing "requested".
            folded = {}
            for row in events:
                folded[str(row.get("attempt") or len(folded))] = row
            want_name = str(q.get("name") or "").strip().casefold()
            want_dos = str(q.get("dos") or "").strip()
            rows = [r for r in folded.values() if (not want_name or str(r.get("name") or "").strip().casefold() == want_name)
                    and (not want_dos or str(r.get("dos") or "").strip() == want_dos)]
            rows.sort(key=lambda r: str(r.get("at") or ""), reverse=True)
            return self._json(rows[:100])
        if path == "/api/drafts":
            folder = self.ctx.cfg.get("prepped_notes_folder") or os.path.join(self.ctx.data_dir, "Prepped Notes")
            out = []
            if os.path.isdir(folder):
                for fn in sorted(os.listdir(folder)):
                    if fn.lower().endswith(".pdf"):
                        full = os.path.join(folder, fn)
                        out.append({"path": full, "name": fn, "modified": datetime.fromtimestamp(os.path.getmtime(full)).strftime("%m/%d/%Y %I:%M %p")})
            return self._json(sorted(out, key=lambda d: d["modified"], reverse=True))
        if path == "/api/superbill/icd":
            return self._json(superbill.search_icd(q.get("q", "")))
        if path == "/api/superbill/cpt":
            return self._json(superbill.search_cpt(q.get("q", "")))
        if path == "/api/superbill/schema":
            return self._json({"blank": superbill.blank(), "icd_count": len(superbill.icd_database()),
                               "cpt_count": len(superbill.cpt_database())})
        if path == "/api/superbill":
            p = q.get("path", "")
            if p and os.path.isfile(p):
                return self._json({"values": superbill.read(p), "path": p})
            if p and os.path.isfile(p + ".json"):
                return self._json({"values": superbill.read(p + ".json"), "path": p})
            return self._json({"values": superbill.blank(), "path": ""})
        if path == "/api/superbills":
            files = _quiet(lambda: superbill.list_for(self.ctx.cfg, self.ctx.data_dir, q.get("last", ""), q.get("first", "")), [])
            return self._json(files or [])
        if path == "/api/superbill/preview":
            pdf = self.ctx.previews.get(q.get("id", ""))
            if not pdf or not os.path.isfile(pdf):
                return self._send(404, b"", "text/plain")
            return self._send(200, note.render_page(pdf, int(q.get("page", "0")), float(q.get("zoom", "1.5"))), "image/png")
        return self._send(404, b"not found", "text/plain")

    def do_POST(self):
        path = urlparse(self.path).path
        if not self._authed():
            return self._send(HTTPStatus.FORBIDDEN, b"forbidden", "text/plain")
        if path == "/api/auth/login":
            try:
                data = json.loads(self._body() or b"{}")
            except ValueError:
                data = {}
            user = self.ctx.users.authenticate(str(data.get("username") or ""), str(data.get("password") or ""))
            if not user:
                time.sleep(.25)
                return self._json({"error": "Incorrect username or password."}, 401)
            sid = secrets.token_urlsafe(32)
            self.ctx.sessions[sid] = (int(user["id"]), time.time() + 12 * 3600)
            return self._json({"ok": True, "user": user}, extra={"Set-Cookie": f"ddos_session={sid}; Path=/; HttpOnly; SameSite=Strict"})
        if path == "/api/auth/logout":
            self.ctx.sessions.pop(self._cookies().get("ddos_session", ""), None)
            return self._json({"ok": True}, extra={"Set-Cookie": "ddos_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"})
        self.current_user = self._session_user()
        if not self.current_user:
            return self._json({"error": "Sign in to DizzyDoctor."}, 401)
        if self._forbidden_permission("POST", path):
            return None
        try:
            return self._post(path)
        except Exception as ex:  # noqa: BLE001
            return self._json({"error": f"{type(ex).__name__}: {ex}"}, 500)

    def _post(self, path: str):
        if path == "/api/dictate":
            return self._dictate()
        if path == "/api/visit/part":
            return self._visit_part()
        data = json.loads(self._body() or b"{}")
        if path == "/api/snapshot":           # the SnapShot of the note open on screen (nothing is written)
            from .providers import snapshot_from_values
            return self._json(snapshot_from_values(data.get("values", {}) or {}))
        if path == "/api/coding":
            return self._json(coding.code_note(data.get("values", {}), data.get("payer", "")).as_dict())
        if path == "/api/superbill/from-note":
            values = superbill.from_note(data.get("values", {}), data.get("payer", ""))
            patient = data.get("patient") or {}
            if patient.get("last") or patient.get("first") or patient.get("id"):
                chart = _quiet(lambda: self._provider("chart", patient), None) or {}
                values = superbill.attach_patient(values, chart)
            return self._json({"values": values, "missing": superbill.validate(values)})
        if path == "/api/superbill/attach":
            chart = _quiet(lambda: self._provider("chart", data.get("patient") or {}), None) or {}
            return self._json({"values": superbill.attach_patient(data.get("values", {}), chart)})
        if path == "/api/superbill/preview":
            pid = secrets.token_hex(8)
            out = os.path.join(tempfile.gettempdir(), f"ddos-superbill-{pid}.pdf")
            superbill.fill(data.get("values", {}), out)
            import pymupdf
            with pymupdf.open(out) as doc:
                pages = len(doc)
            self.ctx.previews[pid] = out
            return self._json({"id": pid, "pages": pages, "missing": superbill.validate(data.get("values", {}))})
        if path == "/api/superbill/save":
            values = data.get("values", {})
            last, first = note.split_name(values.get("Name", ""))
            dos = note.parse_date(values.get("Date", "")) or date.today()
            if not last:
                return self._json({"error": "The superbill needs the patient's name before it can be filed."}, 400)
            if data.get("kind") == "final":
                if not self.ctx.users.allowed(self.current_user, "notes.file"):
                    return self._json({"error": "Your account cannot file final notes."}, 403)
                missing = superbill.validate(values)
                if missing:
                    return self._json({"error": "The superbill still needs " + ", ".join(missing) + "."}, 400)
                out = superbill.final_path(self.ctx.cfg, last, first, dos)
            else:
                out = superbill.draft_path(self.ctx.cfg, self.ctx.data_dir, last, first, dos)
            try:
                superbill.fill(values, out)
            except OSError as ex:
                return self._json({"error": f"Couldn't write {out}: {ex}"}, 500)
            return self._json({"path": out, "name": os.path.basename(out), "saved": datetime.now().strftime("%I:%M %p")})
        if path == "/api/superbill/batch":
            dos = note.parse_date(str(data.get("date") or ""))
            if not dos:
                try:
                    dos = date.fromisoformat(str(data.get("date") or ""))
                except ValueError:
                    dos = None
            if not dos:
                return self._json({"error": "Choose a date for the superbill batch."}, 400)
            schedule = _quiet(lambda: self._provider("schedule", dos, dos + timedelta(days=1)), None) or {"rows": []}
            chosen = {str(x) for x in (data.get("providers") or []) if str(x)}
            appointments = [r for r in schedule.get("rows", []) if r.get("patient") and (not chosen or str(r.get("cal") or "") in chosen)]
            # Outlook copies can put the same appointment on more than one calendar. Keep the first selected copy so
            # one patient never silently becomes two superbills.
            unique, seen = [], set()
            for appt in appointments:
                identity = (str(appt.get("acct") or "").casefold() or (str(appt.get("last") or "") + "|" + str(appt.get("first") or "")).casefold(),
                            str(appt.get("start") or appt.get("time") or "")[:16])
                if identity in seen:
                    continue
                seen.add(identity); unique.append(appt)
            appointments = unique
            uploads = [r for r in (data.get("rows") or []) if isinstance(r, dict)][:5000]
            used_uploads: set[int] = set()

            def norm_row(row):
                return {re.sub(r"[^a-z0-9]+", "_", str(k).strip().lower()).strip("_"): str(v or "").strip() for k, v in row.items()}
            normalized = [norm_row(r) for r in uploads]
            def pick(row, *keys):
                return next((row[k] for k in keys if row.get(k)), "")
            def row_name(row):
                raw = pick(row, "name", "patient_name")
                if raw:
                    last, first = note.split_name(raw)
                else:
                    last, first = pick(row, "last", "last_name", "surname"), pick(row, "first", "first_name", "given_name")
                return last.casefold(), first.casefold()
            def matching_upload(appt):
                best = (0, None)
                alast, afirst = str(appt.get("last") or "").casefold(), str(appt.get("first") or "").casefold()
                for i, row in enumerate(normalized):
                    if i in used_uploads:
                        continue
                    raw_date = pick(row, "date", "dos", "date_of_service", "service_date")
                    rd = note.parse_date(raw_date) if raw_date else None
                    if rd and rd != dos:
                        continue
                    score = 0
                    acct = pick(row, "acct", "account", "account_number", "patient_id", "mrn")
                    last, first = row_name(row)
                    dob = pick(row, "dob", "date_of_birth", "birth_date")
                    if acct and str(appt.get("acct") or "").casefold() == acct.casefold(): score = 100
                    elif last and last == alast and dob and note.parse_date(dob) == note.parse_date(str(appt.get("dob") or "")): score = 85
                    elif last and last == alast and first and (first == afirst or first[:1] == afirst[:1]): score = 70
                    provider = pick(row, "provider", "rendering", "rendering_provider").casefold()
                    if score and provider and provider not in str(appt.get("provider") or appt.get("cal") or "").casefold(): score -= 20
                    if score > best[0]: best = (score, i)
                if best[1] is not None:
                    used_uploads.add(best[1])
                    return uploads[best[1]]
                return None

            groups: dict[str, list[dict]] = {}
            draft_root = superbill.drafts_root(self.ctx.cfg, self.ctx.data_dir)
            for appt in appointments:
                patient = {"id": appt.get("patient_id") or "", "last": appt.get("last") or "", "first": appt.get("first") or "",
                           "dob": appt.get("dob") or "", "acct": appt.get("acct") or ""}
                provider_name = str(appt.get("provider") or appt.get("cal") or "Unassigned")
                values = superbill.blank(); values["Date"] = dos.strftime("%m/%d/%Y"); values["rendering"] = provider_name
                prior = _quiet(lambda: superbill.list_for(self.ctx.cfg, self.ctx.data_dir, patient["last"], patient["first"]), []) or []
                existing = next((s for s in prior if s.get("dos") == dos.isoformat()), None)
                if existing:
                    values = superbill.read(existing.get("path", ""))
                else:
                    note_files = _quiet(lambda: self._provider("patient_notes", patient), []) or []
                    same_day = next((n for n in note_files if getattr(n, "dos", None) == dos), None)
                    if same_day:
                        values = superbill.from_note(note.read(same_day.path))
                chart = _quiet(lambda: self._provider("chart", patient), None) or {"patient": patient}
                values = superbill.attach_patient(values, chart)
                values["Date"] = values.get("Date") or dos.strftime("%m/%d/%Y")
                values["rendering"] = values.get("rendering") or provider_name
                upload = matching_upload(appt)
                values = superbill.imported(values, upload)
                last, first = note.split_name(values.get("Name", ""))
                safe_provider = re.sub(r"[^A-Za-z0-9._ -]", "", provider_name).strip() or "Unassigned"
                folder = os.path.join(draft_root, dos.isoformat(), safe_provider)
                out = os.path.join(folder, superbill.filename(last or "Unknown", first, dos))
                superbill.fill(values, out)
                groups.setdefault(provider_name, []).append({"path": out, "name": os.path.basename(out), "patient": values.get("Name", ""),
                                                              "missing": superbill.validate(values), "matched_upload": bool(upload)})
            batch_rows = []
            for provider_name, items in groups.items():
                safe_provider = re.sub(r"[^A-Za-z0-9._ -]", "", provider_name).strip() or "Unassigned"
                folder = os.path.join(draft_root, "Batches", dos.isoformat())
                batch_path = os.path.join(folder, f"{safe_provider} {dos:%m.%d.%y}.pdf")
                superbill.combine([x["path"] for x in items], batch_path)
                with open(batch_path + ".json", "w", encoding="utf-8") as f:
                    json.dump({"date": dos.isoformat(), "provider": provider_name, "items": items, "created": datetime.now().isoformat(timespec="seconds")}, f, indent=1)
                batch_rows.append({"provider": provider_name, "path": batch_path, "name": os.path.basename(batch_path), "count": len(items),
                                   "ready": sum(not x["missing"] for x in items), "items": items})
            unmatched = [uploads[i] for i in range(len(uploads)) if i not in used_uploads]
            return self._json({"date": dos.isoformat(), "appointments": len(appointments), "matched": len(used_uploads),
                               "batches": batch_rows, "unmatched": unmatched})
        if path == "/api/users":
            try:
                action = str(data.get("action") or "")
                if action == "create_user":
                    result = self.ctx.users.create_user(str(data.get("username") or ""), str(data.get("display_name") or ""),
                                                        str(data.get("password") or ""), list(data.get("groups") or []),
                                                        bool(data.get("active", True)), bool(data.get("must_change", True)))
                elif action == "update_user":
                    fields = {k: data[k] for k in ("display_name", "active", "groups", "password", "must_change") if k in data}
                    result = self.ctx.users.update_user(int(data.get("id") or 0), **fields)
                elif action == "create_group":
                    result = self.ctx.users.create_group(str(data.get("name") or ""), str(data.get("description") or ""), list(data.get("permissions") or []))
                elif action == "update_group":
                    result = self.ctx.users.update_group(int(data.get("id") or 0), str(data.get("description") or ""), list(data.get("permissions") or []))
                else:
                    return self._json({"error": "Unknown user-administration action."}, 400)
            except (TypeError, ValueError) as ex:
                return self._json({"error": str(ex)}, 400)
            return self._json({"ok": True, "result": result, **self.ctx.users.list_state()})
        if path == "/api/visit/finish":
            transcript = scribe.finish(str(data.get("id") or ""))
            return self._json({"transcript": transcript, "reading": scribe.visit_reading(transcript, data.get("values") or {})})
        if path == "/api/read":
            # typed or pasted text: the provider's notes go into the story (Build reads them); a visit transcript is read here
            text = str(data.get("text") or "")
            kind = data.get("kind") or "auto"
            if kind == "auto":
                kind = "visit" if story.looks_like_visit(text) else "notes"
            out = {"kind": kind}
            if kind == "visit":
                out["reading"] = scribe.visit_reading(text, data.get("values") or {})
            return self._json(out)
        if path == "/api/scope/import":
            try:
                return self._json(scope_import.import_note(str(data.get("text") or ""), data.get("values") or {}))
            except ValueError as ex:
                return self._json({"error": str(ex)}, 400)
        if path == "/api/compose":
            return self._json(compose.compose_note(data.get("values", {}), data.get("previous") or {}, data.get("last_seen", "")).as_dict())
        if path == "/api/interpret":
            return self._json(dictation.interpret(data.get("text", ""), data.get("section", "hpi"), data.get("focus", "")).as_dict())
        if path == "/api/new-from":
            prev = note.read(data["path"]) if data.get("path") and os.path.isfile(data["path"]) else {}
            dos = note.parse_date(data.get("dos", "")) or date.today()
            values = note.carry_forward(prev, dos) if prev else note.blank()
            if not prev:
                values["Date"] = dos.strftime("%m/%d/%Y")
            for k in ("Name", "DOB"):
                if data.get(k):
                    values[k] = data[k]
            dob = note.parse_date(values.get("DOB", ""))
            if dob:
                values["HPI-age"] = str(note.age_on(dob, dos))
            prep = None
            if prev:
                try:
                    from .providers import prep_reference
                    prep = prep_reference(prev, data.get("path", ""))
                except Exception:
                    log.exception("prep reference failed")
                    prep = None
            return self._json({"values": values, "prep": prep})
        if path == "/api/preview":
            pid = secrets.token_hex(8)
            out = os.path.join(tempfile.gettempdir(), f"ddos-preview-{pid}.pdf")
            note.fill(self._with_practice(data.get("values", {})), out, note.template_path(self.ctx.cfg))
            self.ctx.previews[pid] = out
            return self._json({"id": pid, "pages": note.SCHEMA["pages"]})
        if path == "/api/print" or path == "/api/superbill/print":
            values = data.get("values", {})
            printer_target = str(data.get("printer") or "").strip()
            copies = max(1, int(data.get("copies") or 1))
            system_dialog = bool(data.get("system_dialog"))
            doc_type = str(data.get("document") or ("superbill" if "superbill" in path else "progress note")).strip()
            pid = secrets.token_hex(8)
            out = os.path.join(tempfile.gettempdir(), f"ddos-print-{pid}.pdf")
            try:
                if doc_type.lower() == "superbill":
                    superbill.fill(values, out)
                else:
                    note.fill(self._with_practice(values), out, note.template_path(self.ctx.cfg))
            except Exception as ex:
                log.exception("could not fill %s for printing", doc_type)
                return self._json({"error": f"Could not prepare {doc_type} for printing: {ex}"}, 500)

            from .. import printer as pr_mod
            doc_title = f"DizzyDoctor Superbill - {values.get('Name', 'Patient')}" if doc_type.lower() == "superbill" else f"DizzyDoctor - {values.get('Name', 'Note')}"
            ok, msg = pr_mod.print_file(out, printer=printer_target, copies=copies,
                                        title=doc_title,
                                        system_dialog=system_dialog)
            if not ok:
                return self._json({"error": msg or "Print command failed."}, 500)

            attempt = str(data.get("attempt") or pid)
            printer_name = printer_target or ("System print dialog" if system_dialog else "Default printer")
            row = {"attempt": attempt, "status": "printed",
                   "name": str(values.get("Name") or "")[:200], "dos": str(values.get("Date") or "")[:40],
                   "document": doc_type,
                   "printer": printer_name,
                   "user": str((self.current_user or {}).get("display_name") or (self.current_user or {}).get("username") or "")[:120],
                   "at": datetime.now().isoformat(timespec="seconds"), "when": datetime.now().strftime("%m/%d/%Y %I:%M %p")}
            log_path = os.path.join(self.ctx.data_dir, "print-log.jsonl")
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            with self.ctx.print_lock:
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
            return self._json({"ok": True, "entry": row, "printer": printer_name, "message": msg})
        if path == "/api/print-log":
            status = str(data.get("status") or "")
            if status not in ("requested", "printed", "not_printed"):
                return self._json({"error": "Unknown print result."}, 400)
            row = {"attempt": str(data.get("attempt") or secrets.token_hex(8)), "status": status,
                   "name": str(data.get("name") or "")[:200], "dos": str(data.get("dos") or "")[:40],
                   "document": str(data.get("document") or "progress note")[:80],
                   "printer": str(data.get("printer") or "")[:120],
                   "user": str((self.current_user or {}).get("display_name") or (self.current_user or {}).get("username") or "")[:120],
                   "at": datetime.now().isoformat(timespec="seconds"), "when": datetime.now().strftime("%m/%d/%Y %I:%M %p")}
            log_path = os.path.join(self.ctx.data_dir, "print-log.jsonl")
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            with self.ctx.print_lock:
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
            return self._json({"ok": True, "entry": row})
        if path == "/api/save":
            values = data.get("values", {})
            last, first = note.split_name(values.get("Name", ""))
            dos = note.parse_date(values.get("Date", "")) or date.today()
            if not last:
                return self._json({"error": "The note needs the patient's name before it can be filed."}, 400)
            if data.get("kind") == "final":
                if not self.ctx.users.allowed(self.current_user, "notes.file"):
                    return self._json({"error": "Your account cannot file final notes."}, 403)
                out = note.final_path(self.ctx.cfg, last, first, dos)
            else:
                out = note.draft_path(self.ctx.cfg, self.ctx.data_dir, last, first, dos)
            try:
                note.fill(self._with_practice(values), out, note.template_path(self.ctx.cfg))
            except OSError as ex:
                return self._json({"error": f"Couldn't write {out}: {ex}"}, 500)
            distributions.save_sidecar(out, values)       # the body-map marks have no box on the form
            return self._json({"path": out, "name": os.path.basename(out), "saved": datetime.now().strftime("%I:%M %p")})
        if path == "/api/open":
            p = _allowed_file(self.ctx, data.get("path", ""))
            if not p:
                return self._json({"error": "That file isn't reachable from this computer."}, 404)
            from .. import shared
            shared.open_file(p)
            return self._json({"ok": True})
        if path == "/api/classic":
            if not self.ctx.launch_classic:
                return self._json({"error": "The office window isn't available here."}, 404)
            tab = str(data.get("tab") or "")
            self.ctx.launch_classic(tab if tab in OFFICE_TABS else "")
            return self._json({"ok": True})
        if path == "/api/settings":
            from .. import config
            allowed = {"notes_folders", "prepped_notes_folder", "note_template", "dictation_model", "initials", "demo_mode", "visit_timer", "superbill_draft_folder", "practice_directory"}
            changes = {k: v for k, v in data.items() if k in allowed}
            self.ctx.cfg.update(changes)
            try:
                config.save(self.ctx.cfg)
            except OSError as ex:
                return self._json({"error": f"Couldn't save settings: {ex}"}, 500)
            return self._json({"ok": True, "settings": {k: self.ctx.cfg.get(k) for k in allowed}})
        return self._send(404, b"not found", "text/plain")

    def _with_practice(self, values: dict) -> dict:
        """The practice's own left-margin directory, when Settings has one; otherwise the form prints its own."""
        text = str(self.ctx.cfg.get("practice_directory") or "").strip()
        return {**values, "directory": text} if text else {**values, "directory": ""}

    def _visit_part(self):
        """30 seconds of a visit recording: transcribed here in the background, then deleted."""
        if not dictation.available():
            return self._json({"error": "Dictation isn't installed on this computer (faster-whisper)."}, 503)
        q = self._query()
        visit_id = re.sub(r"[^A-Za-z0-9-]", "", q.get("id", ""))[:40]
        if not visit_id:
            return self._json({"error": "No visit id."}, 400)
        fd, tmp = tempfile.mkstemp(prefix="ddos-visit-", suffix=".wav")
        with os.fdopen(fd, "wb") as f:
            f.write(self._body())
        scribe.add_part(visit_id, int(q.get("seq", "0") or 0), tmp, self.ctx.cfg.get("dictation_model", "small.en"), self.ctx.data_dir)
        return self._json({"ok": True, **scribe.progress(visit_id)})

    def _dictate(self):
        if not dictation.available():
            return self._json({"error": "Dictation isn't installed on this computer (faster-whisper)."}, 503)
        ext = ".wav" if "wav" in (self.headers.get("Content-Type") or "") else ".webm"
        fd, tmp = tempfile.mkstemp(prefix="ddos-dictation-", suffix=ext)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(self._body())
            text = dictation.transcribe(tmp, self.ctx.cfg.get("dictation_model", "small.en"), self.ctx.data_dir)
        except Exception as ex:  # noqa: BLE001 - shown on screen
            return self._json({"error": f"Transcription failed: {ex}"}, 500)
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
        q = self._query()
        interp = dictation.interpret(text, q.get("section", "hpi"), q.get("focus", ""))
        return self._json({"text": text, "interpretation": interp.as_dict()})


class _Server(ThreadingHTTPServer):
    """A page load asks for many files at once; a longer queue keeps the extra connections waiting instead of reset."""
    request_queue_size = 64
    daemon_threads = True


class ChartServer:
    def __init__(self, ctx: ChartContext, port: int = 0):
        self.ctx = ctx
        handler = type("BoundHandler", (Handler,), {"ctx": ctx})
        self.httpd = _Server(("127.0.0.1", port), handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True, name="chart-server")

    def start(self) -> "ChartServer":
        self.thread.start()
        return self

    def url(self, **params) -> str:
        from urllib.parse import urlencode
        params = {"t": self.ctx.token, **{k: v for k, v in params.items() if v}}
        return f"http://127.0.0.1:{self.port}/?{urlencode(params)}"

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        if self.thread.is_alive() and threading.current_thread() is not self.thread:
            self.thread.join(timeout=2)


_server: ChartServer | None = None


def get_server(ctx: ChartContext) -> ChartServer:
    global _server
    if _server is None:
        _server = ChartServer(ctx).start()
    return _server


def _browser_app_command(url: str) -> list[str] | None:
    """Chrome or Edge in app mode: a plain window with no tabs or address bar."""
    candidates = []
    if sys.platform == "darwin":
        candidates = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                      "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"]
    elif sys.platform.startswith("win"):
        for base in (os.environ.get("ProgramFiles", ""), os.environ.get("ProgramFiles(x86)", ""), os.environ.get("LocalAppData", "")):
            if base:
                candidates += [os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"),
                               os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")]
    else:
        candidates = [shutil.which(n) or "" for n in ("google-chrome", "chromium", "microsoft-edge")]
    for exe in candidates:
        if exe and os.path.isfile(exe):
            return [exe, f"--app={url}", "--window-size=1480,940", "--disable-features=TranslateUI"]
    return None


def open_window(url: str) -> None:
    cmd = _browser_app_command(url)
    if cmd:
        subprocess.Popen(cmd, close_fds=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    else:
        webbrowser.open(url)


def launch(ctx: ChartContext, **params) -> str:
    server = get_server(ctx)
    url = server.url(**params)
    open_window(url)
    return url
