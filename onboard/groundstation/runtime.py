"""Where the Ground Station runs: from source, or as the packaged desktop app.

The packaged app (PyInstaller) has no Python interpreter of its own that can
run other scripts, so the two features that start separate Python programs -
the simulator test and training - look for a Python on the computer instead.
"""

from __future__ import annotations

import functools
import os
import shutil
import subprocess
import sys


def frozen() -> bool:
    """True inside the packaged desktop app."""
    return bool(getattr(sys, "frozen", False))


@functools.lru_cache(maxsize=None)
def python_cmd() -> tuple[str, ...] | None:
    """The command that starts a Python 3.9+ interpreter for helper scripts,
    or None if there is none (packaged app on a computer without Python)."""
    if not frozen():
        return (sys.executable,)
    candidates = [("python3",), ("python",)] + ([("py", "-3")] if os.name == "nt" else [])
    for c in candidates:
        exe = shutil.which(c[0])
        if not exe:
            continue
        cmd = (exe,) + c[1:]
        try:
            r = subprocess.run(cmd + ("-c", "import sys; print(sys.version_info >= (3, 9))"),
                               capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            continue
        if r.returncode == 0 and r.stdout.strip() == "True":
            return cmd
    return None


def has_module(module: str) -> bool:
    """Can the helper Python import `module`?"""
    cmd = python_cmd()
    if cmd is None:
        return False
    if not frozen():
        try:
            __import__(module)
            return True
        except Exception:
            return False
    try:
        return subprocess.run(cmd + ("-c", f"import {module}"), capture_output=True,
                              timeout=60).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False
