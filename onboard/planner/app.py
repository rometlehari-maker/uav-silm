#!/usr/bin/env python3
"""SILM mission planner - a map GUI on the laptop.

    python3 planner/app.py                      # then open http://127.0.0.1:8765
    python3 planner/app.py --port 8800 --config config.yaml

Draw the survey area, set the launch and landing points, pick the target
object and altitude; the planner builds a complete NO-RC mission (hand-launch
takeoff, survey lines, autoland) and checks it. Then download it as a
.waypoints file (opens in Mission Planner / QGroundControl too) or upload it
straight to the aircraft over USB.

Runs only on this computer (binds to 127.0.0.1). Standard library only, plus
pymavlink for uploading. The map tiles come from the internet (OpenStreetMap,
Esri imagery), so plan with a connection; the rest works offline.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

import yaml  # noqa: E402

import survey  # noqa: E402
from silm_recon import mavmission as mm  # noqa: E402

# Typical size of each class's SMALLER side seen from above, in metres. Rough
# values for planning the altitude - measure your own objects if it matters.
TARGET_SIZES = {
    "person": 0.5, "car": 1.8, "truck": 2.5, "tractor": 2.0, "combine": 3.5,
    "harvester": 3.0, "baler": 2.0, "hay_bale": 1.5, "livestock": 0.8, "silo": 4.0,
    "barn": 10.0, "logging_truck": 2.5, "timber_stack": 3.0, "fire": 2.0, "smoke": 10.0,
    "deer": 0.5,
}


def read_params(path: str) -> dict:
    out = {}
    try:
        for line in open(path):
            line = line.split("#", 1)[0].strip()
            if "," in line:
                k, v = line.split(",", 1)
                out[k.strip()] = float(v)
    except OSError:
        pass
    return out


def load_defaults(config_path: str, param_path: str, classes_path: str) -> dict:
    d = survey.defaults_dict()
    try:
        cfg = yaml.safe_load(open(config_path)) or {}
    except OSError:
        cfg = {}
    cam, g, det = cfg.get("camera", {}), cfg.get("georef", {}), cfg.get("detector", {})
    calib = g.get("calibration") or {}
    c = d["camera"]
    c["width"], c["height"] = int(cam.get("width", c["width"])), int(cam.get("height", c["height"]))
    c["lens"] = calib.get("model") or g.get("lens", c["lens"])
    c["hfov_deg"] = float(g.get("hfov_deg", c["hfov_deg"]))
    if calib:
        c["fx"] = float(calib["camera_matrix"][0][0]) * c["width"] / float(calib["width"])
    if det.get("roi"):
        c["roi"] = list(det["roi"])
    c["tiles"] = int(det.get("tile_grid", [2, 2])[0]) if det.get("tiled") else 1
    prm = read_params(param_path)
    d["fence_radius_m"] = prm.get("FENCE_RADIUS", d["fence_radius_m"])
    d["alt_limit_m"] = prm.get("FENCE_ALT_MAX", d["alt_limit_m"])
    d["speed_mps"] = prm.get("AIRSPEED_CRUISE", d["speed_mps"])
    try:
        names = (yaml.safe_load(open(classes_path)) or {}).get("names", {})
        names = [names[i] for i in sorted(names)] if isinstance(names, dict) else list(names)
    except OSError:
        names = list(TARGET_SIZES)
    for n in TARGET_SIZES:
        if n not in names:
            names.append(n)
    targets = [{"name": n, "size_m": TARGET_SIZES.get(n)} for n in names]
    return {"plan": d, "targets": targets, "targets_in_config": cfg.get("targets", [])}


def plan_json(body: dict) -> dict:
    r = survey.plan_from_dict(body)
    items = r.pop("items")
    r["items"] = [it.to_dict() for it in items]
    r["path"] = [[it.lat, it.lon] for it in items[1:] if it.command in (mm.NAV_TAKEOFF, mm.NAV_WAYPOINT, mm.NAV_LAND)]
    r["approach_start"] = list(r["approach_start"])
    r["waypoints_text"] = mm.to_wpl(items)
    return r


def upload_to_aircraft(body: dict) -> dict:
    """Plan, refuse if the mission has errors, upload, read back, compare."""
    from pymavlink import mavutil
    r = survey.plan_from_dict(body["plan"])
    if r["errors"]:
        return {"ok": False, "message": "mission has errors: " + "; ".join(r["errors"])}
    items = r["items"]
    port, baud = body.get("port") or "", int(body.get("baud") or 115200)
    if not port:
        return {"ok": False, "message": "choose the aircraft's serial port (USB)"}
    try:
        m = mavutil.mavlink_connection(port, baud=baud, source_system=255)
        if not m.wait_heartbeat(timeout=10):
            return {"ok": False, "message": f"no heartbeat on {port} - is the flight controller connected?"}
        if m.motors_armed():
            return {"ok": False, "message": "the aircraft is ARMED - disarm before uploading"}
        mm.upload(m, items)
        back = mm.download(m)
        same = len(back) == len(items) and all(
            a.command == b.command and abs(a.lat - b.lat) < 1e-6 and abs(a.lon - b.lon) < 1e-6
            and abs(a.alt - b.alt) < 0.05 for a, b in zip(items[1:], back[1:]))
        msg = [f"uploaded {len(items) - 1} items and read them back: "
               + ("identical" if same else "MISMATCH - upload again")]
        if body.get("set_fence"):
            ok = mm.set_param(m, "FENCE_RADIUS", float(r["fence_needed_m"]))
            msg.append(f"FENCE_RADIUS = {r['fence_needed_m']:.0f} m " + ("set" if ok else "NOT confirmed"))
        fr = mm.get_param(m, "FENCE_RADIUS")
        if fr is not None and fr < r["fence_needed_m"]:
            msg.append(f"WARNING: aircraft FENCE_RADIUS is {fr:.0f} m, mission needs {r['fence_needed_m']:.0f} m")
        m.close()
        return {"ok": same, "message": ". ".join(msg)}
    except Exception as exc:
        return {"ok": False, "message": f"upload failed: {exc}"}


def arm_aircraft(body: dict) -> dict:
    """Arm (in AUTO, ready for the throw) or disarm, over USB. Arming first
    checks that the mission on the aircraft is a valid no-RC mission."""
    from pymavlink import mavutil
    action = body.get("action")
    if action not in ("arm", "disarm"):
        return {"ok": False, "message": "action must be arm or disarm"}
    port, baud = body.get("port") or "", int(body.get("baud") or 115200)
    if not port:
        return {"ok": False, "message": "choose the aircraft's serial port (USB)"}
    try:
        m = mavutil.mavlink_connection(port, baud=baud, source_system=255)
        if not m.wait_heartbeat(timeout=10):
            return {"ok": False, "message": f"no heartbeat on {port}"}
        if action == "disarm":
            ok, msg = mm.arm_disarm(m, False)
            m.close()
            return {"ok": ok, "message": msg}
        items = mm.download(m)
        errors, _ = mm.check_mission(items, mm.get_param(m, "FENCE_RADIUS") or None)
        if errors:
            m.close()
            return {"ok": False, "message": "not arming - mission on the aircraft: " + "; ".join(errors)}
        if not mm.set_mode(m, mm.MODE_AUTO):
            m.close()
            return {"ok": False, "message": "could not switch to AUTO"}
        ok, msg = mm.arm_disarm(m, True)
        m.close()
        if ok:
            msg = ("ARMED in AUTO. The motor starts after the throw. Unplug USB, hold it level "
                   "into the wind, keep hands clear of the propeller, and throw firmly.")
        return {"ok": ok, "message": msg}
    except Exception as exc:
        return {"ok": False, "message": f"{action} failed: {exc}"}


def list_ports() -> list:
    try:
        from serial.tools import list_ports as lp
        return [{"device": p.device, "description": p.description} for p in lp.comports()]
    except Exception:
        return []


class Handler(BaseHTTPRequestHandler):
    defaults: dict = {}

    def log_message(self, fmt, *args):  # quiet
        pass

    def _send(self, code, body, ctype="application/json", extra=None):
        data = body if isinstance(body, bytes) else (
            json.dumps(body).encode() if ctype == "application/json" else body.encode())
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            return self._static("index.html")
        if path.startswith("/static/"):
            return self._static(path[len("/static/"):])
        if path == "/api/defaults":
            return self._send(200, self.defaults)
        if path == "/api/ports":
            return self._send(200, list_ports())
        self._send(404, {"error": "not found"})

    def _static(self, name):
        base = os.path.realpath(os.path.join(HERE, "static"))
        p = os.path.realpath(os.path.join(base, name))
        if not p.startswith(base + os.sep) or not os.path.isfile(p):
            return self._send(404, {"error": "not found"})
        ctype = {".html": "text/html; charset=utf-8", ".js": "text/javascript",
                 ".css": "text/css"}.get(os.path.splitext(p)[1], "application/octet-stream")
        with open(p, "rb") as fh:
            self._send(200, fh.read(), ctype)

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return self._send(400, {"error": "bad JSON"})
        try:
            if self.path == "/api/plan":
                return self._send(200, plan_json(body))
            if self.path == "/api/waypoints":
                text = plan_json(body)["waypoints_text"]
                return self._send(200, text, "text/plain; charset=utf-8",
                                  {"Content-Disposition": 'attachment; filename="silm_mission.waypoints"'})
            if self.path == "/api/upload":
                return self._send(200, upload_to_aircraft(body))
            if self.path == "/api/arm":
                return self._send(200, arm_aircraft(body))
        except (ValueError, KeyError, TypeError) as exc:
            return self._send(400, {"error": str(exc)})
        self._send(404, {"error": "not found"})


def make_server(port=8765, config=None, params=None, classes=None):
    Handler.defaults = load_defaults(
        config or os.path.join(ROOT, "config.yaml"),
        params or os.path.join(ROOT, "ardupilot", "gps_denied_failsafe.param"),
        classes or os.path.join(ROOT, "training", "classes.yaml"))
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--config", default=None, help="onboard config.yaml (camera, detection region)")
    ap.add_argument("--params", default=None, help="ArduPilot .param file (fence, speeds)")
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    srv = make_server(a.port, a.config, a.params)
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    print(f"SILM mission planner running at {url}  (Ctrl-C to stop)")
    if not a.no_browser:
        import webbrowser
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
