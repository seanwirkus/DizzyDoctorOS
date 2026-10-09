"""The screens on a fixed address with the made-up demo clinic, for development and checks:

    python -m recordfinder.charting.devserver            # http://127.0.0.1:8765/?t=devtoken
    node tools/headless_check.mjs "http://127.0.0.1:8765/?t=devtoken" --shot /tmp/shell.png

No real patient data is involved.
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading

from .providers import DemoProvider
from .server import ChartContext, ChartServer

PORT = int(os.environ.get("DDOS_DEV_PORT", "8765"))
TOKEN = "devtoken"


def main() -> None:
    data_dir = os.path.join(tempfile.gettempdir(), "ddos-demo-clinic")
    os.makedirs(data_dir, exist_ok=True)
    # Settings saved on the dev screens stay with the demo clinic: never the real app's config.json.
    from .. import config
    config.CONFIG_PATH = os.path.join(data_dir, "config.json")
    provider = DemoProvider(data_dir)
    cfg = {"notes_folders": [os.path.join(data_dir, "Progress Notes")], "prepped_notes_folder": os.path.join(data_dir, "Prepped"),
           "dictation_model": "small.en", "initials": "DEMO"}
    ctx = ChartContext(cfg, data_dir, provider=provider)
    ctx.token = TOKEN
    ctx.launch_classic = lambda tab="": print(f"office window requested: {tab or 'front'}", flush=True)
    server = ChartServer(ctx, PORT).start()
    print(server.url(), flush=True)
    if "--open" in sys.argv:
        from .server import open_window
        open_window(server.url())
    threading.Event().wait()


if __name__ == "__main__":
    main()
