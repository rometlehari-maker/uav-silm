"""The aircraft's Raspberry Pi, from the laptop: over its Wi-Fi hotspot or a USB stick.

Wi-Fi: join the aircraft's Wi-Fi (SILM-...), then talk to the ground agent at
http://10.42.0.1:8080 (silm_recon/agent.py on the Pi).

USB stick: name the stick SILM. The Ground Station writes
    SILM_TRANSFER/to_aircraft/settings.yaml, mission.waypoints, project.json
plug the stick into the aircraft's Pi (it imports them within a few seconds and
writes import_result.json next to them), and after a flight the Pi copies each
flight to SILM_TRANSFER/from_aircraft/<flight>/, which the Ground Station imports.
"""

from __future__ import annotations

import json
import os
import shutil
import string
import time
import urllib.error
import urllib.request
import zipfile

TRANSFER_DIR = "SILM_TRANSFER"


class PiError(RuntimeError):
    pass


def _url(address: str, path: str) -> str:
    address = (address or "").strip().rstrip("/")
    if not address:
        raise PiError("enter the Pi's address (10.42.0.1 on its Wi-Fi)")
    if not address.startswith("http"):
        address = "http://" + address
    if ":" not in address.split("//", 1)[1]:
        address += ":8080"
    return address + path


def _request(address, path, data=None, timeout=8.0):
    url = _url(address, path)
    body = None if data is None else json.dumps(data).encode()
    req = urllib.request.Request(url, body, {"Content-Type": "application/json"} if body else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read())
        except ValueError:
            raise PiError(f"the Pi answered with error {e.code}") from e
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise PiError(f"no answer from {url} - is the laptop joined to the aircraft's Wi-Fi, "
                      f"and the aircraft powered? ({getattr(e, 'reason', e)})") from e


def status(address: str) -> dict:
    return _request(address, "/api/status")


def camera_jpg(address: str) -> bytes | None:
    """The camera's newest picture (None while the Pi has none yet)."""
    try:
        with urllib.request.urlopen(_url(address, "/api/camera.jpg"), timeout=6) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code == 503:
            return None
        raise PiError(f"the Pi answered with error {e.code}") from e
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise PiError(f"no answer from the Pi ({getattr(e, 'reason', e)})") from e


def calibration(address: str, action: str, body: dict | None = None) -> dict:
    """Lens calibration on the Pi: action = state | capture | compute | reset."""
    if action == "state":
        return _request(address, "/api/calibration")
    if action not in ("capture", "compute", "reset"):
        raise ValueError(f"unknown calibration action {action!r}")
    return _request(address, f"/api/calibration/{action}", body or {},
                    timeout=240 if action == "compute" else 15)


def preflight(address: str, alt_m: float, speed_mps: float) -> dict:
    """The full hardware check on the Pi (takes up to a few minutes)."""
    return _request(address, "/api/preflight", {"alt_m": alt_m, "speed_mps": speed_mps}, timeout=360)


def send_settings(address: str, settings_text: str, project: dict, mission_wpl: str | None) -> dict:
    payload = {"settings_yaml": settings_text, "project": project, "sender": "ground-station"}
    if mission_wpl:
        payload["mission_wpl"] = mission_wpl
    return _request(address, "/api/settings", payload, timeout=30)


def send_model(address: str, payload: dict) -> dict:
    """A trained model (+ labels) to the Pi: {name, tflite_b64, labels}."""
    return _request(address, "/api/model", payload, timeout=120)


def flights(address: str) -> list:
    return _request(address, "/api/flights").get("flights", [])


def recent_log(address: str) -> str:
    return _request(address, "/api/log", timeout=15).get("log", "")


def download_flight(address: str, name: str, library: str, progress=None) -> str:
    """Download one flight as a zip and unpack it into the library. Returns its folder."""
    url = _url(address, f"/api/flights/{name}.zip")
    os.makedirs(library, exist_ok=True)
    tmp = os.path.join(library, f".{name}.zip.part")
    try:
        with urllib.request.urlopen(url, timeout=60) as r, open(tmp, "wb") as fh:
            total = int(r.headers.get("Content-Length") or 0)
            got = 0
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                fh.write(chunk)
                got += len(chunk)
                if progress:
                    progress(got, total)
    except (urllib.error.URLError, OSError) as e:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise PiError(f"download of {name} failed: {e}") from e
    dest = os.path.join(library, name)
    _safe_unzip(tmp, library)
    os.remove(tmp)
    return dest


def _safe_unzip(zpath: str, dest: str) -> None:
    root = os.path.realpath(dest)
    with zipfile.ZipFile(zpath) as z:
        for info in z.infolist():
            target = os.path.realpath(os.path.join(dest, info.filename))
            if not target.startswith(root + os.sep):
                raise PiError(f"unsafe path in the zip: {info.filename}")
        z.extractall(dest)


# ------------------------------------------------------------------ USB stick

def candidate_drives() -> list[str]:
    """Places a USB stick may be mounted on this laptop (Windows, macOS, Linux)."""
    found = []
    if os.name == "nt":
        for letter in string.ascii_uppercase[3:]:
            p = f"{letter}:\\"
            if os.path.isdir(p):
                found.append(p)
    else:
        for base in ("/Volumes", "/media", f"/media/{os.environ.get('USER', '')}",
                     f"/run/media/{os.environ.get('USER', '')}", "/mnt"):
            try:
                for n in sorted(os.listdir(base)):
                    p = os.path.join(base, n)
                    if os.path.isdir(p) and os.access(p, os.W_OK):
                        found.append(p)
            except OSError:
                pass
    # sticks named SILM, or already used for SILM, first
    found.sort(key=lambda p: (os.path.basename(p.rstrip("\\/")).upper() != "SILM"
                              and not os.path.isdir(os.path.join(p, TRANSFER_DIR)), p))
    return found


def usb_write(stick: str, settings_text: str, project: dict, mission_wpl: str | None,
              extra_files: dict | None = None) -> dict:
    """extra_files: {relative path on the stick (under to_aircraft/): local file}."""
    if not stick or not os.path.isdir(stick):
        raise PiError(f"the USB stick folder {stick!r} does not exist - plug it in and pick it")
    d = os.path.join(stick, TRANSFER_DIR, "to_aircraft")
    os.makedirs(d, exist_ok=True)
    for name in ("import_result.json",):              # the old result is now stale
        p = os.path.join(d, name)
        if os.path.exists(p):
            os.remove(p)
    files = {"settings.yaml": settings_text, "project.json": json.dumps(project, indent=2)}
    if mission_wpl:
        files["mission.waypoints"] = mission_wpl
    for name, text in files.items():
        tmp = os.path.join(d, name + ".part")
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, os.path.join(d, name))
    for rel, src in (extra_files or {}).items():
        dst = os.path.join(d, *rel.split("/"))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src, dst + ".part")
        os.replace(dst + ".part", dst)
        files[rel] = ""
    with open(os.path.join(d, "README.txt"), "w", encoding="utf-8") as fh:
        fh.write("Plug this stick (named SILM) into the aircraft's Raspberry Pi, power it on and\n"
                 "wait 10 seconds. The Pi imports these settings and writes import_result.json\n"
                 "here. Then plug the stick back into the laptop to check the result.\n")
    return {"ok": True, "folder": d, "files": sorted(files),
            "message": f"written to {d}. Safely eject the stick, plug it into the aircraft's Pi, "
                       "power on and wait 10 s; then check the result here."}


def usb_read(stick: str) -> dict:
    """What the aircraft left on the stick: import result, its status, flights."""
    root = os.path.join(stick or "", TRANSFER_DIR)
    out = {"present": os.path.isdir(root), "import_result": None, "aircraft": None, "flights": []}
    if not out["present"]:
        return out
    for key, rel in (("import_result", ("to_aircraft", "import_result.json")),
                     ("aircraft", ("aircraft_status.json",))):
        p = os.path.join(root, *rel)
        if os.path.isfile(p):
            try:
                out[key] = json.load(open(p, encoding="utf-8"))
            except ValueError:
                pass
    fa = os.path.join(root, "from_aircraft")
    if os.path.isdir(fa):
        out["flights"] = sorted((n for n in os.listdir(fa)
                                 if os.path.isdir(os.path.join(fa, n)) and n.startswith("mission_")),
                                reverse=True)
    return out


def usb_import_flight(stick: str, name: str, library: str) -> str:
    src = os.path.join(stick, TRANSFER_DIR, "from_aircraft", name)
    if not (name.startswith("mission_") and os.path.isdir(src)) or os.sep in name or "/" in name:
        raise PiError(f"no flight {name!r} on the stick")
    dest = os.path.join(library, name)
    os.makedirs(library, exist_ok=True)
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    shutil.copytree(src, dest)
    return dest


def import_folder(folder: str, library: str) -> list[str]:
    """Import flight folders from anywhere (an SD card, a USB stick, a copied
    folder): the folder itself if it is a flight, or the flights inside it."""
    folder = os.path.abspath(folder)
    if not os.path.isdir(folder):
        raise PiError(f"{folder!r} is not a folder")

    def is_flight(p):
        return any(os.path.isfile(os.path.join(p, f)) for f in
                   ("detections.ndjson", "track.ndjson", "health.ndjson"))

    found = [folder] if is_flight(folder) else []
    if not found:
        for root, dirs, _ in os.walk(folder):
            for d in dirs:
                p = os.path.join(root, d)
                if is_flight(p):
                    found.append(p)
            if root.count(os.sep) - folder.count(os.sep) >= 3:
                dirs[:] = []
    if not found:
        raise PiError(f"no flights found in {folder} (a flight folder contains detections.ndjson)")
    os.makedirs(library, exist_ok=True)
    out = []
    for p in found:
        name = os.path.basename(p.rstrip(os.sep))
        if not name.startswith("mission_"):
            name = "mission_" + time.strftime("%Y%m%d_%H%M%S") + "_" + name
        dest = os.path.join(library, name)
        if os.path.abspath(dest) != p:
            if os.path.isdir(dest):
                shutil.rmtree(dest)
            shutil.copytree(p, dest)
        out.append(name)
    return out
