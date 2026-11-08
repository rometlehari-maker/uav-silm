"""Ground agent on the aircraft's Raspberry Pi 4: settings in, flights out.

Runs as its own service (systemd/silm-agent.service), next to the flight
program (silm-recon.service). It never talks to the flight controller and
never touches the flight program's camera or telemetry; it only moves files.

Two ways to reach it from the laptop Ground Station:

  Wi-Fi  The Pi runs its own hotspot on the ground (pi/install.sh sets it up).
         Join it from the laptop; the Ground Station talks to
         http://10.42.0.1:8080:
           GET  /api/status            aircraft + Pi state, software version
           GET  /api/settings          the settings file now on the Pi
           POST /api/settings          new settings (+ project, mission)
           GET  /api/flights           list of recorded flights
           GET  /api/flights/<name>.zip  one flight (photos + logs), zipped
           GET  /api/log               the flight program's recent log
           GET  /api/camera.jpg        the camera's newest picture (on the ground)
           GET  /api/calibration       lens calibration: pictures taken so far
           POST /api/calibration/capture|compute|reset   lens calibration
           POST /api/preflight         the full hardware check (tools/preflight.py)
         Wi-Fi is switched OFF while the aircraft is armed or airborne, so
         nothing transmits in flight, and back on once it has been disarmed
         on the ground for agent.wifi_back_on_after_s.

  USB    A USB stick labelled SILM (mounted at agent.usb_mount). The Ground
         Station writes  SILM_TRANSFER/to_aircraft/settings.yaml  (+ project,
         mission); the agent imports it once. Finished flights are copied to
         SILM_TRANSFER/from_aircraft/<flight>/ for the Ground Station to read.

Safety: nothing is accepted, imported or switched on while the aircraft is
armed or airborne (from the flight program's status file). Settings are
validated (config.validate_config) before they are written; a rejected file
changes nothing.

    python3 -m silm_recon.agent --config config.yaml
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import yaml

from .config import deep_merge, load_config, settings_path, validate_config
from .status import armed_or_unknown_flying, read_status, status_path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

TRANSFER_DIR = "SILM_TRANSFER"
TO_AIRCRAFT = "to_aircraft"
FROM_AIRCRAFT = "from_aircraft"
FLIGHT_RE = re.compile(r"^mission_\d{8}_\d{6}$")
MAX_SETTINGS_BYTES = 2_000_000
MAX_MODEL_BYTES = 60_000_000

RESTART_CMD = ["sudo", "-n", "systemctl", "restart", "silm-recon"]
WIFI_CMD = ["sudo", "-n", "nmcli", "radio", "wifi"]          # + on | off
LOG_CMD = ["journalctl", "-u", "silm-recon", "-n", "150", "--no-pager", "-o", "short-iso"]
STOP_CMD = ["sudo", "-n", "systemctl", "stop", "silm-recon"]
START_CMD = ["sudo", "-n", "systemctl", "start", "silm-recon"]
PREVIEW_MAX_AGE_S = 5.0


def software_version() -> str:
    try:
        with open(os.path.join(ROOT, "VERSION")) as fh:
            return fh.read().strip()
    except OSError:
        return "unknown"


def _run(cmd, timeout=15) -> tuple[bool, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0, (r.stdout + r.stderr).strip()
    except Exception as exc:
        return False, str(exc)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _atomic_write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=os.path.dirname(path) or ".")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


class Agent:
    def __init__(self, config_path: str, settings: str | None = None,
                 restart_cmd=None, wifi_cmd=None, log_cmd=None, runner=_run,
                 tool_runner=_run):
        self.config_path = os.path.abspath(config_path)
        self._settings_override = settings
        self.base_cfg = load_config(self.config_path, settings="/nonexistent/silm.yaml")
        self.cfg = self._merged()
        a = self.base_cfg.get("agent") or {}
        self.port = int(a.get("port", 8080))
        self.wifi_off_when_armed = bool(a.get("wifi_off_when_armed", True))
        self.wifi_back_on_after_s = float(a.get("wifi_back_on_after_s", 20))
        self.usb_mount = os.path.expanduser(a.get("usb_mount", "/mnt/silm_usb"))
        # Only touch the (auto)mount point when a stick labelled SILM is plugged
        # in: looking at an automount point with no stick stalls for seconds.
        self.usb_device = a.get("usb_device", "/dev/disk/by-label/SILM")
        self.copy_flights_to_usb = bool(a.get("copy_flights_to_usb", True))
        self.restart_cmd = restart_cmd or RESTART_CMD
        self.wifi_cmd = wifi_cmd or WIFI_CMD
        self.log_cmd = log_cmd or LOG_CMD
        self._run = runner
        self._run_tool = tool_runner          # long jobs (preflight); real by default
        self.settings_file = settings_path(self.base_cfg, settings)
        self.settings_dir = os.path.dirname(self.settings_file)
        self.status_file = status_path(self.base_cfg)
        self.wifi_state = "on"          # what we last set ("on" at boot: the hotspot starts)
        self._last_flying = 0.0
        self.events: list[str] = []
        self._lock = threading.Lock()

    # ------------------------------------------------------------ helpers

    def _merged(self) -> dict:
        try:
            return load_config(self.config_path, self._settings_override)
        except Exception:
            return self.base_cfg

    def log(self, msg: str) -> None:
        line = time.strftime("%H:%M:%S ") + msg
        print("[agent] " + msg, flush=True)
        self.events = (self.events + [line])[-50:]

    def output_dir(self) -> str:
        return os.path.expanduser((self._merged().get("storage") or {}).get(
            "output_dir", "~/silm_captures"))

    def aircraft_status(self) -> dict:
        return read_status(self.status_file)

    def flying(self) -> bool:
        return armed_or_unknown_flying(self.aircraft_status())

    # ------------------------------------------------------------ status

    def status(self) -> dict:
        st = self.aircraft_status()
        sp = self.settings_file
        free = None
        try:
            os.makedirs(self.output_dir(), exist_ok=True)
            free = round(shutil.disk_usage(self.output_dir()).free / 1e6)
        except OSError:
            pass
        info = {}
        try:
            with open(os.path.join(self.settings_dir, "settings_info.json")) as fh:
                info = json.load(fh)
        except (OSError, ValueError):
            pass
        return {
            "ok": True, "hostname": socket.gethostname(), "software": software_version(),
            "aircraft": st, "flying": armed_or_unknown_flying(st),
            "program_running": bool(st.get("fresh")),
            "settings_file": sp, "settings_present": os.path.isfile(sp),
            "settings_info": info,
            "free_mb": free, "flights": len(self.list_flights()),
            "usb_stick": self.usb_present(), "wifi": self.wifi_state,
            "models": self.list_models(),
            "events": self.events[-15:],
        }

    # ------------------------------------------------------------ settings

    def current_settings_text(self) -> str:
        try:
            with open(self.settings_file, encoding="utf-8") as fh:
                return fh.read()
        except OSError:
            return ""

    def accept_settings(self, payload: dict, source: str = "wifi") -> dict:
        """Validate and install new settings. payload: settings_yaml (text,
        required), project (dict, optional), mission_wpl (text, optional)."""
        with self._lock:
            if self.flying():
                return {"ok": False, "message": "the aircraft is ARMED or flying - settings can "
                        "only be changed on the ground, disarmed"}
            text = payload.get("settings_yaml")
            if not isinstance(text, str) or not text.strip():
                return {"ok": False, "message": "no settings in the request"}
            if len(text) > MAX_SETTINGS_BYTES:
                return {"ok": False, "message": "settings file too large"}
            try:
                over = yaml.safe_load(text)
            except yaml.YAMLError as exc:
                return {"ok": False, "message": f"settings are not valid YAML: {exc}"}
            if not isinstance(over, dict):
                return {"ok": False, "message": "settings must be a mapping (key: value)"}
            errors = validate_config(deep_merge(self.base_cfg, over))
            if errors:
                return {"ok": False, "message": "settings rejected (nothing changed): "
                        + "; ".join(errors), "errors": errors}
            mission = payload.get("mission_wpl")
            if mission is not None and (not isinstance(mission, str)
                                        or not mission.startswith("QGC WPL")):
                return {"ok": False, "message": "mission is not a QGC WPL .waypoints file"}
            project = payload.get("project")
            if project is not None and not isinstance(project, dict):
                return {"ok": False, "message": "project must be a JSON object"}

            os.makedirs(self.settings_dir, exist_ok=True)
            old = self.current_settings_text()
            if old:
                _atomic_write(os.path.join(self.settings_dir, "settings.prev.yaml"), old)
            _atomic_write(self.settings_file, text)
            if mission:
                _atomic_write(os.path.join(self.settings_dir, "mission.waypoints"), mission)
            if project:
                _atomic_write(os.path.join(self.settings_dir, "project.json"),
                              json.dumps(project, indent=2))
            info = {"received": time.strftime("%Y-%m-%d %H:%M:%S"), "source": source,
                    "sha": _sha(text), "project": (project or {}).get("name"),
                    "sender": payload.get("sender")}
            _atomic_write(os.path.join(self.settings_dir, "settings_info.json"), json.dumps(info))
            ok, out = self._run(self.restart_cmd)
            self.log(f"settings installed from {source} ({info['sha']}); flight program "
                     + ("restarted" if ok else f"NOT restarted: {out}"))
            msg = "settings installed and checked"
            msg += "; the flight program restarted with them" if ok else (
                f"; could not restart the flight program ({out}) - power-cycle the aircraft")
            return {"ok": True, "restarted": ok, "message": msg, "sha": info["sha"]}

    # ------------------------------------------------------------ models

    def models_dir(self) -> str:
        return os.path.join(self.settings_dir, "models")

    def install_model(self, name: str, tflite: bytes, labels: str, source: str = "wifi") -> dict:
        """Store a trained recognition model (+ its labels) on the Pi. The
        settings then point detector.model at it (sent with the settings)."""
        with self._lock:
            if self.flying():
                return {"ok": False, "message": "the aircraft is ARMED or flying - models can "
                        "only be changed on the ground, disarmed"}
            n = re.sub(r"[^A-Za-z0-9_\-]+", "_", name or "").strip("_")
            if not n:
                return {"ok": False, "message": "the model needs a name"}
            if len(tflite) < 1000 or tflite[4:8] != b"TFL3":
                return {"ok": False, "message": "that is not a TensorFlow Lite model (.tflite)"}
            if len(tflite) > MAX_MODEL_BYTES:
                return {"ok": False, "message": "model too large for the Pi"}
            names = [ln.strip() for ln in (labels or "").splitlines() if ln.strip()]
            if not names:
                return {"ok": False, "message": "the model's labels (object names) are missing"}
            d = self.models_dir()
            os.makedirs(d, exist_ok=True)
            tmp = os.path.join(d, f".{n}.tflite.part")
            with open(tmp, "wb") as fh:
                fh.write(tflite)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, os.path.join(d, n + ".tflite"))
            _atomic_write(os.path.join(d, n + "_labels.txt"), "\n".join(names) + "\n")
            self.log(f"model {n} installed from {source} ({len(tflite) // 1000} kB, "
                     f"{len(names)} object types)")
            return {"ok": True, "name": n, "path": os.path.join(d, n + ".tflite"),
                    "message": f"model '{n}' installed ({len(names)} object types)"}

    def list_models(self) -> list[str]:
        try:
            return sorted(f[:-7] for f in os.listdir(self.models_dir()) if f.endswith(".tflite"))
        except OSError:
            return []

    # ------------------------------------------------------------ flights

    def list_flights(self) -> list[dict]:
        base = self.output_dir()
        out = []
        try:
            names = sorted(os.listdir(base), reverse=True)
        except OSError:
            return out
        for n in names:
            d = os.path.join(base, n)
            if not (FLIGHT_RE.match(n) and os.path.isdir(d)):
                continue
            size, files, jpgs = 0, 0, 0
            for root, _, fs in os.walk(d):
                for f in fs:
                    files += 1
                    jpgs += f.lower().endswith(".jpg")
                    try:
                        size += os.path.getsize(os.path.join(root, f))
                    except OSError:
                        pass
            dets = 0
            try:
                with open(os.path.join(d, "detections.ndjson"), encoding="utf-8") as fh:
                    dets = sum(1 for ln in fh if ln.strip())
            except OSError:
                pass
            out.append({"name": n, "pictures": jpgs, "detections": dets, "files": files,
                        "size_mb": round(size / 1e6, 2), "empty": jpgs == 0 and dets == 0})
        return out

    def flight_dir(self, name: str) -> str | None:
        if not FLIGHT_RE.match(name or ""):
            return None
        d = os.path.join(self.output_dir(), name)
        return d if os.path.isdir(d) else None

    def zip_flight(self, name: str, fh) -> int:
        """Write flight `name` as a zip to file object fh. Returns bytes of files."""
        d = self.flight_dir(name)
        if d is None:
            raise FileNotFoundError(name)
        total = 0
        with zipfile.ZipFile(fh, "w", zipfile.ZIP_STORED) as z:   # JPEGs don't compress
            for root, _, fs in os.walk(d):
                for f in sorted(fs):
                    p = os.path.join(root, f)
                    arc = os.path.join(name, os.path.relpath(p, d))
                    z.write(p, arc)
                    total += os.path.getsize(p)
        return total

    def recent_log(self) -> str:
        ok, out = self._run(self.log_cmd, timeout=10)
        return out if ok else f"(log not available: {out})"

    # ------------------------------------------------------------ Wi-Fi

    def wifi_tick(self, now: float | None = None) -> None:
        if not self.wifi_off_when_armed:
            return
        now = time.time() if now is None else now
        if self.flying():
            self._last_flying = now
            if self.wifi_state != "off":
                ok, out = self._run(self.wifi_cmd + ["off"])
                self.wifi_state = "off" if ok else self.wifi_state
                self.log("aircraft armed/airborne: Wi-Fi " + ("OFF" if ok else f"off FAILED: {out}"))
        elif self.wifi_state == "off" and now - self._last_flying >= self.wifi_back_on_after_s:
            ok, out = self._run(self.wifi_cmd + ["on"])
            self.wifi_state = "on" if ok else self.wifi_state
            self.log("disarmed on the ground: Wi-Fi " + ("ON again" if ok else f"on FAILED: {out}"))

    # ------------------------------------------------------------ USB stick

    def usb_root(self) -> str:
        return os.path.join(self.usb_mount, TRANSFER_DIR)

    def usb_present(self) -> bool:
        if self.usb_device and not os.path.exists(self.usb_device):
            return False
        return os.path.isdir(self.usb_mount) and (
            os.path.ismount(self.usb_mount) or os.path.isdir(self.usb_root()))

    def _usb_status_file(self, data: dict) -> None:
        try:
            _atomic_write(os.path.join(self.usb_root(), "aircraft_status.json"),
                          json.dumps(data, indent=2))
        except OSError:
            pass

    def usb_tick(self) -> dict:
        """Import new settings from the stick, copy flights to it. Returns
        what happened (for logs and tests)."""
        done = {"imported": False, "copied": [], "skipped": None}
        if not self.usb_present():
            return done
        if self.flying():
            done["skipped"] = "flying"
            return done
        root = self.usb_root()
        # trained models first, so settings that use one find it installed
        mdir = os.path.join(root, TO_AIRCRAFT, "models")
        if os.path.isdir(mdir):
            done["models"] = []
            for f in sorted(os.listdir(mdir)):
                if not f.endswith(".tflite"):
                    continue
                n = f[:-7]
                data = open(os.path.join(mdir, f), "rb").read()
                lp = os.path.join(mdir, n + "_labels.txt")
                mark = os.path.join(self.models_dir(), f".{n}.sha")
                sha = hashlib.sha256(data).hexdigest()[:16]
                if os.path.isfile(mark) and open(mark).read().strip() == sha:
                    continue
                res = self.install_model(n, data, open(lp, encoding="utf-8").read()
                                         if os.path.isfile(lp) else "", source="usb")
                if res["ok"]:
                    _atomic_write(mark, sha)
                    done["models"].append(n)
                else:
                    self.log(f"USB model {n} rejected: {res['message']}")
        src = os.path.join(root, TO_AIRCRAFT, "settings.yaml")
        if os.path.isfile(src):
            text = open(src, encoding="utf-8").read()
            marker = os.path.join(self.settings_dir, ".usb_imported")
            last = open(marker).read().strip() if os.path.isfile(marker) else ""
            if _sha(text) != last:
                payload = {"settings_yaml": text, "sender": "usb"}
                mp = os.path.join(root, TO_AIRCRAFT, "mission.waypoints")
                pp = os.path.join(root, TO_AIRCRAFT, "project.json")
                if os.path.isfile(mp):
                    payload["mission_wpl"] = open(mp, encoding="utf-8").read()
                if os.path.isfile(pp):
                    try:
                        payload["project"] = json.load(open(pp, encoding="utf-8"))
                    except ValueError:
                        pass
                res = self.accept_settings(payload, source="usb")
                result = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), **res}
                try:
                    _atomic_write(os.path.join(root, TO_AIRCRAFT, "import_result.json"),
                                  json.dumps(result, indent=2))
                except OSError:
                    pass
                if res["ok"]:
                    os.makedirs(self.settings_dir, exist_ok=True)
                    _atomic_write(marker, _sha(text))
                    done["imported"] = True
                else:
                    self.log("USB settings rejected: " + res["message"])
                    _atomic_write(marker, _sha(text))   # don't retry the same bad file forever
        if self.copy_flights_to_usb:
            current = self.aircraft_status().get("mission_dir")
            for f in self.list_flights():
                if f["empty"]:
                    continue
                if self._sync_flight(f["name"], current):
                    done["copied"].append(f["name"])
            if done["copied"]:
                self.log("copied to USB stick: " + ", ".join(done["copied"]))
        self._usb_status_file({"time": time.strftime("%Y-%m-%d %H:%M:%S"),
                               "hostname": socket.gethostname(), "software": software_version(),
                               "flights": self.list_flights(),
                               "settings_info": self.status().get("settings_info")})
        try:
            os.sync()
        except AttributeError:
            pass
        return done

    def _sync_flight(self, name: str, current_dir: str | None) -> bool:
        """Copy new/changed files of one flight to the stick. True if anything copied."""
        src = self.flight_dir(name)
        dst = os.path.join(self.usb_root(), FROM_AIRCRAFT, name)
        copied = False
        for root, _, fs in os.walk(src):
            for f in fs:
                s = os.path.join(root, f)
                t = os.path.join(dst, os.path.relpath(s, src))
                try:
                    if os.path.isfile(t) and os.path.getsize(t) == os.path.getsize(s):
                        continue
                    os.makedirs(os.path.dirname(t), exist_ok=True)
                    shutil.copyfile(s, t + ".part")
                    os.replace(t + ".part", t)
                    copied = True
                except OSError as exc:
                    self.log(f"USB copy failed for {name}/{f}: {exc}")
                    return copied
        return copied

    # ------------------------------------------------------------ camera, lens, checks

    def _status_dir(self) -> str:
        return os.path.dirname(self.status_file) or "."

    def preview_jpg(self) -> bytes | None:
        """The camera's newest picture. Asking for it makes the flight program
        save one every second for the next 15 s (only on the ground)."""
        try:
            os.makedirs(self._status_dir(), exist_ok=True)
            with open(os.path.join(self._status_dir(), "preview_request"), "w") as fh:
                fh.write(str(time.time()))
        except OSError:
            pass
        p = os.path.join(self._status_dir(), "preview.jpg")
        try:
            if time.time() - os.path.getmtime(p) > PREVIEW_MAX_AGE_S:
                return None
            with open(p, "rb") as fh:
                return fh.read()
        except OSError:
            return None

    def calib_dir(self) -> str:
        return os.path.join(self.settings_dir, "calibration")

    def calib_state(self) -> dict:
        d = self.calib_dir()
        shots = sorted(f for f in os.listdir(d) if f.endswith(".jpg")) if os.path.isdir(d) else []
        return {"ok": True, "shots": len(shots)}

    def calib_reset(self) -> dict:
        shutil.rmtree(self.calib_dir(), ignore_errors=True)
        return {"ok": True, "shots": 0, "message": "calibration pictures deleted"}

    def calib_capture(self, cols: int, rows: int) -> dict:
        """Keep the newest camera picture if the whole checkerboard is in it."""
        if self.flying():
            return {"ok": False, "message": "the aircraft is ARMED or flying"}
        jpg = self.preview_jpg()
        if jpg is None:
            return {"ok": False, "message": "no picture from the camera yet (is it on and connected?)"}
        import numpy as np
        import cv2
        cc = _calib_tool()
        img = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
        if img is None or cc.find_corners(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), cols, rows) is None:
            return {**self.calib_state(), "ok": False, "found": False,
                    "message": f"the checkerboard ({cols} x {rows} inner corners) is not fully in the picture"}
        os.makedirs(self.calib_dir(), exist_ok=True)
        n = self.calib_state()["shots"] + 1
        with open(os.path.join(self.calib_dir(), f"shot_{n:03d}.jpg"), "wb") as fh:
            fh.write(jpg)
        return {"ok": True, "found": True, "shots": n, "message": f"picture {n} kept"}

    def calib_compute(self, cols: int, rows: int, square_mm: float, fisheye: bool = True) -> dict:
        import cv2
        cc = _calib_tool()
        d = self.calib_dir()
        files = sorted(os.path.join(d, f) for f in os.listdir(d) if f.endswith(".jpg")) if os.path.isdir(d) else []
        imgs = [im for im in (cv2.imread(f) for f in files) if im is not None]
        try:
            cal = cc.calibrate(imgs, cols, rows, square_mm / 1000.0, fisheye=fisheye)
        except Exception as exc:
            return {"ok": False, "message": f"calibration failed: {exc}"}
        quality = "good" if cal["rms_px"] < 0.5 else "ok" if cal["rms_px"] < 1.0 else "poor"
        block = {k: cal[k] for k in ("model", "width", "height", "camera_matrix", "dist_coeffs")}
        return {"ok": True, "calibration": block, "rms_px": cal["rms_px"], "quality": quality,
                "views_used": cal["views_used"], "hfov_deg": cal["hfov_deg"],
                "message": f"error {cal['rms_px']} px ({quality}), {cal['views_used']} pictures used, "
                           f"field of view {cal['hfov_deg']} deg"}

    def preflight(self, alt_m: float = 100.0, speed_mps: float = 19.0) -> dict:
        """Run tools/preflight.py. The flight program is paused meanwhile (two
        programs can't share the flight controller's serial port)."""
        if self.flying():
            return {"ok": False, "message": "the aircraft is ARMED or flying"}
        with self._lock:
            out = os.path.join(tempfile.gettempdir(), f"silm-preflight-{os.getpid()}.json")
            self._run(STOP_CMD)
            try:
                ok, text = self._run_tool([sys.executable, os.path.join(ROOT, "tools", "preflight.py"),
                                           "--config", self.config_path, "--settings", self.settings_file,
                                           "--alt-m", str(float(alt_m)), "--speed-mps", str(float(speed_mps)),
                                           "--json", out], timeout=300)
            finally:
                self._run(START_CMD)
            try:
                with open(out, encoding="utf-8") as fh:
                    res = json.load(fh)
                os.remove(out)
            except (OSError, ValueError):
                return {"ok": False, "message": "the check did not finish: " + text[-400:]}
        self.log(f"preflight: {res['fails']} FAIL, {res['warns']} WARN")
        return {"ok": True, **res,
                "message": f"{res['fails']} FAIL, {res['warns']} WARN" + (" - fix the FAILs before flying" if res["fails"] else "")}

    # ------------------------------------------------------------ loop

    def tick(self) -> None:
        try:
            self.wifi_tick()
        except Exception as exc:
            self.log(f"Wi-Fi control error: {exc}")
        try:
            self.usb_tick()
        except Exception as exc:
            self.log(f"USB stick error: {exc}")


# ---------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    agent: Agent = None  # set by make_server
    server_version = "SILM-agent"

    def log_message(self, fmt, *args):
        pass

    def _json(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        a = self.agent
        path = self.path.split("?", 1)[0]
        try:
            if path == "/api/status":
                return self._json(200, a.status())
            if path == "/api/settings":
                return self._json(200, {"settings_yaml": a.current_settings_text(),
                                        "path": a.settings_file})
            if path == "/api/flights":
                return self._json(200, {"flights": a.list_flights()})
            if path == "/api/log":
                return self._json(200, {"log": a.recent_log()})
            if path == "/api/calibration":
                return self._json(200, a.calib_state())
            if path == "/api/camera.jpg":
                jpg = a.preview_jpg()
                if jpg is None:
                    return self._json(503, {"ok": False, "message": "waiting for a picture from the camera"})
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(jpg)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(jpg)
                return
            m = re.match(r"^/api/flights/([^/]+)\.zip$", path)
            if m:
                if a.flight_dir(m.group(1)) is None:
                    return self._json(404, {"error": "no such flight"})
                with tempfile.TemporaryFile() as tmp:
                    a.zip_flight(m.group(1), tmp)
                    n = tmp.tell()
                    tmp.seek(0)
                    self.send_response(200)
                    self.send_header("Content-Type", "application/zip")
                    self.send_header("Content-Length", str(n))
                    self.send_header("Content-Disposition", f'attachment; filename="{m.group(1)}.zip"')
                    self.end_headers()
                    shutil.copyfileobj(tmp, self.wfile, 1 << 20)
                return
        except Exception as exc:
            return self._json(500, {"error": str(exc)})
        self._json(404, {"error": "not found"})

    def do_POST(self):
        a = self.agent
        path = self.path.split("?", 1)[0]
        if path not in ("/api/settings", "/api/model", "/api/preflight", "/api/calibration/capture",
                        "/api/calibration/compute", "/api/calibration/reset"):
            return self._json(404, {"error": "not found"})
        limit = MAX_MODEL_BYTES * 2 if path == "/api/model" else MAX_SETTINGS_BYTES * 3
        try:
            n = int(self.headers.get("Content-Length", 0))
            if n > limit:
                return self._json(413, {"ok": False, "message": "request too large"})
            body = json.loads(self.rfile.read(n) or b"{}")
            if not isinstance(body, dict):
                body = {}
        except Exception:
            return self._json(400, {"ok": False, "message": "bad JSON"})
        try:
            if path == "/api/preflight":
                return self._json(200, a.preflight(body.get("alt_m", 100), body.get("speed_mps", 19)))
            if path.startswith("/api/calibration/"):
                cols, rows = int(body.get("cols", 9)), int(body.get("rows", 6))
                act = path.rsplit("/", 1)[1]
                res = (a.calib_capture(cols, rows) if act == "capture" else a.calib_reset() if act == "reset"
                       else a.calib_compute(cols, rows, float(body.get("square_mm", 35)),
                                            bool(body.get("fisheye", True))))
                return self._json(200, res)
        except Exception as exc:
            return self._json(500, {"ok": False, "message": str(exc)})
        if path == "/api/model":
            try:
                import base64
                data = base64.b64decode(body.get("tflite_b64") or "", validate=True)
            except Exception:
                return self._json(400, {"ok": False, "message": "the model data is damaged"})
            res = a.install_model(body.get("name"), data, body.get("labels") or "")
        else:
            res = a.accept_settings(body, source="wifi")
        self._json(200 if res["ok"] else 409 if "ARMED" in res["message"] else 400, res)


def _calib_tool():
    """tools/calibrate_camera.py as a module (checkerboard finding, calibration)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("silm_calibrate_camera",
                                                  os.path.join(ROOT, "tools", "calibrate_camera.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def make_server(agent: Agent, host="0.0.0.0", port=None) -> ThreadingHTTPServer:
    h = type("BoundHandler", (Handler,), {"agent": agent})
    return ThreadingHTTPServer((host, agent.port if port is None else port), h)


def main() -> int:
    ap = argparse.ArgumentParser(description="SILM ground agent (Pi 4): settings in, flights out")
    ap.add_argument("--config", default=os.path.join(ROOT, "config.yaml"))
    ap.add_argument("--settings", default=None)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--tick-s", type=float, default=2.0)
    args = ap.parse_args()
    agent = Agent(args.config, args.settings)
    srv = make_server(agent, args.host)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    agent.log(f"listening on {args.host}:{srv.server_address[1]} (software {software_version()})")
    try:
        while True:
            agent.tick()
            time.sleep(args.tick_s)
    except KeyboardInterrupt:
        pass
    srv.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
