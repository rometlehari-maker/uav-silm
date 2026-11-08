"""The Ground Station project: every answer about the aircraft, the mission,
the wind, the camera and the transfer, in one JSON-able dict.

    QUESTIONS        every editable value, with the question shown above it in
                     the GUI, help text, unit and limits (the GUI's forms are
                     built from this list; the backend checks with it too)
    default_project  a new project with sensible starting answers
    validate         (errors, warnings): errors block sending and launching
    plan_body        the mission planner's input (planner/survey.py)
    make_params      flight-controller parameters from the aircraft answers
    param_file_text  the complete .param file for this aircraft
    make_settings    the Pi's settings.yaml (only what differs from config.yaml)
"""

from __future__ import annotations

import copy
import math
import os
import re

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
ONBOARD = os.path.dirname(HERE)
BASE_PARAMS = os.path.join(ONBOARD, "ardupilot", "gps_denied_failsafe.param")
BASE_CONFIG = os.path.join(ONBOARD, "config.yaml")

# Blind (GPS-denied) landings were flown in the simulator up to this wind and
# landed inside the planned margin; above it they did not (SITL_HOWTO.md E1).
MAX_WIND_MPS = 10.0

# Typical size of each class's SMALLER side seen from above, in metres.
TARGET_SIZES = {
    "person": 0.5, "car": 1.8, "truck": 2.5, "bus": 2.5, "tractor": 2.0, "combine": 3.5,
    "harvester": 3.0, "baler": 2.0, "hay_bale": 1.5, "livestock": 0.8, "cow": 0.8,
    "horse": 0.6, "sheep": 0.5, "silo": 4.0, "barn": 10.0, "logging_truck": 2.5,
    "timber_stack": 3.0, "boat": 1.5, "deer": 0.5,
}
# Classes the bundled COCO model can see (the rest need a trained model).
COCO_TARGETS = ["person", "car", "truck", "bus", "boat", "cow", "horse", "sheep"]
FARM_TARGETS = ["tractor", "combine", "harvester", "baler", "hay_bale", "livestock", "silo",
                "barn", "logging_truck", "timber_stack", "deer"]

LAUNCH_TYPES = {
    "hand": "Thrown by hand",
    "bungee": "Bungee or catapult",
    "runway": "Takes off from a runway on wheels",
}
LANDING_TYPES = {"belly": "Belly landing (on the fuselage)", "wheels": "On its wheels"}


def Q(id, q, help="", type="number", unit="", min=None, max=None, step=None, options=None,
      required=True, group="", tab="", open=False):
    """open=True: more choices are added at run time (your trained models and
    their object types), so any simple name is accepted."""
    return {"id": id, "q": q, "help": help, "type": type, "unit": unit, "min": min, "max": max,
            "step": step, "options": options, "required": required, "group": group, "tab": tab,
            "open": open}


QUESTIONS = [
    # ------------------------------------------------------------- aircraft
    Q("aircraft.name", "What do you call this aircraft?", "Any name; it appears on the "
      "aircraft's files and in the flight list.", "text", tab="aircraft", group="The aircraft"),
    Q("aircraft.wingspan_m", "What is the wingspan?", "Tip to tip.", unit="m",
      min=0.3, max=6, step=0.05, tab="aircraft", group="The aircraft"),
    Q("aircraft.weight_g", "How much does it weigh, ready to fly with the battery?",
      "Weigh it on a kitchen scale with everything installed.", unit="g", min=100, max=25000,
      step=10, tab="aircraft", group="The aircraft"),
    Q("aircraft.motor", "Where is the propeller?", "", "select",
      options={"pusher": "Behind the wing (pusher)", "tractor": "In the nose (tractor)"},
      tab="aircraft", group="The aircraft"),
    Q("aircraft.chord_mm", "How deep is the wing, front edge to back edge?",
      "The wing chord. For a tapered wing, measure the average (or use the mean "
      "aerodynamic chord from your design).", unit="mm", min=40, max=1000, step=1,
      tab="aircraft", group="Balance"),
    Q("aircraft.cg_mm", "What is the center of gravity of your aircraft?",
      "Where it balances, measured from the wing's FRONT edge, with the battery in. "
      "Balance it on two fingertips under the wings. Most aircraft fly well with the "
      "centre of gravity at 25-33 % of the wing depth.", unit="mm behind the front edge",
      min=0, max=1000, step=1, tab="aircraft", group="Balance"),
    Q("aircraft.battery_cells", "How many cells does the battery have?",
      "The 'S' number on the battery: a 4S battery has 4 cells (14.8 V).", unit="cells (S)",
      min=2, max=12, step=1, tab="aircraft", group="Battery"),
    Q("aircraft.battery_mah", "What is the battery's capacity?", "Printed on the battery.",
      unit="mAh", min=500, max=50000, step=100, tab="aircraft", group="Battery"),
    Q("aircraft.endurance_min", "How long can it fly on a full battery?",
      "Your best measured or calculated flight time. The planner keeps a 25 % reserve.",
      unit="minutes", min=5, max=600, step=5, tab="aircraft", group="Battery"),
    Q("aircraft.stall_mps", "At what speed does it stall?",
      "The slowest speed it can fly level. The minimum airspeed is set 30 % above it.",
      unit="m/s", min=4, max=40, step=0.5, tab="aircraft", group="Speeds"),
    Q("aircraft.cruise_mps", "At what speed should it fly the survey?",
      "Its efficient cruising speed.", unit="m/s", min=6, max=60, step=0.5,
      tab="aircraft", group="Speeds"),
    Q("aircraft.max_mps", "What is the fastest it may fly?", "Keep a margin below the "
      "speed at which it gets twitchy or flutters.", unit="m/s", min=8, max=80, step=0.5,
      tab="aircraft", group="Speeds"),
    Q("aircraft.max_bank_deg", "How steeply may it bank in turns?",
      "45 degrees is a good default; less keeps the camera steadier but makes wider turns.",
      unit="degrees", min=15, max=60, step=5, tab="aircraft", group="Speeds"),
    Q("aircraft.has_airspeed_sensor", "Does it have a working airspeed sensor (pitot tube)?",
      "Required: without GPS the aircraft navigates on airspeed. The flight controller must "
      "show a sensible airspeed in Mission Planner when you blow gently into the tube.",
      "bool", tab="aircraft", group="Sensors"),
    Q("aircraft.launch", "How is it launched?", "", "select", options=LAUNCH_TYPES,
      tab="aircraft", group="Launch and landing"),
    Q("aircraft.landing", "How does it land?", "", "select", options=LANDING_TYPES,
      tab="aircraft", group="Launch and landing"),
    # ------------------------------------------------------------- mission
    Q("mission.target", "What is the smallest thing it should find?",
      "Sets the highest altitude at which it is still big enough in the picture.",
      "select", options={k: f"{k} (~{v} m)" for k, v in TARGET_SIZES.items()},
      tab="mission", group="Survey"),
    Q("mission.alt_m", "How high should it fly the survey?", "Above the launch point.",
      unit="m", min=15, max=400, step=5, tab="mission", group="Survey"),
    Q("mission.overlap_pct", "How much should neighbouring survey lines overlap?",
      "More overlap means fewer missed objects but a longer flight.", unit="%",
      min=0, max=80, step=5, tab="mission", group="Survey"),
    Q("mission.line_angle_deg", "Which direction should the survey lines run?",
      "Leave empty to run them along the area's longest side.", unit="degrees from north",
      min=0, max=179, step=1, required=False, tab="mission", group="Survey"),
    Q("mission.takeoff_alt_m", "How high should it climb after launch?", unit="m",
      min=20, max=120, step=5, tab="mission", group="Takeoff and landing"),
    Q("mission.approach_len_m", "How long should the straight final approach be?",
      "200 m starting 25 m up is the tested default: in the simulator, a shorter, "
      "steeper approach landed closer to the planned point without GPS in strong wind. "
      "The approach must cross nothing tall.", unit="m",
      min=100, max=1000, step=25, tab="mission", group="Takeoff and landing"),
    Q("mission.approach_alt_m", "How high should it start the final approach?", unit="m",
      min=10, max=80, step=5, tab="mission", group="Takeoff and landing"),
    Q("mission.alt_limit_m", "What is the highest it is allowed to fly?",
      "Your legal ceiling (EASA open category: 120 m). Also the geofence ceiling.",
      unit="m", min=30, max=500, step=10, tab="mission", group="Limits"),
    # ------------------------------------------------------------- wind
    Q("wind.speed_mps", "How strong is the wind at the field today?",
      f"Enter the GUSTS (the strongest wind), from the forecast or a wind meter. Above "
      f"{MAX_WIND_MPS:g} m/s the aircraft must not be launched: in the simulator a landing "
      "without GPS then missed the planned point by more than 150 m.", unit="m/s",
      min=0, max=30, step=0.5, required=False, tab="mission", group="Wind today"),
    Q("wind.from_deg", "Where is the wind coming FROM?",
      "Compass direction: 0 = from the north, 90 = from the east. The landing is "
      "planned into the wind.", unit="degrees", min=0, max=359, step=5, required=False,
      tab="mission", group="Wind today"),
    # ------------------------------------------------------------- recognition
    Q("recognition.model", "Which recognition model should the aircraft use?",
      "The standard model knows people and vehicles. Models you trained on the Training "
      "tab know your own objects (tractor, combine ...).", "select",
      options={"standard": "Standard model (people, cars, trucks ...)"}, open=True,
      tab="camera", group="What to find"),
    Q("recognition.targets", "What should it look for?",
      "It photographs these. The standard model knows people and vehicles; farm and "
      "forestry objects need your trained model.", "multi",
      options={**{k: k for k in COCO_TARGETS}, **{k: f"{k} (trained model)" for k in FARM_TARGETS}},
      open=True, tab="camera", group="What to find"),
    Q("recognition.fire", "Should it look for fire and smoke?",
      "A colour-based check that works without training. It also flags sunsets and "
      "red roofs, so check its pictures.", "bool", tab="camera", group="What to find"),
    Q("recognition.sensitivity", "How sure must it be before it saves a picture?",
      "Lower finds more but also saves more mistakes.", unit="% confidence",
      min=10, max=95, step=5, tab="camera", group="What to find"),
    Q("recognition.min_alt_m", "From what height should it start taking pictures?",
      "Below this it saves nothing (launch, landing).", unit="m", min=5, max=200, step=5,
      tab="camera", group="Pictures"),
    Q("recognition.save_all_frames", "Should it also save ordinary pictures every few seconds?",
      "Useful for checking the area and for training your own model. Uses more space.",
      "bool", tab="camera", group="Pictures"),
    Q("recognition.frame_interval_s", "How often should it save an ordinary picture?",
      unit="seconds", min=0.5, max=60, step=0.5, tab="camera", group="Pictures"),
    Q("camera.url", "What is the camera's video address?",
      "SIYI A2 mini default: rtsp://192.168.144.25:8554/main.264", "text",
      tab="camera", group="Camera"),
    Q("camera.gimbal", "Does the camera have a gimbal the Pi should point straight down?",
      "Yes for the SIYI A2 mini.", "bool", tab="camera", group="Camera"),
    Q("link.pi_serial", "How is the Pi wired to the flight controller?",
      "/dev/serial0 = the Pi's pins to a flight controller TELEM port; /dev/ttyACM0 = USB.",
      "select", options={"/dev/serial0": "Pi pins (UART) to a TELEM port",
                         "/dev/ttyACM0": "USB cable", "/dev/ttyUSB0": "USB-serial adapter"},
      tab="camera", group="Connections"),
    Q("link.fc_serial_for_pi", "Which flight-controller port are the Pi's wires connected to?",
      "The TELEM port the Pi's UART wires go to (see your board's manual: on most boards "
      "TELEM2 is SERIAL2). The Ground Station sets that port for the Pi. Not used when the "
      "Pi is connected by USB.", "select",
      options={"1": "SERIAL1 (TELEM1)", "2": "SERIAL2 (TELEM2)", "3": "SERIAL3",
               "4": "SERIAL4", "5": "SERIAL5", "6": "SERIAL6", "7": "SERIAL7"},
      tab="camera", group="Connections"),
    Q("link.pi_baud", "At what speed does that connection run?",
      "Must match SERIALx_BAUD on the flight controller (115 = 115200).", "select",
      options={"57600": "57600", "115200": "115200", "921600": "921600"},
      tab="camera", group="Connections"),
    Q("program.mode", "What should the Pi do when it is switched on?", "", "select",
      options={"flight": "Fly: take pictures only in the air (normal)",
               "bench": "Bench test: take pictures whenever it sees something"},
      tab="camera", group="Connections"),
    # ------------------------------------------------------------- transfer
    Q("link.fc_port", "Which USB port is the flight controller plugged into?",
      "Windows: COM3, COM4 ...; Linux: /dev/ttyACM0. Pick it from the list.", "text",
      required=False, tab="send", group="Flight controller (USB cable)"),
    Q("link.pi_address", "What is the Pi's address on its Wi-Fi?",
      "10.42.0.1 when the laptop is joined to the aircraft's own Wi-Fi.", "text",
      tab="send", group="Aircraft computer (Wi-Fi)"),
    Q("link.usb_path", "Where is the USB stick on this laptop?",
      "The stick must be named SILM. Windows: e.g. E:\\  macOS: /Volumes/SILM", "text",
      required=False, tab="send", group="Aircraft computer (USB stick)"),
]
QUESTION_IDS = {q["id"] for q in QUESTIONS}


def default_project() -> dict:
    return {
        "version": 2,
        "name": "New mission",
        "aircraft": {
            "name": "SILM", "wingspan_m": 1.4, "weight_g": 1600, "motor": "pusher",
            "chord_mm": 220, "cg_mm": None, "battery_cells": 4, "battery_mah": 7000,
            "endurance_min": 100, "stall_mps": 9.5, "cruise_mps": 19, "max_mps": 28,
            "max_bank_deg": 45, "has_airspeed_sensor": True, "launch": "hand",
            "landing": "belly",
        },
        "mission": {
            "launch": None, "area": [], "landing": None, "landing_heading_deg": 0,
            "target": "car", "alt_m": 100, "overlap_pct": 20, "line_angle_deg": None,
            "takeoff_alt_m": 50, "approach_len_m": 200, "approach_alt_m": 25,
            "alt_limit_m": 120,
        },
        "wind": {"speed_mps": None, "from_deg": None},
        "recognition": {
            "model": "standard",
            "targets": ["person", "car", "truck"], "fire": True, "sensitivity": 45,
            "min_alt_m": 20, "save_all_frames": False, "frame_interval_s": 2,
        },
        "camera": {"url": "rtsp://192.168.144.25:8554/main.264", "gimbal": True},
        "program": {"mode": "flight"},
        "link": {"fc_port": "", "fc_baud": 115200, "pi_serial": "/dev/serial0",
                 "fc_serial_for_pi": "2", "pi_baud": "115200", "pi_address": "10.42.0.1", "usb_path": ""},
    }


def get(project: dict, qid: str):
    sec, key = qid.split(".", 1)
    return (project.get(sec) or {}).get(key)


def merge_defaults(project: dict) -> dict:
    """A loaded project with any newer questions filled from the defaults."""
    out = default_project()
    for sec, val in (project or {}).items():
        if isinstance(val, dict) and isinstance(out.get(sec), dict):
            out[sec].update(val)
        else:
            out[sec] = val
    return out


# ------------------------------------------------------------- checks

def cg_advice(a: dict) -> dict | None:
    """Centre of gravity as % of the wing depth, and a verdict."""
    cg, chord = a.get("cg_mm"), a.get("chord_mm")
    if cg is None or not chord:
        return None
    pct = 100.0 * float(cg) / float(chord)
    lo, hi = 0.25 * chord, 0.33 * chord
    if 25 <= pct <= 33:
        verdict = "good"
    elif 18 <= pct <= 38:
        verdict = "check"
    else:
        verdict = "bad"
    return {"pct": round(pct, 1), "verdict": verdict,
            "recommended_mm": [round(lo), round(hi)]}


class _Problems:
    """Errors and warnings, each remembering the question it belongs to (so
    the GUI can highlight that input)."""

    def __init__(self):
        self.errors, self.warnings, self.fields = [], [], {}

    def err(self, msg, qid=None):
        self.errors.append(msg)
        if qid and qid not in self.fields:
            self.fields[qid] = msg

    def warn(self, msg, qid=None):
        self.warnings.append(msg)


def _check_question(q, v, pr: _Problems):
    def e(msg):
        pr.err(msg, q["id"])
    if v is None or v == "":
        if q["required"]:
            e(f"Not answered: {q['q']}")
        return
    if q["type"] == "number":
        if isinstance(v, bool) or not isinstance(v, (int, float)) or math.isnan(v):
            e(f"{q['q']} - '{v}' is not a number")
            return
        if q["min"] is not None and v < q["min"]:
            e(f"{q['q']} - {v:g} {q['unit']} is below the minimum {q['min']:g}")
        if q["max"] is not None and v > q["max"]:
            e(f"{q['q']} - {v:g} {q['unit']} is above the maximum {q['max']:g}")
    elif q["type"] == "select" and str(v) not in q["options"] and not (
            q.get("open") and re.fullmatch(r"[A-Za-z0-9_\-]{1,80}", str(v))):
        e(f"{q['q']} - '{v}' is not one of the choices")
    elif q["type"] == "multi":
        def ok(x):
            return x in q["options"] or (q.get("open") and isinstance(x, str)
                                         and re.fullmatch(r"[a-z0-9_ ]{1,40}", x))
        if not isinstance(v, list) or not all(ok(x) for x in v):
            e(f"{q['q']} - unknown choice in {v}")
    elif q["type"] == "bool" and not isinstance(v, bool):
        e(f"{q['q']} - must be yes or no")
    elif q["type"] == "text" and not isinstance(v, str):
        e(f"{q['q']} - must be text")


def check_answers(project: dict, for_launch: bool = False) -> _Problems:
    """All problems with the answers (the mission geometry is checked by the planner)."""
    pr = _Problems()
    for q in QUESTIONS:
        _check_question(q, get(project, q["id"]), pr)
    a = project.get("aircraft", {})
    try:
        if a["cruise_mps"] < 1.3 * a["stall_mps"]:
            pr.err(f"The survey speed {a['cruise_mps']:g} m/s is too close to the stall "
                   f"speed {a['stall_mps']:g} m/s: it must be at least "
                   f"{1.3 * a['stall_mps']:.1f} m/s", "aircraft.cruise_mps")
        if a["max_mps"] < a["cruise_mps"] + 3:
            pr.err(f"The fastest speed {a['max_mps']:g} m/s must be at least 3 m/s above "
                   f"the survey speed {a['cruise_mps']:g} m/s", "aircraft.max_mps")
    except (KeyError, TypeError):
        pass
    if a.get("has_airspeed_sensor") is False:
        pr.err("An airspeed sensor is required: without GPS the aircraft navigates on "
               "airspeed, and the GPS-loss landing depends on it", "aircraft.has_airspeed_sensor")
    cg = cg_advice(a)
    if cg:
        lo, hi = cg["recommended_mm"]
        if cg["verdict"] == "bad":
            pr.err(f"The centre of gravity is at {cg['pct']:.0f} % of the wing depth: "
                   f"far outside the usual 25-33 % ({lo}-{hi} mm). Re-measure; it may "
                   f"not be controllable", "aircraft.cg_mm")
        elif cg["verdict"] == "check":
            pr.warn(f"The centre of gravity is at {cg['pct']:.0f} % of the wing depth; "
                    f"25-33 % ({lo}-{hi} mm) is usual. Check it against your design", "aircraft.cg_mm")
    if a.get("launch") == "runway":
        pr.warn("Runway takeoff with no pilot needs ground steering set up and tested "
                "(GROUND_STEER_ALT, steering servo); prefer a hand or bungee launch", "aircraft.launch")
    r = project.get("recognition", {})
    if not r.get("targets") and not r.get("fire"):
        pr.warn("Nothing to look for: no objects chosen and fire detection is off", "recognition.targets")
    farm = [t for t in r.get("targets") or [] if t in FARM_TARGETS]
    if farm and r.get("model", "standard") == "standard":
        pr.warn(f"{', '.join(farm)} need your trained model (the standard model "
                f"can't see them) - see the 'Teach new objects' tab", "recognition.targets")
    m = project.get("mission", {})
    if (m.get("alt_m") or 0) > (m.get("alt_limit_m") or 1e9):
        pr.err(f"The survey height {m['alt_m']:g} m is above the {m['alt_limit_m']:g} m limit",
               "mission.alt_m")
    if (r.get("min_alt_m") or 0) >= (m.get("alt_m") or 1e9):
        pr.err("Pictures start above the survey height: it would never take any",
               "recognition.min_alt_m")
    w = project.get("wind", {})
    ws = w.get("speed_mps")
    if ws is not None and ws > MAX_WIND_MPS:
        pr.err(f"Wind {ws:g} m/s is above the {MAX_WIND_MPS:g} m/s limit: a landing "
               f"without GPS could miss the strip. Don't fly today", "wind.speed_mps")
    if for_launch:
        if ws is None or w.get("from_deg") is None:
            pr.err("Enter today's wind (speed and direction) before launching", "wind.speed_mps")
        if not (project.get("mission") or {}).get("launch"):
            pr.err("The mission has no launch point")
    elif ws is None:
        pr.warn("Today's wind is not entered yet (needed before launch)", "wind.speed_mps")
    return pr


def validate(project: dict, for_launch: bool = False) -> tuple[list[str], list[str]]:
    """(errors, warnings) about the answers. Errors block sending and launching."""
    pr = check_answers(project, for_launch)
    return pr.errors, pr.warnings


# ------------------------------------------------------------- outputs

def plan_body(project: dict, camera: dict | None = None) -> dict:
    """Input for planner/survey.py plan_from_dict()."""
    a, m = project["aircraft"], project["mission"]
    tgt = m.get("target")
    body = {
        "launch": m.get("launch"), "area": m.get("area") or [], "landing": m.get("landing"),
        "landing_heading_deg": float(m.get("landing_heading_deg") or 0.0),
        "alt_m": float(m["alt_m"]), "speed_mps": float(a["cruise_mps"]),
        "overlap": float(m.get("overlap_pct", 20)) / 100.0,
        "line_angle_deg": m.get("line_angle_deg"),
        "max_bank_deg": float(a["max_bank_deg"]),
        "takeoff_alt_m": float(m["takeoff_alt_m"]),
        "approach_len_m": float(m["approach_len_m"]),
        "approach_alt_m": float(m["approach_alt_m"]),
        "alt_limit_m": float(m["alt_limit_m"]),
        "endurance_min": float(a["endurance_min"]),
        "target_size_m": TARGET_SIZES.get(tgt) if tgt else None,
        # the fence is SET from the plan (make_params: FENCE_RADIUS = fence needed)
        "fence_radius_m": float(m.get("fence_radius_m") or 10000.0),
    }
    if camera:
        body["camera"] = camera
    return body


def wind_landing_heading(project: dict) -> float | None:
    """Land INTO the wind: travel towards where the wind comes from."""
    w = project.get("wind") or {}
    return None if w.get("from_deg") is None else float(w["from_deg"]) % 360


def make_params(project: dict, plan: dict | None = None) -> dict:
    """ArduPlane parameters that follow from the answers (on top of the base file)."""
    a, m = project["aircraft"], project["mission"]
    cells, mah = int(a["battery_cells"]), int(a["battery_mah"])
    p = {
        "BATT_CAPACITY": mah,
        "BATT_LOW_VOLT": round(3.5 * cells, 1), "BATT_LOW_MAH": int(round(0.25 * mah)),
        "BATT_CRT_VOLT": round(3.3 * cells, 1), "BATT_CRT_MAH": int(round(0.15 * mah)),
        "AIRSPEED_MIN": int(math.ceil(1.3 * float(a["stall_mps"]))),
        "AIRSPEED_CRUISE": round(float(a["cruise_mps"]), 1),
        "AIRSPEED_MAX": int(math.floor(float(a["max_mps"]))),
        "ROLL_LIMIT_DEG": int(a["max_bank_deg"]),
        "ARSPD_USE": 1,
        "FENCE_ALT_MAX": int(m["alt_limit_m"]),
        "RTL_ALTITUDE": int(min(float(m["alt_limit_m"]) - 10, float(m["alt_m"]))),
    }
    pusher = a.get("motor", "pusher") == "pusher"
    launch = a.get("launch", "hand")
    if launch == "hand":          # motor waits for the throw, then clears the hand
        p.update(TKOFF_THR_MINACC=15, TKOFF_THR_DELAY=3 if pusher else 1)
    elif launch == "bungee":      # the bungee pull is a harder push
        p.update(TKOFF_THR_MINACC=20, TKOFF_THR_DELAY=3 if pusher else 1)
    else:                         # runway: motor runs as soon as AUTO starts the takeoff
        p.update(TKOFF_THR_MINACC=0, TKOFF_THR_DELAY=0,
                 TKOFF_ROTATE_SPD=int(math.ceil(1.1 * float(a["stall_mps"]))))
    p["LAND_PITCH_DEG"] = 4 if a.get("landing", "belly") == "belly" else 2
    if plan and plan.get("fence_needed_m"):
        p["FENCE_RADIUS"] = int(max(plan["fence_needed_m"], 300))
    # the flight controller's port to the Pi: MAVLink2 at the Pi's speed
    link = project.get("link") or {}
    port = str(link.get("fc_serial_for_pi") or "2")
    if link.get("pi_serial", "/dev/serial0") == "/dev/serial0" and port in {str(i) for i in range(1, 8)}:
        n = int(port)
        baud = int(link.get("pi_baud") or 115200)
        p[f"SERIAL{n}_PROTOCOL"] = 2
        p[f"SERIAL{n}_BAUD"] = {57600: 57, 115200: 115, 921600: 921}.get(baud, 115)
    return p


def param_file_text(project: dict, plan: dict | None = None, base_path: str = BASE_PARAMS) -> str:
    """The base parameter file with this aircraft's values filled in place;
    parameters not in the base file are added at the end."""
    params = make_params(project, plan)
    left = dict(params)
    out = [f"# Generated by the SILM Ground Station for aircraft "
           f"'{project['aircraft'].get('name', '')}', project '{project.get('name', '')}'.",
           "# Values marked '# from your answers' come from the Ground Station questions.", ""]
    with open(base_path, encoding="utf-8") as fh:
        for line in fh:
            body = line.split("#", 1)[0].strip()
            m = re.match(r"^([A-Z0-9_]+)\s*,", body)
            if m and m.group(1) in left:
                name = m.group(1)
                out.append(f"{name},{_fmt(left.pop(name))}   # from your answers")
            else:
                out.append(line.rstrip("\n"))
    if left:
        out += ["", "# --- From your answers in the Ground Station ---------------------------"]
        out += [f"{k},{_fmt(v)}" for k, v in left.items()]
    return "\n".join(out) + "\n"


def _fmt(v) -> str:
    return f"{v:g}" if isinstance(v, float) else str(v)


def read_param_text(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        body = line.split("#", 1)[0].strip()
        if "," in body:
            k, v = body.split(",", 1)
            out[k.strip()] = float(v)
    return out


def make_settings(project: dict) -> dict:
    """The Pi's settings.yaml: the sections config.yaml has, with your answers."""
    r, c, link = project["recognition"], project["camera"], project["link"]
    targets = list(r.get("targets") or [])
    out = {
        "program": {"mode": project.get("program", {}).get("mode", "flight")},
        "telemetry": {"connection": link.get("pi_serial", "/dev/serial0"),
                      "baud": int(link.get("pi_baud") or 115200)},
        "camera": {"url": c.get("url")},
        "gimbal": {"enabled": bool(c.get("gimbal"))},
        "targets": targets,
        "detector": {"enabled": bool(targets),
                     "score_threshold": round(float(r.get("sensitivity", 45)) / 100.0, 2),
                     **model_settings(r.get("model", "standard"))},
        "fire": {"enabled": bool(r.get("fire")), "detect_smoke": bool(r.get("fire"))},
        "activation": {"min_alt_m": float(r.get("min_alt_m", 20))},
        "storage": {"save_all_frames": bool(r.get("save_all_frames")),
                    "frame_interval_s": float(r.get("frame_interval_s", 2))},
        "mission_name": project.get("name", ""),
        "aircraft_name": project["aircraft"].get("name", ""),
    }
    if c.get("calibration"):            # measured on the Tools tab (lens calibration)
        out["georef"] = {"calibration": c["calibration"]}
    return out


PI_MODELS = "~/silm-settings/models"     # where the agent stores trained models on the Pi


def model_settings(model: str) -> dict:
    """detector.model/labels/format for the chosen model (standard = config.yaml's)."""
    if not model or model == "standard":
        return {}
    return {"model": f"{PI_MODELS}/{model}.tflite", "labels": f"{PI_MODELS}/{model}_labels.txt",
            "format": "yolo"}


def settings_yaml(project: dict) -> str:
    head = (f"# SILM settings from the Ground Station - project '{project.get('name', '')}'.\n"
            "# Applied on top of config.yaml by the flight program. Edit in the Ground Station,\n"
            "# not here: sending again replaces this file.\n")
    return head + yaml.safe_dump(make_settings(project), sort_keys=False)


def check_settings_against_config(project: dict, config_path: str = BASE_CONFIG) -> list[str]:
    """Validate the Pi settings exactly as the Pi will (config.validate_config)."""
    import sys
    sys.path.insert(0, ONBOARD)
    from silm_recon.config import deep_merge, validate_config
    with open(config_path, encoding="utf-8") as fh:
        base = yaml.safe_load(fh) or {}
    return validate_config(deep_merge(base, make_settings(project)))


def copy_project(project: dict) -> dict:
    return copy.deepcopy(project)
