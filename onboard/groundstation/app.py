#!/usr/bin/env python3
"""SILM Ground Station - the laptop program.

    Windows:        double-click  "Start Ground Station.bat"
    macOS / Linux:  ./start_ground_station.sh
    or:             python -m groundstation          (from the onboard folder)

It opens in your web browser (http://127.0.0.1:8765) but runs only on this
computer. Tabs: Start, Aircraft, Mission, Camera, Send to aircraft, Launch,
Simulator, Flights & pictures.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import sys
import threading
import time
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ONBOARD = os.path.dirname(HERE)
sys.path.insert(0, ONBOARD)
sys.path.insert(0, os.path.join(ONBOARD, "planner"))

import yaml  # noqa: E402

import survey  # noqa: E402
from groundstation import fc, flights, pilink, training  # noqa: E402
from groundstation import project as P  # noqa: E402
from groundstation.sim import SimRunner, commands_for  # noqa: E402
from silm_recon import mavmission as mm  # noqa: E402

STATIC = os.path.join(HERE, "static")
SAFE_NAME = re.compile(r"[^A-Za-z0-9 _\-]+")


def software_version() -> str:
    try:
        return open(os.path.join(ONBOARD, "VERSION")).read().strip()
    except OSError:
        return "?"


def camera_from_config(path: str = P.BASE_CONFIG) -> dict:
    """The planner's camera model from config.yaml (detection region, lens)."""
    try:
        cfg = yaml.safe_load(open(path)) or {}
    except OSError:
        cfg = {}
    cam, g, det = cfg.get("camera", {}), cfg.get("georef", {}), cfg.get("detector", {})
    c = {"width": int(cam.get("width", 1920)), "height": int(cam.get("height", 1080)),
         "lens": (g.get("calibration") or {}).get("model") or g.get("lens", "fisheye"),
         "hfov_deg": float(g.get("hfov_deg", 160))}
    if det.get("roi"):
        c["roi"] = list(det["roi"])
    c["tiles"] = int(det.get("tile_grid", [2, 2])[0]) if det.get("tiled") else 1
    return c


class Station:
    """Everything the HTTP layer calls; testable without HTTP."""

    def __init__(self, library: str | None = None, projects: str | None = None,
                 sim: SimRunner | None = None):
        home = os.path.join(os.path.expanduser("~"), "SILM")
        self.library = library or os.environ.get("SILM_LIBRARY") or os.path.join(home, "flights")
        self.projects = projects or os.environ.get("SILM_PROJECTS") or os.path.join(home, "projects")
        self.training_root = os.environ.get("SILM_TRAINING") or os.path.join(
            os.path.dirname(self.projects), "training")
        os.makedirs(self.library, exist_ok=True)
        os.makedirs(self.projects, exist_ok=True)
        self.camera = camera_from_config()
        self.sim = sim or SimRunner()
        self.dataset = training.Dataset(self.training_root)
        self.trainer = training.Trainer(os.path.join(self.training_root, "models"),
                                        bundled_dirs=(os.path.join(ONBOARD, "models"),))

    # ---------------------------------------------------------- meta
    def meta(self) -> dict:
        return {"version": software_version(), "questions": P.QUESTIONS,
                "default_project": P.default_project(), "max_wind_mps": P.MAX_WIND_MPS,
                "target_sizes": P.TARGET_SIZES, "library": self.library,
                "projects_dir": self.projects, "sim": self.sim.available(),
                "models": self.trainer.list_models(), "training_classes": self.dataset.classes()}

    # ---------------------------------------------------------- setup wizard
    SETUP_STEPS = ("need", "aircraft", "mission", "fc_connect", "fc_setup", "bench",
                   "pi_install", "pi_connect", "pi_check", "sim")

    def _setup_file(self) -> str:
        return os.path.join(os.path.dirname(self.projects), "setup.json")

    def setup_state(self) -> dict:
        try:
            with open(self._setup_file(), encoding="utf-8") as fh:
                done = json.load(fh).get("done", {})
        except (OSError, ValueError):
            done = {}
        done = {k: v for k, v in done.items() if k in self.SETUP_STEPS}
        return {"steps": list(self.SETUP_STEPS), "done": done,
                "complete": all(done.get(k) for k in self.SETUP_STEPS)}

    def setup_mark(self, body: dict) -> dict:
        state = self.setup_state()
        done = state["done"]
        if body.get("reset"):
            done = {}
        else:
            step = body.get("step")
            if step not in self.SETUP_STEPS:
                return {"ok": False, "message": f"unknown setup step {step!r}"}
            if body.get("done", True):
                done[step] = time.strftime("%Y-%m-%d %H:%M")
            else:
                done.pop(step, None)
        tmp = self._setup_file() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"done": done}, fh, indent=2)
        os.replace(tmp, self._setup_file())
        return {"ok": True, **self.setup_state()}

    # ---------------------------------------------------------- training
    def train_state(self) -> dict:
        return {"dataset": self.dataset.name, "classes": self.dataset.classes(),
                "images": self.dataset.list_images(), "stats": self.dataset.stats(),
                "presets": {k: v["label"] for k, v in training.PRESETS.items()},
                "tools": training.tools_available(), "models": self.trainer.list_models(),
                "job": self.trainer.status()}

    def train_upload(self, files: list) -> dict:
        added, failed = [], []
        for f in files or []:
            try:
                added.append(self.dataset.add_image_b64(f.get("name", "picture.jpg"), f.get("data", "")))
            except (ValueError, TypeError) as exc:
                failed.append(str(exc))
            except Exception as exc:          # broken base64 etc.
                failed.append(f"{f.get('name')}: {exc}")
        return {"ok": bool(added) and not failed, "added": added, "failed": failed,
                "message": f"{len(added)} picture(s) added" + (
                    "; not added: " + "; ".join(failed) if failed else "")}

    # ---------------------------------------------------------- projects
    def _ppath(self, name: str) -> str:
        safe = SAFE_NAME.sub("", name or "").strip() or "unnamed"
        return os.path.join(self.projects, safe + ".json")

    def list_projects(self) -> list:
        out = []
        for f in sorted(os.listdir(self.projects)):
            if f.endswith(".json"):
                p = os.path.join(self.projects, f)
                out.append({"name": f[:-5], "saved": time.strftime(
                    "%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(p)))})
        return out

    def load_project(self, name: str) -> dict:
        with open(self._ppath(name), encoding="utf-8") as fh:
            return P.merge_defaults(json.load(fh))

    def save_project(self, project: dict) -> dict:
        p = self._ppath(project.get("name"))
        tmp = p + ".part"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(project, fh, indent=2)
        os.replace(tmp, p)
        return {"ok": True, "path": p, "name": os.path.basename(p)[:-5]}

    def delete_project(self, name: str) -> dict:
        p = self._ppath(name)
        if os.path.isfile(p):
            os.remove(p)
        return {"ok": True}

    # ---------------------------------------------------------- check / outputs
    def plan(self, project: dict) -> dict | None:
        m = project.get("mission") or {}
        if not m.get("launch") or len(m.get("area") or []) < 3:
            return None
        return survey.plan_from_dict(P.plan_body(project, self.camera))

    def check(self, project: dict, for_launch: bool = False) -> dict:
        project = P.merge_defaults(project)
        pr = P.check_answers(project, for_launch)
        errors, warnings, fields = pr.errors, pr.warnings, pr.fields
        plan, plan_out = None, None
        try:
            plan = self.plan(project)
        except (ValueError, KeyError, TypeError) as exc:
            errors.append(f"Mission: {exc}")
        if plan is None and not errors:
            warnings.append("Mission: set the launch point and draw the survey area on the map")
        elif plan is None:
            pass
        else:
            errors += [f"Mission: {e}" for e in plan["errors"]]
            warnings += [f"Mission: {w}" for w in plan["warnings"]
                         if "FENCE_RADIUS" not in w]     # the fence is set from the plan
            items = plan["items"]
            plan_out = {k: plan[k] for k in ("swath_m", "spacing_m", "turn_radius_m", "lines",
                                             "line_angle_deg", "distance_km", "flight_min",
                                             "battery_frac", "max_dist_m", "fence_needed_m",
                                             "glide_deg", "detection")}
            plan_out["items"] = [it.to_dict() for it in items]
            plan_out["path"] = [[it.lat, it.lon] for it in items[1:]
                                if it.command in (mm.NAV_TAKEOFF, mm.NAV_WAYPOINT, mm.NAV_LAND)]
            plan_out["approach_start"] = list(plan["approach_start"])
            hd = P.wind_landing_heading(project)
            if hd is not None:
                diff = abs((project["mission"].get("landing_heading_deg", 0) - hd + 180) % 360 - 180)
                if diff > 30:
                    warnings.append(f"The landing direction is {diff:.0f} degrees off the wind: land "
                                    f"INTO the wind (towards {hd:.0f} degrees) - use 'Land into "
                                    f"the wind' on the Mission tab")
        errors += [f"Aircraft computer settings: {e}" for e in P.check_settings_against_config(project)]
        mdl = (project.get("recognition") or {}).get("model", "standard")
        if mdl != "standard" and mdl not in {m["name"] for m in self.trainer.list_models()}:
            errors.append(f"The recognition model '{mdl}' is not on this laptop (Training tab)")
        return {"ok": not errors, "errors": errors, "warnings": warnings, "plan": plan_out,
                "fields": fields,
                "cg": P.cg_advice(project["aircraft"]),
                "params": P.make_params(project, plan) if plan else P.make_params(project)}

    def outputs(self, project: dict) -> dict:
        """Everything that goes to the aircraft (raises ValueError if not ready)."""
        project = P.merge_defaults(project)
        res = self.check(project)
        if not res["ok"]:
            raise ValueError("fix these first: " + "; ".join(res["errors"]))
        plan = self.plan(project)
        if plan is None:
            raise ValueError("set the launch point and draw the survey area on the Mission tab first")
        return {"project": project, "plan": plan,
                "params_text": P.param_file_text(project, plan),
                "mission_wpl": mm.to_wpl(plan["items"]),
                "settings_text": P.settings_yaml(project)}

    def file(self, project: dict, kind: str) -> tuple[str, str, str]:
        o = self.outputs(project)
        n = SAFE_NAME.sub("", project.get("name") or "mission").replace(" ", "_") or "mission"
        return {"params": (o["params_text"], f"{n}.param", "text/plain"),
                "mission": (o["mission_wpl"], f"{n}.waypoints", "text/plain"),
                "settings": (o["settings_text"], "settings.yaml", "text/yaml"),
                "project": (json.dumps(o["project"], indent=2), f"{n}.json", "application/json"),
                }[kind]

    # ---------------------------------------------------------- flight controller
    def fc_action(self, action: str, body: dict) -> dict:
        port = body.get("port") or ""
        baud = int(body.get("baud") or 115200)
        if action == "info":
            return {"ok": True, **fc.info(port, baud)}
        if action == "disarm":
            return fc.disarm(port, baud)
        if action == "arm":
            project = P.merge_defaults(body.get("project") or {})
            res = self.check(project, for_launch=True)
            if not res["ok"]:
                return {"ok": False, "message": "not arming: " + "; ".join(res["errors"])}
            if not body.get("checklist_done"):
                return {"ok": False, "message": "tick every item of the launch checklist first"}
            return fc.arm(port, baud)
        o = self.outputs(body.get("project") or {})
        if action == "params":
            params = P.read_param_text(o["params_text"])
            r = fc.send_params(port, params, baud)
            r["message"] = (f"{r['written']} parameters written and read back"
                            + (f"; FAILED: {', '.join(r['failed'])}" if r["failed"] else "")
                            + (f"; not on this flight controller: {', '.join(r['not_on_aircraft'])}"
                               if r["not_on_aircraft"] else "")
                            + ("; reboot the flight controller (unplug USB and battery) to apply"
                               if r["reboot_needed"] else ""))
            return r
        if action == "script":
            return fc.send_script(port, os.path.join(ONBOARD, "ardupilot", "scripts",
                                                     "silm_gps_loss.lua"), baud)
        if action == "mission":
            return fc.send_mission(port, o["plan"]["items"], baud, o["plan"]["fence_needed_m"])
        raise ValueError(f"unknown action {action}")

    # ---------------------------------------------------------- Pi
    def _model(self, project: dict) -> str | None:
        m = (project.get("recognition") or {}).get("model", "standard")
        return None if m == "standard" else m

    def pi_send(self, body: dict) -> dict:
        o = self.outputs(body.get("project") or {})
        model = self._model(o["project"])
        if model:        # the model first, so the new settings find it installed
            r = pilink.send_model(body.get("address"),
                                  training.model_payload(self.trainer.model_dir(model), model))
            if not r.get("ok"):
                return r
        r = pilink.send_settings(body.get("address"), o["settings_text"], o["project"],
                                 o["mission_wpl"])
        if model and r.get("ok"):
            r["message"] = f"model '{model}' and " + r["message"]
        return r

    def usb_write(self, body: dict) -> dict:
        o = self.outputs(body.get("project") or {})
        model = self._model(o["project"])
        files = None
        if model:
            base = os.path.join(self.trainer.model_dir(model), model)
            files = {f"models/{model}.tflite": base + ".tflite",
                     f"models/{model}_labels.txt": base + "_labels.txt"}
        return pilink.usb_write(body.get("path"), o["settings_text"], o["project"],
                                o["mission_wpl"], extra_files=files)

    # ---------------------------------------------------------- simulator
    def sim_start(self, body: dict) -> dict:
        o = self.outputs(body.get("project") or {})
        pr = o["project"]
        w = pr.get("wind") or {}
        launch = pr["mission"]["launch"]
        return self.sim.start(o["params_text"], o["mission_wpl"], (launch[0], launch[1], 0.0),
                              float(w.get("speed_mps") or 0), float(w.get("from_deg") or 0),
                              fault=body.get("fault", "jam"),
                              fault_s=float(body.get("fault_s") or 90),
                              trigger_s=float(body.get("trigger_s") or 150))


class Handler(BaseHTTPRequestHandler):
    station: Station = None
    server_version = "SILM-GroundStation"

    def log_message(self, fmt, *args):
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

    def _file(self, path):
        if not path or not os.path.isfile(path):
            return self._send(404, {"error": "not found"})
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        if path.endswith(".js"):
            ctype = "text/javascript"
        elif path.endswith(".woff2"):
            ctype = "font/woff2"
        with open(path, "rb") as fh:
            self._send(200, fh.read(), ctype)

    def _static(self, rel):
        base = os.path.realpath(STATIC)
        p = os.path.realpath(os.path.join(base, rel))
        if not p.startswith(base + os.sep):
            return self._send(404, {"error": "not found"})
        return self._file(p)

    def do_GET(self):
        st = self.station
        url = urllib.parse.urlparse(self.path)
        path, qs = url.path, urllib.parse.parse_qs(url.query)
        try:
            if path in ("/", "/index.html"):
                return self._static("index.html")
            if path.startswith("/static/"):
                return self._static(path[len("/static/"):])
            if path == "/api/meta":
                return self._send(200, st.meta())
            if path == "/api/projects":
                return self._send(200, st.list_projects())
            if path.startswith("/api/projects/"):
                return self._send(200, st.load_project(urllib.parse.unquote(path[14:])))
            if path == "/api/setup":
                return self._send(200, st.setup_state())
            if path == "/api/pi/camera.jpg":
                try:
                    jpg = pilink.camera_jpg((qs.get("address") or [""])[0])
                except pilink.PiError as exc:
                    return self._send(502, {"ok": False, "message": str(exc)})
                if jpg is None:
                    return self._send(503, {"ok": False, "message": "waiting for a picture from the camera"})
                return self._send(200, jpg, "image/jpeg")
            if path == "/api/ports":
                return self._send(200, fc.list_ports())
            if path == "/api/drives":
                return self._send(200, pilink.candidate_drives())
            if path == "/api/library":
                return self._send(200, flights.list_library(st.library))
            m = re.match(r"^/api/library/([^/]+)(?:/(img|export\.geojson|export\.csv))?$", path)
            if m:
                name, what = urllib.parse.unquote(m.group(1)), m.group(2)
                if what == "img":
                    return self._file(flights.image_path(st.library, name, (qs.get("p") or [""])[0]))
                fl = flights.load_flight(st.library, name)
                if what == "export.geojson":
                    return self._send(200, json.dumps(flights.to_geojson(fl), indent=1),
                                      "application/geo+json",
                                      {"Content-Disposition": f'attachment; filename="{name}.geojson"'})
                if what == "export.csv":
                    return self._send(200, flights.to_csv(fl), "text/csv",
                                      {"Content-Disposition": f'attachment; filename="{name}.csv"'})
                return self._send(200, fl)
            if path == "/api/train/state":
                return self._send(200, st.train_state())
            if path == "/api/train/status":
                return self._send(200, st.trainer.status())
            if path == "/api/train/img":
                try:
                    return self._file(st.dataset.image_path((qs.get("n") or [""])[0]))
                except FileNotFoundError:
                    return self._send(404, {"error": "not found"})
            if path == "/api/train/boxes":
                n = (qs.get("n") or [""])[0]
                st.dataset.image_path(n)
                return self._send(200, {"name": n, "boxes": st.dataset.get_boxes(n)})
            if path == "/api/sim/status":
                return self._send(200, st.sim.status())
            if path == "/api/sim/track.png":
                s = st.sim.status()
                return self._file(s.get("track_png"))
        except FileNotFoundError:
            return self._send(404, {"error": "not found"})
        except Exception as exc:
            return self._send(500, {"error": str(exc)})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        st = self.station
        try:
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return self._send(400, {"ok": False, "message": "bad JSON"})
        path = self.path.split("?", 1)[0]
        try:
            if path == "/api/check":
                return self._send(200, st.check(body.get("project") or {},
                                                bool(body.get("for_launch"))))
            if path == "/api/projects":
                return self._send(200, st.save_project(body.get("project") or {}))
            if path == "/api/projects/delete":
                return self._send(200, st.delete_project(body.get("name")))
            m = re.match(r"^/api/files/(params|mission|settings|project)$", path)
            if m:
                text, fname, ctype = st.file(body.get("project") or {}, m.group(1))
                return self._send(200, text, ctype,
                                  {"Content-Disposition": f'attachment; filename="{fname}"'})
            m = re.match(r"^/api/fc/(info|params|script|mission|arm|disarm)$", path)
            if m:
                return self._send(200, st.fc_action(m.group(1), body))
            if path == "/api/setup":
                return self._send(200, st.setup_mark(body))
            if path == "/api/pi/status":
                return self._send(200, pilink.status(body.get("address")))
            if path == "/api/pi/send":
                return self._send(200, st.pi_send(body))
            if path == "/api/pi/flights":
                return self._send(200, {"ok": True, "flights": pilink.flights(body.get("address"))})
            if path == "/api/pi/log":
                return self._send(200, {"ok": True, "log": pilink.recent_log(body.get("address"))})
            if path == "/api/pi/calibration":
                return self._send(200, pilink.calibration(body.get("address"), body.get("action", "state"),
                                                          {k: body[k] for k in ("cols", "rows", "square_mm", "fisheye")
                                                           if k in body}))
            if path == "/api/pi/preflight":
                pr = P.merge_defaults(body.get("project") or {})
                return self._send(200, pilink.preflight(body.get("address"), float(pr["mission"].get("alt_m") or 100),
                                                        float(pr["aircraft"].get("cruise_mps") or 19)))
            if path == "/api/pi/download":
                d = pilink.download_flight(body.get("address"), body.get("name"), st.library)
                return self._send(200, {"ok": True, "message": f"downloaded to {d}",
                                        "name": os.path.basename(d)})
            if path == "/api/usb/write":
                return self._send(200, st.usb_write(body))
            if path == "/api/usb/read":
                return self._send(200, {"ok": True, **pilink.usb_read(body.get("path"))})
            if path == "/api/usb/import":
                d = pilink.usb_import_flight(body.get("path"), body.get("name"), st.library)
                return self._send(200, {"ok": True, "name": os.path.basename(d),
                                        "message": f"imported to {d}"})
            if path == "/api/library/import_folder":
                names = pilink.import_folder(body.get("folder"), st.library)
                return self._send(200, {"ok": True, "names": names,
                                        "message": f"imported {len(names)} flight(s)"})
            if path == "/api/train/upload":
                return self._send(200, st.train_upload(body.get("files")))
            if path == "/api/train/class":
                return self._send(200, {"ok": True, "classes": st.dataset.add_class(body.get("name"))})
            if path == "/api/train/boxes":
                n = st.dataset.set_boxes(body.get("name"), body.get("boxes") or [])
                return self._send(200, {"ok": True, "boxes": n})
            if path == "/api/train/delete":
                st.dataset.delete_image(body.get("name"))
                return self._send(200, {"ok": True})
            if path == "/api/train/start":
                return self._send(200, st.trainer.start(st.dataset, body.get("preset", "quick"),
                                                        body.get("device") or "cpu"))
            if path == "/api/train/stop":
                return self._send(200, st.trainer.stop())
            if path == "/api/sim/start":
                return self._send(200, st.sim_start(body))
            if path == "/api/sim/stop":
                return self._send(200, st.sim.stop())
            if path == "/api/sim/commands":
                pr = P.merge_defaults(body.get("project") or {})
                w = pr["wind"]
                return self._send(200, {"ok": True, "text": commands_for(
                    SAFE_NAME.sub("", pr["name"]).replace(" ", "_"), pr["mission"]["launch"],
                    float(w.get("speed_mps") or 0), float(w.get("from_deg") or 0))})
        except FileNotFoundError as exc:
            return self._send(200, {"ok": False, "message": f"not found: {exc}"})
        except (fc.FCError, pilink.PiError, ValueError, KeyError, TypeError, OSError) as exc:
            return self._send(200, {"ok": False, "message": str(exc)})
        except Exception as exc:
            traceback.print_exc()
            return self._send(200, {"ok": False, "message": f"unexpected error: {exc}"})
        self._send(404, {"ok": False, "message": "not found"})


def make_server(port: int = 8765, host: str = "127.0.0.1", station: Station | None = None):
    h = type("BoundHandler", (Handler,), {"station": station or Station()})
    return ThreadingHTTPServer((host, port), h)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true", help="don't open the browser")
    ap.add_argument("--library", default=None, help="flight library folder (default ~/SILM/flights)")
    ap.add_argument("--projects", default=None, help="projects folder (default ~/SILM/projects)")
    a = ap.parse_args(argv)
    try:
        srv = make_server(a.port, station=Station(a.library, a.projects))
    except OSError:
        url = f"http://127.0.0.1:{a.port}/"
        print(f"The Ground Station already seems to be running: opening {url}")
        if not a.no_browser:
            import webbrowser
            webbrowser.open(url)
        return 0
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    print("=" * 64)
    print(f"  SILM Ground Station {software_version()} is running.")
    print(f"  It opens in your browser: {url}")
    print("  Keep this window open while you use it. Close it to stop.")
    print("=" * 64)
    if not a.no_browser:
        import webbrowser
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
