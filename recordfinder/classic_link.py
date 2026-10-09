"""The main window opens the office (classic) window's tabs: Billing requests, Superbills, Balances, MIPS, Dashboard.

They are two processes (a Mac can run only one window toolkit per process), so they talk through small files in the
data folder. While the office window is open it touches a heartbeat every couple of seconds and picks up "show this
tab" requests; the main window asks it instead of starting a second copy. Nothing here holds patient data.
"""
from __future__ import annotations

import json
import os
import time

ALIVE = "office-window-alive"
STARTING = "office-window-starting"
REQUEST = "office-window-request.json"
FRESH = 6        # seconds: the office window touches its heartbeat every 1.5 s
STARTUP = 60     # seconds a just-started office window gets to come up (loading the clinic data can take a while)


def _path(data_dir: str, name: str) -> str:
    return os.path.join(data_dir, name)


def _age(path: str) -> float | None:
    try:
        return time.time() - os.path.getmtime(path)
    except OSError:
        return None


def _touch(path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8"):
        pass
    os.utime(path, None)


def _remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def is_open(data_dir: str) -> bool:
    """An office window is open on this computer, or one was just started."""
    alive, starting = _age(_path(data_dir, ALIVE)), _age(_path(data_dir, STARTING))
    return (alive is not None and alive < FRESH) or (starting is not None and starting < STARTUP)


def ask(data_dir: str, tab: str) -> bool:
    """Ask the open office window to show `tab` and come to the front. False when none is open."""
    if not is_open(data_dir):
        return False
    tmp = _path(data_dir, REQUEST + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"tab": tab or "", "at": time.time()}, f)
    os.replace(tmp, _path(data_dir, REQUEST))
    return True


def starting(data_dir: str) -> None:
    """Just started an office window: further clicks ask it rather than start another copy."""
    _touch(_path(data_dir, STARTING))


def beat(data_dir: str) -> None:
    """The office window is open (called every 1.5 s while it is)."""
    _touch(_path(data_dir, ALIVE))
    _remove(_path(data_dir, STARTING))


def take_request(data_dir: str) -> dict | None:
    """The waiting "show this tab" request, if any (it is used up)."""
    path = _path(data_dir, REQUEST)
    try:
        with open(path, encoding="utf-8") as f:
            req = json.load(f)
    except (OSError, ValueError):
        return None
    finally:
        _remove(path)
    return req if isinstance(req, dict) else None


def closed(data_dir: str) -> None:
    _remove(_path(data_dir, ALIVE))
    _remove(_path(data_dir, STARTING))
