"""Safe updater for computers that run DizzyDoctor from a Git checkout.

Only a clean, fast-forwardable checkout is changed.  Local settings, databases,
uploads and generated output are ignored by Git and are never touched.
"""
from __future__ import annotations

import os
import subprocess
import sys


def _git(root: str, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", root, *args],
        check=check,
        capture_output=True,
        text=True,
        timeout=30,
    )


def update(root: str) -> tuple[str, bool]:
    """Fetch and fast-forward the checkout. Return (human message, changed)."""
    if getattr(sys, "frozen", False) or not os.path.isdir(os.path.join(root, ".git")):
        return "This copy is an installed build; it updates from GitHub Releases when it opens.", False
    try:
        dirty = _git(root, "status", "--porcelain", "--untracked-files=no").stdout.strip()
        if dirty:
            return "Update skipped because this source checkout has local code changes.", False
        upstream = _git(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}").stdout.strip()
        _git(root, "fetch", "--quiet", "--prune", "origin")
        head = _git(root, "rev-parse", "HEAD").stdout.strip()
        remote = _git(root, "rev-parse", upstream).stdout.strip()
        if head == remote:
            return "DizzyDoctor is already up to date.", False
        ahead = _git(root, "merge-base", "--is-ancestor", head, remote, check=False)
        if ahead.returncode:
            return "Update found, but it is not a safe fast-forward; no files were changed.", False
        changed = set(_git(root, "diff", "--name-only", head, remote).stdout.splitlines())
        _git(root, "merge", "--ff-only", "--quiet", remote)
        if "requirements.txt" in changed:
            try:
                subprocess.run(
                    [sys.executable, "-m", "pip", "install", "-r", os.path.join(root, "requirements.txt")],
                    check=True,
                    timeout=600,
                )
            except (OSError, subprocess.SubprocessError) as ex:
                return f"DizzyDoctor updated, but a dependency could not be installed ({ex}). Restarting.", True
        return "DizzyDoctor updated. Restarting with the new version.", True
    except (OSError, subprocess.SubprocessError) as ex:
        return f"Couldn't check for an update ({ex}). Starting the installed version.", False
