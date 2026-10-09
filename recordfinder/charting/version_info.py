"""The app version, importable without pulling in the Tkinter app."""
try:
    from ..version import VERSION
except Exception:  # pragma: no cover - running the charting package on its own
    VERSION = "dev"
