#!/usr/bin/env python3
"""Build the files offered on the website's Downloads section.

    python3 onboard/tools/build_downloads.py            # from the repository root

Writes `downloads/` at the repository root (the website serves it):

    uav-silm-software-<version>.zip   the whole onboard folder: Ground Station,
                                      Pi software, ArduPilot files, docs, tests
    silm-oi-farm-model-v1.zip         the ready-made recognition model only
    gps_denied_failsafe.param         the flight controller settings
    silm_gps_loss.lua                 the GPS-loss script
    SHA256SUMS.txt                    a checksum per file
    downloads.json                    names, sizes, checksums (for the page)

Safety of the files:
- Only files **committed to git** are packed, read straight from the commit
  (never stray local files such as datasets, logs or settings), and the
  last commit that changed `onboard/` is written into the package.
- Files that must not be published are left out (EXCLUDE). The script stops
  if a file looks like it holds a secret.
- Zips are reproducible: sorted names and fixed timestamps, so the same
  commit always gives byte-identical files and the same checksums.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import zipfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
FIXED_TIME = (2026, 1, 1, 0, 0, 0)
EXCLUDE = re.compile(r"^onboard/(HANDOFF\.md$|\.github/|.*__pycache__/)")
SECRET = re.compile(rb"(BEGIN (RSA|OPENSSH|EC) PRIVATE KEY|ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}"
                    rb"|AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9]{32,}|xox[bp]-[A-Za-z0-9-]{20,})")
MODEL_FILES = ["models/silm_oi.tflite", "models/silm_oi_labels.txt", "models/silm_oi.json",
               "models/LICENSE-silm_oi-AGPL-3.0.txt"]


def git(*args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True).stdout


def tracked_files(commit: str) -> list[str]:
    names = git("ls-tree", "-r", "--name-only", commit, "--", "onboard").decode().splitlines()
    return sorted(n for n in names if not EXCLUDE.search(n))


def blob(commit: str, path: str) -> bytes:
    return git("show", f"{commit}:{path}")


def is_executable(commit: str, path: str) -> bool:
    mode = git("ls-tree", commit, "--", path).split()[0]
    return mode == b"100755"


def add(z: zipfile.ZipFile, name: str, data: bytes, executable: bool = False) -> None:
    info = zipfile.ZipInfo(name, FIXED_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = ((0o755 if executable else 0o644) | 0o100000) << 16
    info.create_system = 3          # unix permissions
    z.writestr(info, data, compresslevel=9)


def check_secret(path: str, data: bytes) -> None:
    if path.endswith((".tflite", ".woff2", ".jpg", ".png")):
        return
    m = SECRET.search(data)
    if m:
        raise SystemExit(f"STOP: {path} looks like it contains a secret ({m.group(0)[:12]!r}...). "
                         "Remove it from the commit before publishing.")


def build(out: str, commit: str) -> list[dict]:
    os.makedirs(out, exist_ok=True)
    version = blob(commit, "onboard/VERSION").decode().strip()
    when = git("log", "-1", "--format=%cs", commit).decode().strip()
    short = commit[:10]
    url = "https://github.com/rometlehari-maker/uav-silm"
    source_note = (f"UAV SILM software {version}\nBuilt from commit {commit} ({when})\n"
                   f"Source: {url}/tree/{commit}/onboard\n"
                   "Licence: MIT for the UAV SILM team's files (LICENSE); third-party files keep their "
                   "own licences (THIRD_PARTY_NOTICES.md).\n").encode()
    files = []

    # 1. the whole software
    name = f"uav-silm-software-{version}.zip"
    top = f"uav-silm-software-{version}/"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for p in tracked_files(commit):
            data = blob(commit, p)
            check_secret(p, data)
            add(z, top + p[len("onboard/"):], data, is_executable(commit, p))
        add(z, top + "SOURCE.txt", source_note)
    files.append(_write(out, name, buf.getvalue(), "software", version,
                        "Ground Station (laptop), Raspberry Pi software, ArduPilot settings and "
                        "GPS-loss script, simulator tools, documentation and tests.", "MIT + third-party"))

    # 2. the model alone
    name = "silm-oi-farm-model-v1.zip"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for p in MODEL_FILES:
            add(z, "silm-oi-farm-model-v1/" + os.path.basename(p), blob(commit, "onboard/" + p))
        add(z, "silm-oi-farm-model-v1/README.txt", (
            "silm_oi - ready-made recognition model of the UAV SILM project\n"
            "Finds: tractor, car, truck, livestock. YOLO11n, 320x320 input, TFLite (INT8 body, float head).\n"
            "Trained on Google Open Images photos (boxes CC BY 4.0, photos CC BY 2.0 by their Flickr authors).\n"
            "Mostly ground-level photos: from straight above it recognises much less. Measured results:\n"
            f"{url}/blob/{commit}/onboard/training/README.md\n\n"
            "Licence: AGPL-3.0 (derived from Ultralytics YOLO11n, (c) Ultralytics), see "
            "LICENSE-silm_oi-AGPL-3.0.txt.\n"
            "Corresponding source (how it was made): onboard/training/ in\n"
            f"{url}/tree/{commit}/onboard/training\n"
            f"Built from commit {commit} ({when}).\n").encode())
    files.append(_write(out, name, buf.getvalue(), "model", "v1",
                        "The ready-made recognition model only (tractor, car, truck, livestock), with "
                        "labels and its description.", "AGPL-3.0"))

    # 3. two single files people often want on their own
    files.append(_write(out, "gps_denied_failsafe.param", blob(commit, "onboard/ardupilot/gps_denied_failsafe.param"),
                        "param", version, "ArduPlane parameters: no-RC operation and all failsafes ending in "
                        "the automatic landing. Load in Mission Planner or QGroundControl.", "MIT"))
    files.append(_write(out, "silm_gps_loss.lua", blob(commit, "onboard/ardupilot/scripts/silm_gps_loss.lua"),
                        "lua", version, "ArduPilot Lua script: GPS lost, jammed or spoofed - stop trusting "
                        "GPS and land on dead reckoning. Copy to APM/scripts on the SD card.", "MIT"))

    with open(os.path.join(out, "SHA256SUMS.txt"), "w", encoding="utf-8", newline="\n") as fh:
        for f in files:
            fh.write(f"{f['sha256']}  {f['name']}\n")
    meta = {"version": version, "commit": commit, "commit_short": short, "date": when, "files": files}
    with open(os.path.join(out, "downloads.json"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(meta, fh, indent=2)
        fh.write("\n")
    return files


def _write(out, name, data, kind, version, what, licence) -> dict:
    with open(os.path.join(out, name), "wb") as fh:
        fh.write(data)
    return {"name": name, "kind": kind, "version": version, "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(), "what": what, "licence": licence}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=os.path.join(ROOT, "downloads"))
    ap.add_argument("--commit", default=None,
                    help="git commit to pack (default: the last commit that changed onboard/)")
    a = ap.parse_args(argv)
    commit = a.commit or git("log", "-1", "--format=%H", "HEAD", "--", "onboard").decode().strip()
    commit = git("rev-parse", commit).decode().strip()
    if git("status", "--porcelain", "--", "onboard").strip():
        print("note: onboard/ has uncommitted changes; they are NOT in the downloads "
              f"(packing commit {commit[:10]})", file=sys.stderr)
    for f in build(a.out, commit):
        print(f"{f['name']:34s} {f['bytes'] / 1e6:6.2f} MB  {f['sha256']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
