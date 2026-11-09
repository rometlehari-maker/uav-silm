#!/usr/bin/env python3
"""Build the download files for a GitHub release, and the website's version line.

    python3 onboard/tools/build_downloads.py                       # the website
    python3 onboard/tools/build_downloads.py --release --out DIR   # a GitHub release

Commit your onboard/ changes first: only committed files are packed.

The website (default) offers only the app: its download buttons link to the
latest GitHub release. This writes the line under them in `index.html`
(between the downloads:begin/end markers): the version and the commit it was
built from. No files are put on the website.

A release (--release, run by the app workflow) gets these, which are NOT
committed to the repository (they would grow its history every time):

    uav-silm-software-<version>.zip   the whole onboard folder: Ground Station,
                                      Pi software, ArduPilot files, docs, tests
    silm-oi-farm-model-v1.zip         the ready-made recognition model only
    gps_denied_failsafe.param         the example flight controller settings
    silm_gps_loss.lua                 the GPS-loss script
    SHA256SUMS.txt, downloads.json    checksums; names and sizes

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


RELEASE_KINDS = ("software", "model", "param", "lua")   # attached to each GitHub release


def build(out: str, commit: str, kinds=RELEASE_KINDS) -> list[dict]:
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
    if "software" in kinds:
        files.append(_software_zip(out, commit, name, version, source_note))
    if "model" in kinds:
        files.append(_model_zip(out, commit, url, when))
    if "param" in kinds:
        files.append(_write(out, "gps_denied_failsafe.param", blob(commit, "onboard/ardupilot/gps_denied_failsafe.param"),
                            "param", version, "ArduPlane parameters: no-RC operation and all failsafes ending in "
                            "the automatic landing. Load in Mission Planner or QGroundControl.", "MIT"))
    if "lua" in kinds:
        files.append(_write(out, "silm_gps_loss.lua", blob(commit, "onboard/ardupilot/scripts/silm_gps_loss.lua"),
                            "lua", version, "ArduPilot Lua script: GPS lost, jammed or spoofed - stop trusting "
                            "GPS and land on dead reckoning. Copy to APM/scripts on the SD card.", "MIT"))
    return _finish(out, files, version, commit, short, when)


def _software_zip(out, commit, name, version, source_note) -> dict:
    top = f"uav-silm-software-{version}/"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for p in tracked_files(commit):
            data = blob(commit, p)
            check_secret(p, data)
            add(z, top + p[len("onboard/"):], data, is_executable(commit, p))
        add(z, top + "SOURCE.txt", source_note)
    return _write(out, name, buf.getvalue(), "software", version,
                  "Ground Station (laptop), Raspberry Pi software, ArduPilot settings and "
                  "GPS-loss script, simulator tools, documentation and tests.", "MIT + third-party")


def _model_zip(out, commit, url, when) -> dict:
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
    return _write(out, name, buf.getvalue(), "model", "v1",
                  "The ready-made recognition model only (tractor, car, truck, livestock), with "
                  "labels and its description.", "AGPL-3.0")


def _finish(out, files, version, commit, short, when) -> list[dict]:
    keep = {f["name"] for f in files} | {"SHA256SUMS.txt", "downloads.json"}
    for old in os.listdir(out):                    # files from an older version
        if old not in keep and os.path.isfile(os.path.join(out, old)):
            os.remove(os.path.join(out, old))
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


SITE_BEGIN, SITE_END = "<!-- downloads:begin (written by onboard/tools/build_downloads.py) -->", "<!-- downloads:end -->"


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def site_meta(commit: str) -> dict:
    """What the website's version line needs."""
    return {"version": blob(commit, "onboard/VERSION").decode().strip(), "commit": commit,
            "commit_short": commit[:10], "date": git("log", "-1", "--format=%cs", commit).decode().strip()}


def site_block(meta: dict) -> str:
    """The line under the app's download buttons in index.html (between the markers)."""
    src = f"https://github.com/rometlehari-maker/uav-silm/tree/{meta['commit']}/onboard"
    rel = "https://github.com/rometlehari-maker/uav-silm/releases/latest"
    return (f"{SITE_BEGIN}\n"
            f'      <p class="note rv">Version {_esc(meta["version"])}, built {_esc(meta["date"])} from commit '
            f'<a href="{src}"><code>{_esc(meta["commit_short"])}</code></a>. '
            f'Checksums of every file: <a href="{rel}">SHA256SUMS.txt on the release page</a>.</p>\n'
            f"      {SITE_END}")


def update_site(index_html: str, meta: dict) -> bool:
    with open(index_html, encoding="utf-8") as fh:
        page = fh.read()
    a, b = page.find(SITE_BEGIN), page.find(SITE_END)
    if a < 0 or b < a:
        raise SystemExit(f"{index_html}: download markers not found")
    new = page[:a] + site_block(meta) + page[b + len(SITE_END):]
    if new != page:
        with open(index_html, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(new)
    return new != page


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=None, help="folder for the release files (with --release)")
    ap.add_argument("--commit", default=None,
                    help="git commit to pack (default: the last commit that changed onboard/)")
    ap.add_argument("--site", default=os.path.join(ROOT, "index.html"),
                    help="website page whose version line to update ('' to skip)")
    ap.add_argument("--release", action="store_true",
                    help="build every file for a GitHub release; doesn't touch the website")
    a = ap.parse_args(argv)
    # the handoff notes aren't packed, so a commit that only changes them doesn't count
    commit = a.commit or git("log", "-1", "--format=%H", "HEAD", "--", "onboard",
                             ":(exclude)onboard/HANDOFF.md").decode().strip()
    commit = git("rev-parse", commit).decode().strip()
    if git("status", "--porcelain", "--", "onboard").strip():
        print("note: onboard/ has uncommitted changes; they are NOT in the downloads "
              f"(packing commit {commit[:10]})", file=sys.stderr)
    if a.release:
        if not a.out:
            ap.error("--release needs --out DIR")
        for f in build(a.out, commit, RELEASE_KINDS):
            print(f"{f['name']:34s} {f['bytes'] / 1e6:6.2f} MB  {f['sha256']}")
    elif a.site:
        changed = update_site(a.site, site_meta(commit))
        print(f"{a.site}: {'updated' if changed else 'already up to date'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
