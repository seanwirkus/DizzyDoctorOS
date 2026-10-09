"""DizzyDoctor: the app window.

The app is a local web screen (recordfinder/charting) in a native window. The office tabs (Billing
requests, Superbills, Balances, MIPS, Dashboard) are the classic Tkinter window: the top bar opens it
on the tab you pick, as its own process, since a Mac can only run one GUI toolkit on its main thread.
An office window that is already open switches tabs instead (classic_link.py).
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading

from . import config
from .version import VERSION

log = logging.getLogger("recordfinder")


def _setup_log() -> None:
    """app.log next to the index. The shell writes here; the classic Tk app has its own setup."""
    import logging.handlers
    try:
        os.makedirs(config.DATA_DIR, exist_ok=True)
        if any(isinstance(h, logging.handlers.RotatingFileHandler) for h in log.handlers):
            return
        handler = logging.handlers.RotatingFileHandler(
            os.path.join(config.DATA_DIR, "app.log"), maxBytes=1_000_000, backupCount=2, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(handler)
        log.setLevel(logging.INFO)
    except OSError:
        pass


def _visible_failure(message: str) -> None:
    """A real window, not the classic Tk app. A missing web view must not look like a successful launch."""
    log.error("%s", message)
    if sys.platform == "darwin":
        subprocess.run(
            ["osascript", "-e", "on run argv",
             "-e", 'display dialog (item 1 of argv) with title "DizzyDoctor" buttons {"OK"} default button "OK" with icon stop',
             "-e", "end run", message],
            check=False,
        )
        return
    if sys.platform.startswith("win"):
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, message, "DizzyDoctor", 0x10)
            return
        except Exception:
            pass
    print(message, file=sys.stderr)


def _context(cfg: dict, demo: bool = False):
    from .charting.providers import AppProvider, DemoProvider
    from .charting.server import ChartContext
    if demo:
        return ChartContext(cfg, config.DATA_DIR, provider=DemoProvider())
    from .index import Store
    store = Store(config.DB_PATH)
    return ChartContext(cfg, config.DATA_DIR, store, provider=AppProvider(store, cfg))


def classic_command() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "--classic"]
    return [sys.executable, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py"), "--classic"]


def launch_classic(tab: str = "") -> None:
    """The office window on `tab` (billing, superbills, balances, mips, dashboard, today, auth, patients)."""
    from . import classic_link, shared
    if classic_link.ask(config.DATA_DIR, ""):      # already open: it comes to the front on whatever tab it was on
        return
    subprocess.Popen(classic_command() + (["--tab", tab] if tab else []), close_fds=True, env=shared.fresh_env(),
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    classic_link.starting(config.DATA_DIR)


def _window_size(webview) -> tuple[int, int, bool]:
    """1480 x 940 where it fits; on a smaller screen (a 1366 x 768 clinic laptop) the window starts maximized."""
    width, height = 1480, 940
    try:
        screen = webview.screens[0]
        avail_w, avail_h = int(screen.width), int(screen.height) - 48     # leave room for the taskbar / menu bar
    except Exception:
        return width, height, False
    if avail_w < width + 40 or avail_h < height:
        return min(width, avail_w), min(height, avail_h), True
    return width, height, False


def run(demo: bool | None = None) -> None:
    """Open the web shell. The classic Tk window is only started with --classic (see main.py)."""
    _setup_log()
    from .charting.server import ChartServer, open_window
    server = None
    try:
        cfg = config.load()
        if demo is None:
            demo = bool(cfg.get("demo_mode"))
        ctx = _context(cfg, demo)
        ctx.launch_classic = launch_classic
        server = ChartServer(ctx).start()
        url = server.url()
        log.info("DizzyDoctor %s shell on %s", VERSION, url.split("?")[0])
        # DDOS_BROWSER: serve the shell and open Chrome/Edge in app mode (no native web view).
        if os.environ.get("DDOS_BROWSER"):
            open_window(url)
            threading.Event().wait()
            return
        try:
            import webview
        except Exception as ex:
            log.exception("pywebview import failed")
            _visible_failure(
                "DizzyDoctor could not open its window because pywebview did not import.\n\n"
                f"{type(ex).__name__}: {ex}\n\n"
                "The classic tools stay closed unless you start the app with --classic.")
            return
        try:
            width, height, maximized = _window_size(webview)
            window = webview.create_window("DizzyDoctor", url, width=width, height=height, min_size=(min(1100, width), min(700, height)),
                                           maximized=maximized, background_color="#f3f4f6", text_select=True)
            ctx.window = window
            kwargs = {}
            if sys.platform == "darwin":
                kwargs["gui"] = "cocoa"
            elif sys.platform.startswith("win"):
                kwargs["gui"] = "edgechromium"      # Edge WebView2, present on Windows 10/11
                if config.asset("DizzyDoctor.ico"):
                    kwargs["icon"] = config.asset("DizzyDoctor.ico")   # title bar and taskbar (the .exe carries it too)
            webview.start(private_mode=False, **kwargs)
        except Exception as ex:
            log.exception("Native window unavailable")
            _visible_failure(
                "DizzyDoctor could not open the native window.\n\n"
                f"{type(ex).__name__}: {ex}\n\n"
                "The classic tools stay closed unless you start the app with --classic.")
    except Exception as ex:
        log.exception("DizzyDoctor shell failed")
        _visible_failure(f"DizzyDoctor could not open.\n\n{type(ex).__name__}: {ex}")
    finally:
        if server is not None:
            server.stop()
