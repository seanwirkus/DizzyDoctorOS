import multiprocessing
import os
import sys


def _update_source_checkout() -> None:
    """Update before importing the application, then restart so no old modules remain loaded."""
    background_commands = {"--selftest", "--map-superbills", "--after"}
    if "--no-update" in sys.argv or background_commands.intersection(sys.argv) or os.environ.get("DIZZYDOCTOR_UPDATED") == "1":
        return
    root = os.path.dirname(os.path.abspath(__file__))
    from recordfinder.source_update import update
    message, changed = update(root)
    if "--update-only" in sys.argv or changed:
        print(message)
    if changed:
        env = dict(os.environ)
        env["DIZZYDOCTOR_UPDATED"] = "1"
        args = [a for a in sys.argv if a != "--update-only"]
        os.execve(sys.executable, [sys.executable, os.path.abspath(__file__), *args[1:]], env)
    if "--update-only" in sys.argv:
        raise SystemExit(0)

if __name__ == "__main__":
    _update_source_checkout()
    # set by whoever started this copy as a program of its own (shared.fresh_env); this copy's own helpers - the
    # superbill mapper's workers - must share its unpacked folder again, so don't hand it on
    os.environ.pop("PYINSTALLER_RESET_ENVIRONMENT", None)
    multiprocessing.freeze_support()  # the superbill mapper's worker processes start from this same .exe
    if len(sys.argv) > 2 and sys.argv[1] == "--selftest":
        from recordfinder.selftest import run
        run(sys.argv[2])
    elif len(sys.argv) > 1 and sys.argv[1] == "--map-superbills":
        # DizzyDoctor.exe --map-superbills [--workers N] [--years 2026,2025] [--limit N]
        from recordfinder import config, superbill_map
        args = dict(zip(sys.argv[2::2], sys.argv[3::2]))
        years = [int(y) for y in args.get("--years", "").split(",") if y.strip()] or None
        superbill_map.run(config.DB_PATH, config.load(), workers=int(args.get("--workers", 6)), years=years,
                          limit=int(args["--limit"]) if "--limit" in args else None)
    elif "--classic" in sys.argv:
        # Tk screens. Other flags (--after, --no-update) stay on sys.argv for app.main.
        sys.argv = [a for a in sys.argv if a != "--classic"]
        from recordfinder.app import main
        main()
    else:
        from recordfinder.shell import run
        run(demo=True if "--demo" in sys.argv else None)
