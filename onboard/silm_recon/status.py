"""Live status shared between the flight program and the ground agent.

The flight program (recon.py) writes a small JSON file about once a second:
armed, flight mode, altitude, GPS state, camera, last saved picture, health.
The ground agent (agent.py: Wi-Fi hotspot API and USB-stick transfer) reads it
to show the aircraft's state in the laptop GUI and, above all, to REFUSE to
change settings or switch Wi-Fi on while the aircraft is armed.

Written atomically (temp file + rename) so a reader never sees half a file.
"""

from __future__ import annotations

import json
import os
import tempfile
import time

DEFAULT_PATH = "/run/silm/status.json"


def status_path(cfg: dict | None) -> str:
    return ((cfg or {}).get("status") or {}).get("path") or DEFAULT_PATH


def write_status(path: str, data: dict) -> bool:
    """Atomically write `data` (+ a timestamp). Returns False if the folder
    can't be written (never raises: status must not break the flight program)."""
    try:
        d = os.path.dirname(path) or "."
        os.makedirs(d, exist_ok=True)
        payload = dict(data, updated=time.time())
        fd, tmp = tempfile.mkstemp(prefix=".status-", dir=d)
        with os.fdopen(fd, "w") as fh:
            json.dump(payload, fh)
        os.replace(tmp, path)
        return True
    except Exception:
        return False


def read_status(path: str, max_age_s: float = 10.0, now: float | None = None) -> dict:
    """The last status, with `fresh` = written within max_age_s (False when
    the flight program is not running). Missing/corrupt file -> {fresh: False}."""
    try:
        with open(path) as fh:
            data = json.load(fh)
    except Exception:
        return {"fresh": False}
    now = time.time() if now is None else now
    data["fresh"] = (now - float(data.get("updated", 0))) <= max_age_s
    return data


def armed_or_unknown_flying(st: dict) -> bool:
    """True if the aircraft must be treated as possibly flying: it reported
    armed, or it reported being airborne (above 5 m), in its last fresh status."""
    if not st.get("fresh"):
        return False
    return bool(st.get("armed")) or float(st.get("rel_alt_m") or 0.0) > 5.0
