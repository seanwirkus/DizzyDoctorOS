"""Local SQLite index of file names on the clinic drive(s), plus request tracking.

The drive index stores only paths, sizes and modified times. The one exception is scanned
superbill batches: when a superbill is looked up, the batch's OCR text is cached here so it is
only read once. The database lives in the user's profile (%LOCALAPPDATA%\\DizzyDoctor)
and never leaves the PC.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from datetime import datetime

SKIP_DIRS = {"$recycle.bin", "system volume information", "windows", "program files",
             "program files (x86)", "programdata", "appdata", "node_modules", ".git", "__pycache__",
             ".synologyworkingdirectory", "#recycle", "@eadir"}
SKIP_FILES = {"thumbs.db", "desktop.ini", ".ds_store"}


class Store:
    def __init__(self, db_path: str):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.path = db_path
        self._lock = threading.Lock()
        self._local = threading.local()
        self._readers: list[sqlite3.Connection] = []
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY, name TEXT, folder TEXT, size INTEGER, mtime REAL);
            CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
            CREATE TABLE IF NOT EXISTS items(
                key TEXT PRIMARY KEY, first_seen TEXT, status TEXT DEFAULT 'Open', note TEXT DEFAULT '',
                data TEXT);
            CREATE TABLE IF NOT EXISTS ocr(
                path TEXT, page INTEGER, size INTEGER, mtime REAL, lines TEXT, PRIMARY KEY(path, page));
            CREATE TABLE IF NOT EXISTS amendments(
                id INTEGER PRIMARY KEY, created TEXT, item_key TEXT, account TEXT, patient TEXT, dos TEXT,
                batch TEXT, page INTEGER, provider TEXT, attending TEXT, files TEXT, initials TEXT);
            CREATE TABLE IF NOT EXISTS ai_cache(msg_id TEXT PRIMARY KEY, data TEXT);
        """)

    def close(self):
        for c in list(self._readers):
            try:
                c.close()
            except Exception:
                pass
        self.db.close()

    def reader(self) -> sqlite3.Connection:
        """This thread's own read connection. WAL lets it read while another thread writes, so a long search
        never holds the shared lock (holding it is what froze the window during searches)."""
        c = getattr(self._local, "db", None)
        if c is None:
            c = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
            c.execute("PRAGMA query_only=1")
            self._local.db = c
            self._readers.append(c)
        return c

    # ------------------------------------------------------------------ file index
    def rebuild_index(self, roots: list[str], extra_skip: list[str], progress=None, cancel: threading.Event | None = None) -> int:
        skip = SKIP_DIRS | {s.lower() for s in extra_skip}
        batch, count, last_report = [], 0, 0.0
        with self._lock:
            self.db.execute("DROP TABLE IF EXISTS files_new")
            self.db.execute("CREATE TABLE files_new(path TEXT PRIMARY KEY, name TEXT, folder TEXT, size INTEGER, mtime REAL)")
        stack = [r for r in roots if r and os.path.isdir(r)]
        while stack:
            if cancel is not None and cancel.is_set():
                return -1
            folder = stack.pop()
            try:
                with os.scandir(folder) as it:
                    for e in it:
                        try:
                            if e.is_dir(follow_symlinks=False):
                                if e.name.lower() not in skip and not e.name.startswith("~$"):
                                    stack.append(e.path)
                            elif e.name.lower() not in SKIP_FILES and not e.name.startswith("~$"):
                                st = e.stat(follow_symlinks=False)
                                batch.append((e.path, e.name, folder, st.st_size, st.st_mtime))
                        except OSError:
                            continue
            except OSError:
                continue  # permission denied, offline share, etc.
            if len(batch) >= 2000:
                count += self._flush(batch)
            if progress and time.monotonic() - last_report > 0.25:
                last_report = time.monotonic()
                progress(count + len(batch), folder)
        count += self._flush(batch)
        with self._lock:
            self.db.execute("DROP TABLE IF EXISTS files")
            self.db.execute("ALTER TABLE files_new RENAME TO files")
            self.db.execute("INSERT OR REPLACE INTO meta VALUES('indexed_at', ?)", (datetime.now().isoformat(timespec="seconds"),))
            self.db.execute("INSERT OR REPLACE INTO meta VALUES('indexed_roots', ?)", (json.dumps(roots),))
            self.db.commit()
        return count

    def _flush(self, batch: list) -> int:
        n = len(batch)
        if n:
            with self._lock:
                self.db.executemany("INSERT OR REPLACE INTO files_new VALUES(?,?,?,?,?)", batch)
                self.db.commit()
            batch.clear()
        return n

    def meta_get(self, k: str) -> str:
        with self._lock:
            row = self.db.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return row[0] if row else ""

    def meta_set(self, k: str, v: str) -> None:
        with self._lock:
            self.db.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (k, v))
            self.db.commit()

    def index_info(self) -> tuple[int, datetime | None, list[str]]:
        with self._lock:
            n = self.db.execute("SELECT COUNT(*) FROM files").fetchone()[0]
            row = self.db.execute("SELECT v FROM meta WHERE k='indexed_at'").fetchone()
            roots = self.db.execute("SELECT v FROM meta WHERE k='indexed_roots'").fetchone()
        return n, datetime.fromisoformat(row[0]) if row else None, json.loads(roots[0]) if roots else []

    def find_files(self, terms: list[str], limit: int = 5000) -> list[tuple]:
        """Rows whose full path contains any of the terms (case-insensitive)."""
        terms = [t for t in dict.fromkeys(terms) if t and len(t) >= 2]
        if not terms:
            return []
        where = " OR ".join("path LIKE ? ESCAPE '\\'" for _ in terms)
        args = ["%" + t.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%" for t in terms]
        sql = f"SELECT path, name, folder, size, mtime FROM files WHERE {where} LIMIT {int(limit)}"
        try:
            return self.reader().execute(sql, args).fetchall()
        except sqlite3.OperationalError:  # the table was swapped by a re-scan mid-query: once more
            return self.reader().execute(sql, args).fetchall()

    # ------------------------------------------------------------------ request tracking
    def upsert_item(self, key: str, data: dict) -> tuple[str, str]:
        """Insert a new request (status Open) or refresh an existing one. Returns (status, note)."""
        with self._lock:
            row = self.db.execute("SELECT status, note, data FROM items WHERE key=?", (key,)).fetchone()
            if row:
                old = json.loads(row[2])
                if old.get("answer") and not data.get("answer"):
                    data["answer"], data["answered_by"] = old["answer"], old.get("answered_by", "")
                self.db.execute("UPDATE items SET data=? WHERE key=?", (json.dumps(data), key))
                status, note = row[0], row[1]
            else:
                self.db.execute("INSERT INTO items(key, first_seen, data) VALUES(?,?,?)",
                                (key, datetime.now().isoformat(timespec="seconds"), json.dumps(data)))
                status, note = "Open", ""
            self.db.commit()
        return status, note

    def all_items(self) -> list[dict]:
        with self._lock:
            rows = self.db.execute("SELECT key, first_seen, status, note, data FROM items").fetchall()
        out = []
        for key, first_seen, status, note, data in rows:
            d = json.loads(data)
            d.update(key=key, first_seen=first_seen, status=status, note=note)
            out.append(d)
        return out

    def set_status(self, key: str, status: str, note: str | None = None):
        with self._lock:
            if note is None:
                self.db.execute("UPDATE items SET status=? WHERE key=?", (status, key))
            else:
                self.db.execute("UPDATE items SET status=?, note=? WHERE key=?", (status, note, key))
            self.db.commit()

    def get_ai(self, msg_id: str):
        with self._lock:
            row = self.db.execute("SELECT data FROM ai_cache WHERE msg_id=?", (msg_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def put_ai(self, msg_id: str, data) -> None:
        with self._lock:
            self.db.execute("INSERT OR REPLACE INTO ai_cache VALUES(?,?)", (msg_id, json.dumps(data)))
            self.db.commit()

    def delete_item(self, key: str):
        with self._lock:
            self.db.execute("DELETE FROM items WHERE key=?", (key,))
            self.db.commit()

    # ------------------------------------------------------------------ OCR cache (scanned batches)
    def get_ocr(self, path: str, size: int, mtime: float) -> dict:
        """{page_index: lines} for pages already read, if the file hasn't changed since."""
        from . import ocr
        with self._lock:
            rows = self.db.execute("SELECT page, size, mtime, lines FROM ocr WHERE path=?", (path,)).fetchall()
            if rows and any(r[1] != size or abs(r[2] - mtime) > 1 for r in rows):
                self.db.execute("DELETE FROM ocr WHERE path=?", (path,))  # file was replaced; read it again
                self.db.commit()
                return {}
        return {page: ocr.loads(lines) for page, _s, _m, lines in rows}

    def put_ocr(self, path: str, size: int, mtime: float, page: int, lines):
        from . import ocr
        with self._lock:
            self.db.execute("INSERT OR REPLACE INTO ocr VALUES(?,?,?,?,?)", (path, page, size, mtime, ocr.dumps(lines)))
            self.db.commit()

    def clear_ocr(self):
        with self._lock:
            self.db.execute("DELETE FROM ocr")
            self.db.commit()

    # ------------------------------------------------------------------ amendments made by this app
    def add_amendment(self, rec: dict) -> int:
        cols = ("created", "item_key", "account", "patient", "dos", "batch", "page", "provider", "attending", "files", "initials")
        rec = dict(rec, created=rec.get("created") or datetime.now().isoformat(timespec="seconds"))
        rec["files"] = json.dumps(rec.get("files") or [])
        with self._lock:
            cur = self.db.execute(f"INSERT INTO amendments({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
                                  [rec.get(c, "") for c in cols])
            self.db.commit()
            return cur.lastrowid

    def amendments_for(self, account: str = "", dos: str = "") -> list[dict]:
        where, args = [], []
        if account:
            where.append("ltrim(account,'0')=?"); args.append(account.lstrip("0"))
        if dos:
            where.append("dos=?"); args.append(dos)
        sql = "SELECT id, created, item_key, account, patient, dos, batch, page, provider, attending, files, initials FROM amendments"
        with self._lock:
            rows = self.db.execute(sql + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id DESC", args).fetchall()
        keys = ("id", "created", "item_key", "account", "patient", "dos", "batch", "page", "provider", "attending", "files", "initials")
        out = []
        for r in rows:
            d = dict(zip(keys, r))
            d["files"] = json.loads(d["files"] or "[]")
            out.append(d)
        return out
