"""The aircraft computer's software as one update package (a zip), so the
Raspberry Pi can be updated from the Ground Station over its Wi-Fi or a USB
stick, without internet on the Pi.

The packaged desktop app carries a prebuilt package (pi_software.zip, made by
packaging/build_app.py); from the source folder it is built on the fly.
"""

from __future__ import annotations

import io
import os
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ONBOARD = os.path.dirname(HERE)
PREBUILT = os.path.join(ONBOARD, "pi_software.zip")

# what the Pi runs or reads (paths relative to onboard/)
PI_FILES = ["run.py", "VERSION", "config.yaml", "requirements.txt", "LICENSE", "THIRD_PARTY_NOTICES.md",
            "README.md", "FLIGHT_READY.md", "pi/install.sh", "pi/silm-wifi",
            "models/coco_labels.txt", "models/README.md", "models/silm_oi.tflite",
            "models/silm_oi_labels.txt", "models/silm_oi.json", "models/LICENSE-silm_oi-AGPL-3.0.txt"]
PI_DIRS = [("silm_recon", (".py",)), ("tools", (".py",)), ("systemd", (".service",))]
REQUIRED = ("VERSION", "run.py", "config.yaml", "silm_recon/agent.py", "silm_recon/recon.py")
FIXED_TIME = (2026, 1, 1, 0, 0, 0)


def pi_file_list(onboard: str = ONBOARD) -> list[str]:
    files = [f for f in PI_FILES if os.path.isfile(os.path.join(onboard, f))]
    for d, exts in PI_DIRS:
        for root, dirs, names in os.walk(os.path.join(onboard, d)):
            dirs[:] = sorted(x for x in dirs if x != "__pycache__")
            for n in sorted(names):
                if n.endswith(exts):
                    files.append(os.path.relpath(os.path.join(root, n), onboard).replace(os.sep, "/"))
    return sorted(set(files))


def build_package(onboard: str = ONBOARD) -> bytes:
    """A reproducible zip of the Pi's files (same files -> same bytes)."""
    files = pi_file_list(onboard)
    missing = [f for f in REQUIRED if f not in files]
    if missing:
        raise FileNotFoundError(f"not a complete onboard folder, missing {missing}")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            info = zipfile.ZipInfo(f, FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            mode = 0o755 if f.endswith((".sh", "silm-wifi")) else 0o644
            info.external_attr = (mode | 0o100000) << 16
            with open(os.path.join(onboard, f), "rb") as fh:
                z.writestr(info, fh.read())
    return buf.getvalue()


def package() -> tuple[bytes, str]:
    """(the update package, its version)."""
    if os.path.isfile(PREBUILT):
        data = open(PREBUILT, "rb").read()
    else:
        data = build_package()
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        version = z.read("VERSION").decode().strip()
    return data, version
