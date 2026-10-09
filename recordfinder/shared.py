r"""Clinic-wide install: one main PC keeps the clinic data current, every other PC loads it from the file server.

The main PC (config "publish_data": true) is the one with the staff Outlook, the Veradigm report downloads and the
superbill mapper. After it refreshes its data it publishes a copy of its index to the server; the other PCs install
the app from the server, update themselves when a newer build is published there, and load the newest published
data at start instead of scanning the drive, Outlook and Veradigm themselves.

Server folder (config "share_folder", e.g. V:\DizzyDoctor App):
    DizzyDoctor.exe + version.json  the current build; the installer puts it on a PC
    config.json                         the office settings a newly installed PC starts with
    data\index-<stamp>.db               published data - the newest two are kept
    data\latest.json                    which one is newest

Each PC's own work never leaves it and survives a data load: billing requests pasted from Teams, the amendment log,
cached AI readings and today's AI spend.
"""
from __future__ import annotations

import glob
import hashlib
import json
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime

from .version import VERSION, parse

log = logging.getLogger("recordfinder")

LOCAL_TABLES = ("items", "amendments", "ai_cache", "note_cache")
MARKER = "shared.json"                    # next to the local index: which published data it holds
CHUNK = 4 * 1024 * 1024
GITHUB_LATEST = "https://api.github.com/repos/seanwirkus/DizzyDoctorOS/releases/latest"


def _local_meta(k: str) -> bool:
    return k.startswith("ai_spend:") or k in ("sb_stop", "shared_published")


# --------------------------------------------------------------------------- settings

def share_folder(cfg: dict) -> str:
    return (cfg.get("share_folder") or "").strip()


def is_main(cfg: dict) -> bool:
    return bool(share_folder(cfg)) and bool(cfg.get("publish_data"))


def is_client(cfg: dict) -> bool:
    return bool(share_folder(cfg)) and not cfg.get("publish_data")


def _read_json(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write_json(path: str, data: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    for attempt in range(10):                 # a PC reading it at that moment blocks the rename on the share
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.5 * (attempt + 1))
    os.replace(tmp, path)


def latest(share: str) -> dict | None:
    info = _read_json(os.path.join(share, "data", "latest.json"))
    return info if info and info.get("file") else None


def marker(db_path: str) -> dict:
    return _read_json(os.path.join(os.path.dirname(db_path), MARKER)) or {}


# --------------------------------------------------------------------------- main PC: publish

def fingerprint(db: sqlite3.Connection) -> str:
    """Changes whenever the data the other PCs see changes (scan, sync, map, imports, superbill progress)."""
    rows = [(k, v) for k, v in db.execute("SELECT k, v FROM meta ORDER BY k") if not _local_meta(k)]
    return hashlib.sha1(json.dumps(rows).encode()).hexdigest()


def publish(db_path: str, share: str, keep: int = 2) -> dict:
    """Copy this PC's index to the server as the newest published data. Returns what latest.json now says."""
    data = os.path.join(share, "data")
    os.makedirs(data, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
    name = f"index-{stamp}.db"
    tmp = os.path.join(os.path.dirname(db_path), f"publish-{stamp}.db")
    src = sqlite3.connect(db_path, timeout=60)
    try:
        fp = fingerprint(src)
        src.execute("VACUUM INTO ?", (tmp,))  # a consistent copy even while the app keeps writing
    finally:
        src.close()
    try:
        con = sqlite3.connect(tmp)
        try:
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for t in LOCAL_TABLES:
                if t in tables:
                    con.execute(f'DELETE FROM "{t}"')
            keys = [k for (k,) in con.execute("SELECT k FROM meta") if _local_meta(k)]
            con.executemany("DELETE FROM meta WHERE k = ?", [(k,) for k in keys])
            counts = {t: con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                      for t in ("patients", "files", "appts", "vd_vouchers", "balances", "sb_pages") if t in tables}
            con.commit()
        finally:
            con.close()
        size = os.path.getsize(tmp)
        part = os.path.join(data, name + ".part")
        shutil.copyfile(tmp, part)
        os.replace(part, os.path.join(data, name))
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    info = {"file": name, "stamp": stamp, "created": datetime.now().isoformat(timespec="seconds"), "size": size,
            "version": VERSION, "from": os.environ.get("COMPUTERNAME", ""), "fingerprint": fp, "counts": counts}
    _write_json(os.path.join(data, "latest.json"), info)
    _prune(data, keep, name)
    return info


def _prune(data: str, keep: int, current: str) -> None:
    """Only the newest `keep` published copies stay (a PC may still be copying the previous one)."""
    files = sorted(glob.glob(os.path.join(data, "index-*.db")))
    for path in files[:-keep] if keep > 0 else files:
        if os.path.basename(path) != current:
            try:
                os.remove(path)
            except OSError:
                pass                              # someone is copying it right now: next time
    for path in glob.glob(os.path.join(data, "*.part")):
        try:
            if time.time() - os.path.getmtime(path) > 86400:
                os.remove(path)
        except OSError:
            pass


# --------------------------------------------------------------------------- other PCs: load the published data

class Busy(Exception):
    """The local index is open in another DizzyDoctor window, so it can't be swapped right now."""


def _download(share: str, info: dict, db_path: str, progress=None) -> str:
    """The published file copied next to the local index (a finished earlier copy is reused)."""
    os.makedirs(os.path.dirname(db_path), exist_ok=True)            # a PC that never ran the app yet
    target = os.path.join(os.path.dirname(db_path), info["file"] + ".download")
    if os.path.exists(target) and os.path.getsize(target) == info.get("size"):
        return target
    for old in glob.glob(os.path.join(os.path.dirname(db_path), "index-*.download*")):
        if not old.startswith(target):
            try:
                os.remove(old)                    # an older download that was never loaded
            except OSError:
                pass
    part = target + ".part"
    src = os.path.join(share, "data", info["file"])
    total, done = info.get("size") or os.path.getsize(src), 0
    with open(src, "rb") as fi, open(part, "wb") as fo:
        while True:
            buf = fi.read(CHUNK)
            if not buf:
                break
            fo.write(buf)
            done += len(buf)
            if progress:
                progress(done, total)
    if info.get("size") and os.path.getsize(part) != info["size"]:
        os.remove(part)
        raise OSError("the copy from the server came out the wrong size - try again")
    os.replace(part, target)
    return target


def _install(new_path: str, db_path: str) -> None:
    """Make the downloaded file this PC's index, keeping this PC's own tables and settings."""
    con = sqlite3.connect(new_path)
    try:
        con.execute("PRAGMA journal_mode=DELETE")
        con.execute("SELECT 1 FROM files LIMIT 1").fetchall()         # a readable index, or this raises
        if os.path.exists(db_path):
            con.execute("ATTACH DATABASE ? AS old", (db_path,))
            new_tables = {r[0]: r[1] for r in con.execute("SELECT name, sql FROM main.sqlite_master WHERE type='table'")}
            old_tables = {r[0]: r[1] for r in con.execute("SELECT name, sql FROM old.sqlite_master WHERE type='table'")}
            for t in LOCAL_TABLES:
                if t not in old_tables:
                    continue
                if t not in new_tables:
                    con.execute(old_tables[t])
                cols_new = [r[1] for r in con.execute(f'PRAGMA main.table_info("{t}")')]
                cols_old = {r[1] for r in con.execute(f'PRAGMA old.table_info("{t}")')}
                cols = ", ".join(f'"{c}"' for c in cols_new if c in cols_old)
                con.execute(f'DELETE FROM main."{t}"')
                con.execute(f'INSERT INTO main."{t}" ({cols}) SELECT {cols} FROM old."{t}"')
            if "meta" in old_tables:
                rows = [(k, v) for k, v in con.execute("SELECT k, v FROM old.meta") if _local_meta(k)]
                con.executemany("INSERT OR REPLACE INTO main.meta(k, v) VALUES(?, ?)", rows)
            con.commit()
            con.execute("DETACH DATABASE old")
    finally:
        con.close()
    if os.path.exists(db_path):
        prev = db_path + ".prev"
        try:
            os.replace(db_path, prev)             # fails while another window has the index open
        except PermissionError as ex:
            raise Busy(str(ex)) from ex
        for ext in ("-wal", "-shm"):              # the old index's journal must never meet the new file
            try:
                os.remove(db_path + ext)
            except FileNotFoundError:
                pass
    os.replace(new_path, db_path)
    try:
        os.remove(db_path + ".prev")
    except OSError:
        pass


def pull(cfg: dict, db_path: str, progress=None, force: bool = False) -> str:
    """Load the newest published data if this PC doesn't have it yet. Returns a line for the status bar."""
    share = share_folder(cfg)
    if not share:
        return ""
    if not os.path.isdir(share):
        return f"Can't reach {share} - showing the clinic data from the last time it was loaded."
    info = latest(share)
    if not info:
        return "No clinic data has been published to the server yet."
    have = marker(db_path)
    if not force and have.get("stamp") == info["stamp"] and os.path.exists(db_path):
        return f"Clinic data from {_when(info)}."
    path = _download(share, info, db_path, progress)
    try:
        _install(path, db_path)
    except Busy:
        return "DizzyDoctor is open in another window - the newest clinic data loads when all windows are closed."
    _write_json(os.path.join(os.path.dirname(db_path), MARKER),
                {"stamp": info["stamp"], "file": info["file"], "created": info.get("created", ""),
                 "from": info.get("from", ""), "loaded": datetime.now().isoformat(timespec="seconds")})
    log.info("Loaded published clinic data %s (%s bytes)", info["file"], info.get("size"))
    return f"Clinic data from {_when(info)}."


def prefetch(cfg: dict, db_path: str) -> dict | None:
    """While the app runs: copy newer published data down in the background, ready for the next start."""
    share = share_folder(cfg)
    info = latest(share) if share and os.path.isdir(share) else None
    if not info or marker(db_path).get("stamp") == info["stamp"]:
        return None
    _download(share, info, db_path)
    return info


def _when(info: dict) -> str:
    try:
        return f"{datetime.fromisoformat(info['created']):%m/%d %I:%M %p}" + (f" ({info['from']})" if info.get("from") else "")
    except (KeyError, ValueError):
        return info.get("created", "?")


# --------------------------------------------------------------------------- updating the app itself

def newer_build(cfg: dict) -> dict | None:
    """A newer Windows build, preferring the clinic share and falling back to GitHub Releases."""
    share = share_folder(cfg)
    # The Mac app is updated by installing a new signed build; source checkouts use source_update.py.
    if not getattr(sys, "frozen", False) or not sys.platform.startswith("win"):
        return None
    if share:
        info = _read_json(os.path.join(share, "version.json"))
        if info and parse(info.get("version", "")) > parse(VERSION):
            exe = os.path.join(share, info.get("exe", "DizzyDoctor.exe"))
            if not os.path.isfile(exe) and "exe" not in info:
                exe = os.path.join(share, "DizzyDoctorOS.exe")
            if os.path.isfile(exe) and os.path.normcase(os.path.abspath(exe)) != os.path.normcase(os.path.abspath(sys.executable)):
                return dict(info, path=exe, source="clinic server")
    req = urllib.request.Request(GITHUB_LATEST, headers={"Accept": "application/vnd.github+json",
                                                         "User-Agent": f"DizzyDoctor/{VERSION}"})
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            release = json.load(response)
    except (OSError, ValueError):
        return None
    version = str(release.get("tag_name", "")).lstrip("vV")
    if parse(version) <= parse(VERSION):
        return None
    asset = next((a for a in release.get("assets", []) if a.get("name") == "DizzyDoctor.exe"), None)
    if not asset or not asset.get("browser_download_url"):
        return None
    digest = str(asset.get("digest", ""))
    return {"version": version, "url": asset["browser_download_url"],
            "sha256": digest.removeprefix("sha256:") if digest.startswith("sha256:") else "",
            "size": asset.get("size", 0), "source": "GitHub Releases"}


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for buf in iter(lambda: f.read(CHUNK), b""):
            h.update(buf)
    return h.hexdigest()


def open_file(path: str) -> None:
    """Open a file or folder with its default app (Explorer on Windows, Finder on a Mac)."""
    if sys.platform.startswith("win"):
        os.startfile(path)  # noqa: S606 - opening the clinic's own file
    else:
        subprocess.Popen(["open", path])


def others_running(exe: str) -> int:
    """How many OTHER processes were started from this same .exe - another window, or the superbill mapper and its
    workers. Windows can't replace an .exe while any of them runs, so an update waits until they're gone."""
    try:
        import ctypes
        from ctypes import wintypes

        class Entry(ctypes.Structure):
            _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                        ("th32DefaultHeapID", ctypes.c_void_p), ("th32ModuleID", wintypes.DWORD),
                        ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                        ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]

        k32 = ctypes.windll.kernel32
        k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        k32.Process32FirstW.argtypes = k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
        k32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                   ctypes.POINTER(wintypes.DWORD)]
        target = os.path.normcase(os.path.abspath(exe))
        name = os.path.basename(exe).lower()
        mine = {os.getpid(), os.getppid()}             # this process and its launcher (the one-file .exe's stub)
        snap = k32.CreateToolhelp32Snapshot(0x2, 0)    # TH32CS_SNAPPROCESS
        n = 0
        e = Entry()
        e.dwSize = ctypes.sizeof(Entry)
        ok = k32.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            if e.szExeFile.lower() == name and e.th32ProcessID not in mine:
                h = k32.OpenProcess(0x1000, False, e.th32ProcessID)   # PROCESS_QUERY_LIMITED_INFORMATION
                if h:
                    buf = ctypes.create_unicode_buffer(1024)
                    size = wintypes.DWORD(1024)
                    if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)) and os.path.normcase(buf.value) == target:
                        n += 1
                    k32.CloseHandle(h)
            ok = k32.Process32NextW(snap, ctypes.byref(e))
        k32.CloseHandle(snap)
        return n
    except Exception:
        return 0


def stage_update(info: dict, progress=None) -> str:
    """Copy the new build next to this one (checked against version.json) and return the updater script to run."""
    cur = sys.executable
    new = cur + ".new"
    if info.get("url"):
        req = urllib.request.Request(info["url"], headers={"User-Agent": f"DizzyDoctor/{VERSION}"})
        done = 0
        with urllib.request.urlopen(req, timeout=30) as src, open(new, "wb") as dst:
            total = int(info.get("size") or src.headers.get("Content-Length") or 0)
            while True:
                buf = src.read(CHUNK)
                if not buf:
                    break
                dst.write(buf)
                done += len(buf)
                if progress:
                    progress(done, total)
    else:
        shutil.copyfile(info["path"], new)
    if info.get("sha256") and _sha256(new) != info["sha256"]:
        os.remove(new)
        raise OSError("the new version didn't copy correctly")
    script = os.path.join(tempfile.gettempdir(), f"dizzydoctor_update_{os.getpid()}.cmd")
    with open(script, "w", encoding="ascii", errors="replace") as f:
        f.write("@echo off\r\n"
                "set n=0\r\n"
                ":try\r\n"
                f'move /y "{new}" "{cur}" >nul 2>&1 && goto ok\r\n'
                "set /a n+=1\r\n"
                "if %n% geq 45 goto giveup\r\n"
                "ping -n 2 127.0.0.1 >nul\r\n"
                "goto try\r\n"
                ":ok\r\n"
                f'start "" "{cur}"\r\n'
                "goto end\r\n"
                ":giveup\r\n"                     # still locked: open the current version, no new attempt this time
                f'start "" "{cur}" --no-update\r\n'
                ":end\r\n"
                'del "%~f0"\r\n')
    return script


def fresh_env() -> dict:
    """Environment for starting another copy of the one-file .exe as its own program. Without it the child reuses
    this process's unpacked temp folder: when this one closes, the folder can't be removed ("Warning: failed to remove
    temporary directory") or is gone from under the child, which then can't start."""
    env = dict(os.environ)
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env


def run_updater(script: str) -> None:
    # CREATE_NO_WINDOW alone: the script gets a hidden console that its ping/move inherit (with DETACHED_PROCESS
    # each ping opened a visible console window of its own)
    subprocess.Popen(["cmd", "/c", script], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), close_fds=True,
                     env=fresh_env())


def mapper_exe() -> str:
    """A private copy of this .exe for the superbill mapper (hours of work), so the mapper never keeps the app's own
    .exe locked - updates can then swap it at any time."""
    if not sys.platform.startswith("win"):
        return sys.executable                             # a Mac app can't be copied out of its bundle, and isn't locked
    from . import config
    target = os.path.join(config.DATA_DIR, "mapper", "DizzyDoctor-mapper.exe")
    src = sys.executable
    try:
        same = os.path.getsize(target) == os.path.getsize(src) and os.path.getmtime(target) >= os.path.getmtime(src)
    except OSError:
        same = False
    if not same:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copy2(src, target + ".tmp")
        os.replace(target + ".tmp", target)
    return target


def restart(after_pid: int | None = None) -> None:
    """Start a fresh copy of the app (used after newer data was copied down). The new copy waits for this one to
    close first (--after), because the data can only be swapped once nothing has the index open."""
    if getattr(sys, "frozen", False):
        cmd = [sys.executable]
    else:
        cmd = [sys.executable, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py")]
    if after_pid:
        cmd += ["--after", str(after_pid)]
    subprocess.Popen(cmd, close_fds=True, env=fresh_env())


def pid_alive(pid: int) -> bool:
    if not sys.platform.startswith("win"):
        try:
            os.kill(int(pid), 0)                           # signal 0: only checks the process exists
            return True
        except ProcessLookupError:
            return False
        except (PermissionError, OSError, ValueError):
            return False
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


def wait_for_exit(pid: int, seconds: float = 60) -> None:
    t0 = time.time()
    while pid_alive(pid) and time.time() - t0 < seconds:
        time.sleep(0.3)
