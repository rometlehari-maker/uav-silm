"""SILM Ground Station (laptop): questions, checks, generated files, the
flight controller over MAVLink, the Pi over real HTTP and a USB stick, the
flight library with picture coordinates, and the HTTP API."""

import io
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import zipfile

import pytest
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
ONBOARD = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(ONBOARD, "planner"))

from groundstation import fc, flights, piinstall, pilink  # noqa: E402
from groundstation import project as P  # noqa: E402
from groundstation.app import Station, make_server  # noqa: E402
from silm_recon import mavmission as mm  # noqa: E402
from silm_recon.agent import Agent  # noqa: E402
from silm_recon.agent import make_server as agent_server  # noqa: E402
from silm_recon.config import deep_merge, validate_config  # noqa: E402

# a real field near Tartu: launch at the edge, survey area ~1 km away
LAUNCH = [58.3800, 26.7200]
AREA = [[58.3850, 26.7250], [58.3880, 26.7350], [58.3840, 26.7400], [58.3815, 26.7300]]


def full_project(**over):
    p = P.default_project()
    p["name"] = "Test field"
    p["aircraft"]["cg_mm"] = 64
    p["mission"].update(launch=LAUNCH, area=AREA, landing=LAUNCH, landing_heading_deg=250)
    p["wind"] = {"speed_mps": 5, "from_deg": 250}
    for k, v in over.items():
        sec, key = k.split("__")
        p[sec][key] = v
    return p


@pytest.fixture
def station(tmp_path):
    return Station(library=str(tmp_path / "flights"), projects=str(tmp_path / "projects"))


# ------------------------------------------------------------- questions

def test_every_value_has_a_question_above_it():
    ids = [q["id"] for q in P.QUESTIONS]
    assert len(ids) == len(set(ids))
    proj = P.default_project()
    tabs = {"aircraft", "mission", "camera", "send"}
    for q in P.QUESTIONS:
        assert q["q"].endswith("?"), q["q"]
        assert q["tab"] in tabs and q["group"], q
        sec, key = q["id"].split(".")
        assert key in proj[sec], q["id"]
        if q["type"] in ("select", "multi"):
            assert q["options"]
    assert any(q["q"] == "What is the center of gravity of your aircraft?" for q in P.QUESTIONS)


def test_default_project_needs_only_the_cg_and_wind():
    errors, warnings = P.validate(P.default_project())
    assert errors == ["Not answered: What is the center of gravity of your aircraft?"]
    assert any("wind" in w for w in warnings)
    assert P.validate(full_project())[0] == []


@pytest.mark.parametrize("over, words", [
    ({"aircraft__cg_mm": 20}, "far outside"),               # 9 % of the wing depth
    ({"aircraft__cg_mm": 140}, "far outside"),
    ({"aircraft__cruise_mps": 11}, "too close to the stall"),
    ({"aircraft__max_mps": 20}, "at least 3 m/s above"),
    ({"aircraft__has_airspeed_sensor": False}, "airspeed sensor is required"),
    ({"aircraft__battery_cells": 1}, "below the minimum"),
    ({"aircraft__launch": "slingshot"}, "not one of the choices"),
    ({"wind__speed_mps": 11}, "above the 10 m/s limit"),
    ({"recognition__min_alt_m": 150}, "never take any"),
    ({"mission__alt_m": 150}, "above the 120 m limit"),
    ({"aircraft__weight_g": "heavy"}, "is not a number"),
    ({"recognition__targets": ["Bad Name!"]}, "unknown choice"),
    ({"recognition__model": "../../etc"}, "not one of the choices"),
])
def test_wrong_answers_are_errors(over, words):
    errors, _ = P.validate(full_project(**over))
    assert any(words in e for e in errors), errors


def test_cg_advice():
    a = {"chord_mm": 200}
    assert P.cg_advice(dict(a, cg_mm=60))["verdict"] == "good"
    assert P.cg_advice(dict(a, cg_mm=72))["verdict"] == "check"      # 36 %
    assert P.cg_advice(dict(a, cg_mm=20))["verdict"] == "bad"
    assert P.cg_advice(dict(a, cg_mm=60))["recommended_mm"] == [50, 66]
    assert P.cg_advice({"chord_mm": 200, "cg_mm": None}) is None


def test_launch_needs_wind():
    p = full_project()
    p["wind"] = {"speed_mps": None, "from_deg": None}
    assert any("Enter today's wind" in e for e in P.validate(p, for_launch=True)[0])
    assert P.validate(full_project(), for_launch=True)[0] == []


# ------------------------------------------------------------- parameters

@pytest.mark.parametrize("cells, mah, low, crt", [(4, 7000, 14.0, 13.2), (6, 10000, 21.0, 19.8),
                                                  (3, 2200, 10.5, 9.9)])
def test_battery_parameters(cells, mah, low, crt):
    p = full_project(aircraft__battery_cells=cells, aircraft__battery_mah=mah)
    prm = P.make_params(p)
    assert prm["BATT_LOW_VOLT"] == low and prm["BATT_CRT_VOLT"] == crt
    assert prm["BATT_CAPACITY"] == mah and prm["BATT_LOW_MAH"] == round(mah * 0.25)
    assert prm["BATT_CRT_MAH"] == round(mah * 0.15)


@pytest.mark.parametrize("launch, motor, minacc, delay", [
    ("hand", "pusher", 15, 3), ("hand", "tractor", 15, 1), ("bungee", "pusher", 20, 3),
    ("runway", "tractor", 0, 0)])
def test_launch_type_parameters(launch, motor, minacc, delay):
    prm = P.make_params(full_project(aircraft__launch=launch, aircraft__motor=motor))
    assert prm["TKOFF_THR_MINACC"] == minacc and prm["TKOFF_THR_DELAY"] == delay
    assert ("TKOFF_ROTATE_SPD" in prm) == (launch == "runway")


def test_speed_and_fence_parameters(station):
    p = full_project()
    plan = station.plan(p)
    prm = P.make_params(p, plan)
    assert prm["AIRSPEED_MIN"] == 13 and prm["AIRSPEED_CRUISE"] == 19 and prm["AIRSPEED_MAX"] == 28
    assert prm["FENCE_RADIUS"] == plan["fence_needed_m"] and prm["FENCE_ALT_MAX"] == 120
    assert prm["ROLL_LIMIT_DEG"] == 45 and prm["ARSPD_USE"] == 1


def test_param_file_keeps_the_failsafe_and_fills_in_answers(station):
    p = full_project(aircraft__battery_cells=6)
    text = P.param_file_text(p, station.plan(p))
    vals = P.read_param_text(text)
    assert vals["BATT_CRT_VOLT"] == pytest.approx(19.8)
    for name, v in (("AHRS_OPTIONS", 1), ("EK3_SRC2_POSXY", 0), ("SCR_ENABLE", 1),
                    ("ARMING_MIS_ITEMS", 13), ("RTL_AUTOLAND", 2), ("INITIAL_MODE", 10)):
        assert vals[name] == v, name
    names = [ln.split(",")[0] for ln in text.splitlines() if ln and not ln.startswith("#") and "," in ln]
    assert len(names) == len(set(names)), "a parameter appears twice"
    assert "# from your answers" in text


ARDUPILOT = os.environ.get("ARDUPILOT_SRC") or (
    "/home/user/ardupilot" if os.path.isdir("/home/user/ardupilot/ArduPlane") else None)


@pytest.mark.skipif(not ARDUPILOT, reason="set ARDUPILOT_SRC to an ArduPilot checkout")
@pytest.mark.parametrize("launch", ["hand", "bungee", "runway"])
def test_generated_param_file_is_valid_for_arduplane(station, tmp_path, launch):
    p = full_project(aircraft__launch=launch, aircraft__battery_cells=6)
    f = tmp_path / "gen.param"
    f.write_text(P.param_file_text(p, station.plan(p)))
    r = subprocess.run([sys.executable, os.path.join(ONBOARD, "tools", "check_params.py"),
                        "--src", ARDUPILOT, str(f)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


# ------------------------------------------------------------- Pi settings

def test_pi_settings_pass_the_pis_own_validation():
    p = full_project(recognition__targets=["tractor", "car"], recognition__save_all_frames=True,
                     program__mode="bench", link__pi_serial="/dev/ttyACM0", link__pi_baud="921600")
    s = P.make_settings(p)
    assert s["targets"] == ["tractor", "car"] and s["storage"]["save_all_frames"] is True
    assert s["telemetry"] == {"connection": "/dev/ttyACM0", "baud": 921600}
    assert s["program"]["mode"] == "bench" and s["detector"]["score_threshold"] == 0.45
    base = yaml.safe_load(open(os.path.join(ONBOARD, "config.yaml")))
    assert validate_config(deep_merge(base, yaml.safe_load(P.settings_yaml(p)))) == []
    assert P.check_settings_against_config(p) == []


def test_fire_only_disables_the_object_detector():
    s = P.make_settings(full_project(recognition__targets=[], recognition__fire=True))
    assert s["detector"]["enabled"] is False and s["fire"]["enabled"] is True
    assert P.check_settings_against_config(full_project(recognition__targets=[])) == []


def test_trained_model_targets_warn():
    _, w = P.validate(full_project(recognition__targets=["combine"]))
    assert any("trained model" in x for x in w)


# ------------------------------------------------------------- check + outputs

def test_check_full_mission(station):
    r = station.check(full_project())
    assert r["ok"], r["errors"]
    pl = r["plan"]
    assert pl["lines"] >= 2 and pl["flight_min"] > 3 and pl["fence_needed_m"] >= 1000
    assert pl["items"][1]["name"] == "TAKEOFF" and pl["items"][-1]["name"] == "LAND"
    assert r["cg"]["verdict"] == "good"


def test_check_warns_when_landing_is_not_into_the_wind(station):
    r = station.check(full_project(mission__landing_heading_deg=70))
    assert any("off the wind" in w for w in r["warnings"])


def test_check_without_a_mission_explains_what_to_do(station):
    p = full_project()
    p["mission"]["area"] = []
    r = station.check(p)
    assert r["plan"] is None and any("draw the survey area" in w for w in r["warnings"])
    with pytest.raises(ValueError):
        station.outputs(p)


def test_outputs_are_consistent(station):
    o = station.outputs(full_project())
    items = mm.from_wpl(o["mission_wpl"])
    assert mm.check_mission(items, P.read_param_text(o["params_text"])["FENCE_RADIUS"])[0] == []
    assert yaml.safe_load(o["settings_text"])["targets"] == ["person", "car", "truck"]


def test_outputs_refuse_a_project_with_errors(station):
    with pytest.raises(ValueError, match="center of gravity"):
        station.outputs(full_project(aircraft__cg_mm=None))


# ------------------------------------------------------------- projects

def test_projects_save_list_load_delete(station):
    p = full_project()
    p["name"] = "../../evil name!"
    r = station.save_project(p)
    assert r["ok"] and os.path.dirname(r["path"]) == station.projects
    names = [x["name"] for x in station.list_projects()]
    assert names == ["evil name"]
    assert station.load_project("evil name")["aircraft"]["cg_mm"] == 64
    station.delete_project("evil name")
    assert station.list_projects() == []


def test_old_project_gets_new_questions_filled():
    old = {"name": "old", "aircraft": {"name": "X", "cg_mm": 60}}
    m = P.merge_defaults(old)
    assert m["aircraft"]["cg_mm"] == 60 and m["aircraft"]["battery_cells"] == 4
    assert "recognition" in m and m["link"]["pi_address"] == "10.42.0.1"


# ------------------------------------------------------------- flight controller (fake, MAVLink)

@pytest.fixture
def vehicle():
    pytest.importorskip("pymavlink")
    from test_planner import FakeVehicle, free_port
    port = free_port()
    v = FakeVehicle(port)
    v.params.update({"BATT_CAPACITY": 5000.0, "AIRSPEED_CRUISE": 15.0, "SLM_ENABLE": 1.0,
                     "SCR_ENABLE": 1.0})
    v.start()
    yield f"udpin:127.0.0.1:{port}", v
    v.stop_evt.set()


def test_fc_params_mission_arm_disarm(station, vehicle):
    url, v = vehicle
    p = full_project()
    p["link"]["fc_port"] = url
    o = station.outputs(p)
    r = fc.send_params(url, {"BATT_CAPACITY": 7000, "AIRSPEED_CRUISE": 19, "NOT_A_PARAM": 1})
    assert r["written"] == 2 and r["not_on_aircraft"] == ["NOT_A_PARAM"] and not r["ok"]
    assert v.params["BATT_CAPACITY"] == 7000
    r = fc.send_mission(url, o["plan"]["items"])
    assert r["ok"] and "identical" in r["message"]
    # arming through the station: needs the checklist and today's wind
    r = station.fc_action("arm", {"port": url, "project": p})
    assert not r["ok"] and "checklist" in r["message"]
    p2 = full_project()
    p2["wind"] = {"speed_mps": 12, "from_deg": 250}
    r = station.fc_action("arm", {"port": url, "project": p2, "checklist_done": True})
    assert not r["ok"] and "10 m/s limit" in r["message"]
    r = station.fc_action("arm", {"port": url, "project": p, "checklist_done": True})
    assert r["ok"] and v.armed and v.mode == mm.MODE_AUTO
    with pytest.raises(fc.FCError, match="ARMED"):
        fc.send_params(url, {"BATT_CAPACITY": 7000})
    assert station.fc_action("disarm", {"port": url})["ok"] and not v.armed


def test_fc_no_port_and_no_answer():
    with pytest.raises(fc.FCError, match="choose"):
        fc.connect("")
    from test_planner import free_port
    with pytest.raises(fc.FCError, match="no answer"):
        fc.connect(f"udpin:127.0.0.1:{free_port()}", timeout=1)


# ------------------------------------------------------------- the Pi over Wi-Fi (real agent)

def make_flight(base, name="mission_20261108_101500", n_det=3, n_frames=4):
    d = os.path.join(base, name)
    os.makedirs(os.path.join(d, "frames"))
    t0 = 1794132900.0
    with open(os.path.join(d, "track.ndjson"), "w") as fh:
        for i in range(60):
            fh.write(json.dumps({"t": t0 + i, "lat": 58.38 + i * 1e-4, "lon": 26.72, "alt": 100,
                                 "mode": "AUTO", "armed": True, "gps": "OK"}) + "\n")
    import numpy as np
    import cv2
    with open(os.path.join(d, "detections.ndjson"), "w") as fh:
        for i in range(n_det):
            jpg = f"det_{i + 1:05d}_{int(t0) + i}.jpg"
            cv2.imwrite(os.path.join(d, jpg), np.full((90, 160, 3), 40 * i, np.uint8))
            rec = {"seq": i + 1, "time": "2026-11-08T10:15:0%dZ" % i, "jpg": jpg,
                   "aircraft": {"lat": 58.38, "lon": 26.72, "rel_alt_m": 100, "yaw_deg": 90,
                                "mode": "AUTO"},
                   "detections": [{"label": "car" if i else "fire", "score": 0.8,
                                   "box_norm": [0.4, 0.4, 0.5, 0.5],
                                   "ground_lat": 58.381 + i * 1e-4 if i != 2 else None,
                                   "ground_lon": 26.721 if i != 2 else None}]}
            fh.write(json.dumps(rec) + "\n")
        fh.write('{"seq": 99, "cut off by a power lo')     # a torn last line
    for k in range(n_frames):
        cv2.imwrite(os.path.join(d, "frames", f"frame_{int(t0) + 10 + 10 * k}_{k}.jpg"),
                    np.zeros((90, 160, 3), np.uint8))
    with open(os.path.join(d, "gps_health.ndjson"), "w") as fh:
        fh.write(json.dumps({"t": "x", "event": "GPS state OK -> LOST"}) + "\n")
    return d


@pytest.fixture
def pi(tmp_path):
    """A real ground agent on 127.0.0.1, paths in tmp_path."""
    base = yaml.safe_load(open(os.path.join(ONBOARD, "config.yaml")))
    base["settings_file"] = str(tmp_path / "pi" / "silm-settings" / "settings.yaml")
    base["status"] = {"path": str(tmp_path / "pi" / "status.json")}
    base["storage"]["output_dir"] = str(tmp_path / "pi" / "captures")
    base["agent"]["usb_mount"] = str(tmp_path / "stick")
    base["agent"]["usb_device"] = ""
    (tmp_path / "pi").mkdir()
    cfgp = tmp_path / "pi" / "config.yaml"
    cfgp.write_text(yaml.safe_dump(base))
    calls = []
    agent = Agent(str(cfgp), runner=lambda cmd, timeout=15: (calls.append(cmd) or True, ""))
    srv = agent_server(agent, host="127.0.0.1", port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield agent, f"127.0.0.1:{srv.server_address[1]}", tmp_path, calls
    srv.shutdown()


def test_wifi_status_send_and_download_round_trip(station, pi):
    agent, addr, tmp, calls = pi
    st = pilink.status(addr)
    assert st["ok"] and st["software"] == open(os.path.join(ONBOARD, "VERSION")).read().strip() and not st["settings_present"]
    p = full_project(recognition__targets=["tractor"])
    r = station.pi_send({"project": p, "address": addr})
    assert r["ok"], r
    on_pi = yaml.safe_load(open(agent.settings_file))
    assert on_pi["targets"] == ["tractor"] and on_pi["mission_name"] == "Test field"
    assert open(os.path.join(agent.settings_dir, "mission.waypoints")).read().startswith("QGC WPL")
    assert ["sudo", "-n", "systemctl", "restart", "silm-recon"] in calls
    assert pilink.status(addr)["settings_info"]["project"] == "Test field"
    # a flight recorded on the Pi comes back to the laptop with its pictures
    make_flight(agent.output_dir())
    fl = pilink.flights(addr)
    assert fl[0]["name"] == "mission_20261108_101500" and fl[0]["pictures"] == 7
    d = pilink.download_flight(addr, "mission_20261108_101500", station.library)
    assert os.path.isfile(os.path.join(d, "det_00001_1794132900.jpg"))
    assert flights.list_library(station.library)[0]["pictures"] == 7


def test_wifi_errors_are_explained(station):
    with pytest.raises(pilink.PiError, match="Wi-Fi"):
        pilink.status("127.0.0.1:9")            # nothing listens there
    with pytest.raises(pilink.PiError, match="address"):
        pilink.status("")


def test_wifi_refused_while_armed(station, pi):
    agent, addr, tmp, _ = pi
    from silm_recon.status import write_status
    write_status(agent.status_file, {"armed": True})
    r = station.pi_send({"project": full_project(), "address": addr})
    assert not r["ok"] and "ARMED" in r["message"]


# ------------------------------------------------------------- the Pi with a USB stick

def test_usb_stick_round_trip(station, pi):
    agent, _, tmp, _ = pi
    stick = tmp / "stick"
    stick.mkdir()
    r = station.usb_write({"project": full_project(recognition__targets=["truck"]),
                           "path": str(stick)})
    assert r["ok"]
    assert pilink.usb_read(str(stick))["import_result"] is None       # not in the Pi yet
    make_flight(agent.output_dir())
    done = agent.usb_tick()                                             # "plugged into the Pi"
    assert done["imported"] and done["copied"] == ["mission_20261108_101500"]
    st = pilink.usb_read(str(stick))
    assert st["import_result"]["ok"] and st["flights"] == ["mission_20261108_101500"]
    assert yaml.safe_load(open(agent.settings_file))["targets"] == ["truck"]
    name = os.path.basename(pilink.usb_import_flight(str(stick), st["flights"][0], station.library))
    assert flights.load_flight(station.library, name)["summary"]["pictures"] == 7


def test_usb_write_to_a_missing_stick(station):
    with pytest.raises(pilink.PiError, match="does not exist"):
        station.usb_write({"project": full_project(), "path": "/no/such/stick"})


def test_import_folder_from_an_sd_card(station, tmp_path):
    sd = tmp_path / "sd" / "silm_captures"
    make_flight(str(sd), "mission_20261101_090000")
    make_flight(str(sd), "mission_20261102_090000")
    names = pilink.import_folder(str(tmp_path / "sd"), station.library)
    assert sorted(names) == ["mission_20261101_090000", "mission_20261102_090000"]
    (tmp_path / "empty").mkdir()
    with pytest.raises(pilink.PiError, match="no flights"):
        pilink.import_folder(str(tmp_path / "empty"), station.library)
    with pytest.raises(pilink.PiError, match="is not a folder"):
        pilink.import_folder(str(tmp_path / "does_not_exist"), station.library)


# ------------------------------------------------------------- pictures with coordinates

def test_every_picture_gets_coordinates(tmp_path):
    lib = str(tmp_path)
    make_flight(lib)
    fl = flights.load_flight(lib, "mission_20261108_101500")
    dets = [p for p in fl["pictures"] if p["kind"] == "detection"]
    frames = [p for p in fl["pictures"] if p["kind"] == "frame"]
    assert len(dets) == 3 and len(frames) == 4 and fl["labels"] == ["car", "fire"]
    assert dets[0]["lat"] == pytest.approx(58.381) and dets[0]["pos_source"].startswith("object")
    assert dets[2]["lat"] == 58.38 and dets[2]["pos_source"].startswith("aircraft")
    # frame taken 10 s after the track started -> 10 track steps north
    assert frames[0]["lat"] == pytest.approx(58.38 + 10e-4) and frames[0]["lon"] == 26.72
    assert frames[0]["pos_source"].startswith("aircraft (interpolated")
    assert fl["summary"]["with_position"] == 7 and fl["summary"]["gps_problems"] == 1


def test_track_interpolation_and_gaps():
    tr = [{"t": 0, "lat": 0.0, "lon": 0.0, "alt": 0}, {"t": 2, "lat": 2.0, "lon": 4.0, "alt": 10},
          {"t": 100, "lat": 9.0, "lon": 9.0, "alt": 0}]
    p = flights._interp_track(tr, 1.0)
    assert (p["lat"], p["lon"], p["alt"]) == (1.0, 2.0, 5.0) and p["interp"]
    assert flights._interp_track(tr, 50) is None          # inside a 98 s gap: no guessing
    assert flights._interp_track(tr, 3)["interp"] is False  # next to a gap: nearest point
    assert flights._interp_track(tr, 200) is None and flights._interp_track([], 1) is None


def test_picture_paths_are_contained(tmp_path):
    lib = str(tmp_path)
    make_flight(lib)
    n = "mission_20261108_101500"
    assert flights.image_path(lib, n, "frames/" + os.listdir(os.path.join(lib, n, "frames"))[0])
    for bad in ("../../etc/passwd", "track.ndjson", "/etc/hosts.jpg", ""):
        assert flights.image_path(lib, n, bad) is None
    assert flights.flight_dir(lib, "../x") is None and flights.flight_dir(lib, "nope") is None


def test_exports(tmp_path):
    make_flight(str(tmp_path))
    fl = flights.load_flight(str(tmp_path), "mission_20261108_101500")
    gj = flights.to_geojson(fl)
    pts = [f for f in gj["features"] if f["geometry"]["type"] == "Point"]
    assert len(pts) == 7 and pts[0]["geometry"]["coordinates"][1] == pytest.approx(58.381)
    assert gj["features"][-1]["geometry"]["type"] == "LineString"
    rows = flights.to_csv(fl).strip().splitlines()
    assert rows[0].startswith("picture,kind,label") and len(rows) == 8


# ------------------------------------------------------------- HTTP API

@pytest.fixture
def http(station):
    srv = make_server(0, station=station)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", station
    srv.shutdown()


def call(url, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data, {"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(req, timeout=20) as r:
        raw = r.read()
        ct = r.headers.get("Content-Type", "")
        return (json.loads(raw) if "json" in ct and "geo" not in ct else raw.decode(errors="replace")), r.headers


def test_http_pages_and_meta(http):
    url, _ = http
    html, _ = call(url + "/")
    assert "SILM Ground Station" in html and "/static/vendor/leaflet/leaflet.js" in html
    js, hdr = call(url + "/static/app.js")
    assert "function renderForm" in js and hdr["Content-Type"] == "text/javascript"
    assert call(url + "/static/vendor/leaflet/leaflet.js")[1]["Content-Type"] == "text/javascript"
    meta, _ = call(url + "/api/meta")
    assert meta["max_wind_mps"] == P.MAX_WIND_MPS == 10.0 and len(meta["questions"]) == len(P.QUESTIONS)
    with pytest.raises(urllib.error.HTTPError):
        call(url + "/static/../../config.yaml")


def test_http_check_files_projects(http):
    url, st = http
    r, _ = call(url + "/api/check", {"project": full_project()})
    assert r["ok"] and r["plan"]["lines"] >= 2
    text, hdr = call(url + "/api/files/params", {"project": full_project()})
    assert "AHRS_OPTIONS,1" in text and "Test_field.param" in hdr["Content-Disposition"]
    text, _ = call(url + "/api/files/mission", {"project": full_project()})
    assert text.startswith("QGC WPL 110")
    text, _ = call(url + "/api/files/settings", {"project": full_project()})
    assert yaml.safe_load(text)["program"]["mode"] == "flight"
    r, _ = call(url + "/api/files/params", {"project": full_project(aircraft__cg_mm=None)})
    assert r["ok"] is False and "center of gravity" in r["message"]
    assert call(url + "/api/projects", {"project": full_project()})[0]["ok"]
    assert call(url + "/api/projects")[0][0]["name"] == "Test field"
    assert call(url + "/api/projects/Test%20field")[0]["mission"]["launch"] == LAUNCH


def test_http_library_and_images(http):
    url, st = http
    make_flight(st.library)
    lib, _ = call(url + "/api/library")
    assert lib[0]["pictures"] == 7
    fl, _ = call(url + "/api/library/mission_20261108_101500")
    p = fl["pictures"][0]
    req = urllib.request.urlopen(url + "/api/library/mission_20261108_101500/img?p=" +
                                 urllib.parse.quote(p["img"]), timeout=10)
    assert req.headers["Content-Type"] == "image/jpeg" and req.read(2) == b"\xff\xd8"
    gj, hdr = call(url + "/api/library/mission_20261108_101500/export.geojson")
    assert json.loads(gj)["type"] == "FeatureCollection" and "attachment" in hdr["Content-Disposition"]
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(url + "/api/library/mission_20261108_101500/img?p=..%2F..%2F..%2Fetc%2Fpasswd")
    assert e.value.code == 404


def test_http_errors_are_messages_not_crashes(http):
    url, _ = http
    r, _ = call(url + "/api/fc/info", {"port": ""})
    assert r["ok"] is False and "port" in r["message"]
    r, _ = call(url + "/api/pi/status", {"address": "127.0.0.1:9"})
    assert r["ok"] is False and "Wi-Fi" in r["message"]
    r, _ = call(url + "/api/usb/read", {"path": "/nonexistent"})
    assert r["ok"] and r["present"] is False
    r, _ = call(url + "/api/sim/commands", {"project": full_project()})
    assert "sim_vehicle.py" in r["text"] and "58.380000,26.720000" in r["text"]


import urllib.error  # noqa: E402
import urllib.parse  # noqa: E402


# ------------------------------------------------------------- teaching new objects

from groundstation import training  # noqa: E402


def jpeg_bytes(w=160, h=120, val=90):
    import cv2
    import numpy as np
    ok, buf = cv2.imencode(".jpg", np.full((h, w, 3), val, np.uint8))
    return buf.tobytes()


def test_dataset_pictures_boxes_and_export(tmp_path):
    ds = training.Dataset(str(tmp_path), "fields")
    classes = ds.classes()
    assert classes[:5] == ["person", "car", "truck", "tractor", "combine"]
    assert ds.add_class("Crop Sprayer") == classes + ["crop_sprayer"]
    assert ds.add_class("crop_sprayer")[-1] == "crop_sprayer"        # no duplicate
    with pytest.raises(ValueError):
        ds.add_class("  ")
    a = ds.add_image("../../tractor 1.jpg", jpeg_bytes(val=10))
    assert a.startswith("tractor_1_") and a.endswith(".jpg") and "/" not in a
    assert ds.add_image("again.jpg", jpeg_bytes(val=10)) == a          # same picture: one file
    b = ds.add_image_b64("empty.png", "data:image/png;base64," + __import__("base64").b64encode(
        __import__("cv2").imencode(".png", __import__("numpy").zeros((50, 60, 3), "uint8"))[1].tobytes()).decode())
    with pytest.raises(ValueError, match="not a JPEG or PNG"):
        ds.add_image("evil.jpg", b"#!/bin/sh\nrm -rf /")
    assert ds.get_boxes(a) is None                        # not looked at yet
    n = ds.set_boxes(a, [{"cls": 3, "x0": 0.6, "y0": 0.5, "x1": 0.2, "y1": 0.1},   # drawn backwards
                         {"cls": 4, "x0": 0.5, "y0": 0.5, "x1": 0.5005, "y1": 0.9},  # a click
                         {"cls": 4, "x0": -0.2, "y0": 0.7, "x1": 0.3, "y1": 1.4}])   # off the edge
    assert n == 2
    bx = ds.get_boxes(a)
    assert bx[0]["cls"] == 3 and bx[0]["x0"] == pytest.approx(0.2) and bx[0]["y1"] == pytest.approx(0.5)
    assert bx[1]["x0"] == pytest.approx(0.0) and bx[1]["y1"] == pytest.approx(1.0)
    with pytest.raises(ValueError, match="unknown object type"):
        ds.set_boxes(a, [{"cls": 99, "x0": 0, "y0": 0, "x1": 1, "y1": 1}])
    ds.set_boxes(b, [])                                   # "nothing here"
    st = ds.stats()
    assert st["pictures"] == 2 and st["labelled"] == 2 and st["empty"] == 1
    assert st["per_class"]["tractor"] == {"boxes": 1, "pictures": 1} and not st["ready"]
    assert any("add at least 30" in x for x in st["advice"])
    data = yaml.safe_load(open(ds.export()))
    assert data["names"][3] == "tractor" and data["names"][len(classes)] == "crop_sprayer"
    lbl = os.path.join(data["path"], "labels")
    files = [f for sub in ("train", "val") for f in os.listdir(os.path.join(lbl, sub))]
    assert len(files) >= 2
    with pytest.raises(FileNotFoundError):
        ds.image_path("../../../etc/passwd")
    ds.delete_image(b)
    assert len(ds.list_images()) == 1


def test_dataset_needs_marked_pictures_to_export(tmp_path):
    ds = training.Dataset(str(tmp_path))
    ds.add_image("x.jpg", jpeg_bytes())
    with pytest.raises(ValueError, match="no marked pictures"):
        ds.export()


def fake_model(tmp_dir, name="farm_v1", classes=("tractor", "combine")):
    os.makedirs(tmp_dir, exist_ok=True)
    real = os.path.join(ONBOARD, "models", "coco_ssd_mobilenet_v1_quant.tflite")
    data = open(real, "rb").read() if os.path.isfile(real) else b"\x1c\x00\x00\x00TFL3" + bytes(5000)
    open(os.path.join(tmp_dir, name + ".tflite"), "wb").write(data)
    open(os.path.join(tmp_dir, name + "_labels.txt"), "w").write("\n".join(classes) + "\n")
    json.dump({"classes": list(classes), "map50": 0.71}, open(os.path.join(tmp_dir, name + ".json"), "w"))
    return data


def test_trained_model_goes_to_the_pi_over_wifi(station, pi):
    agent, addr, tmp, _ = pi
    data = fake_model(station.trainer.models_dir)
    p = full_project(recognition__model="farm_v1", recognition__targets=["tractor", "combine"])
    assert station.check(p)["ok"], station.check(p)["errors"]
    assert P.make_settings(p)["detector"]["model"] == "~/silm-settings/models/farm_v1.tflite"
    r = station.pi_send({"project": p, "address": addr})
    assert r["ok"] and "farm_v1" in r["message"]
    on_pi = os.path.join(agent.models_dir(), "farm_v1.tflite")
    assert open(on_pi, "rb").read() == data
    assert open(os.path.join(agent.models_dir(), "farm_v1_labels.txt")).read() == "tractor\ncombine\n"
    assert "farm_v1" in pilink.status(addr)["models"]
    # the Pi's flight program resolves the ~ path to that file
    import run
    cfg = run.load_checked(os.path.join(ONBOARD, "config.yaml"), agent.settings_file)
    assert cfg["detector"]["model"] == os.path.expanduser("~/silm-settings/models/farm_v1.tflite")
    assert cfg["detector"]["format"] == "yolo" and cfg["targets"] == ["tractor", "combine"]


def test_trained_model_goes_to_the_pi_by_usb_stick(station, pi):
    agent, _, tmp, _ = pi
    fake_model(station.trainer.models_dir)
    stick = tmp / "stick"
    stick.mkdir()
    p = full_project(recognition__model="farm_v1", recognition__targets=["tractor"])
    r = station.usb_write({"project": p, "path": str(stick)})
    assert r["ok"] and "models/farm_v1.tflite" in r["files"]
    done = agent.usb_tick()
    assert done["models"] == ["farm_v1"] and done["imported"]
    assert agent.list_models() == ["farm_v1"]
    assert agent.usb_tick().get("models") == []              # same model: not reinstalled


def test_bundled_model_is_offered_and_sent(station, pi, tmp_path):
    """A ready-made model shipped in onboard/models (with a .json description)
    is offered without training and goes to the Pi like a trained one; a model
    file without a description (the standard COCO model) is not offered, and
    the user's own model of the same name wins."""
    agent, addr, _, _ = pi
    bundled = tmp_path / "bundled"
    data = fake_model(str(bundled), name="ready_v1", classes=("tractor", "car"))
    open(bundled / "plain.tflite", "wb").write(data)            # no .json -> not offered
    station.trainer.bundled_dirs = (str(bundled),)
    names = {m["name"]: m for m in station.trainer.list_models()}
    assert "ready_v1" in names and names["ready_v1"]["bundled"] and "plain" not in names
    p = full_project(recognition__model="ready_v1", recognition__targets=["tractor"])
    assert station.check(p)["ok"], station.check(p)["errors"]
    r = station.pi_send({"project": p, "address": addr})
    assert r["ok"] and "ready_v1" in r["message"]
    assert open(os.path.join(agent.models_dir(), "ready_v1.tflite"), "rb").read() == data
    stick = tmp_path / "stick"
    stick.mkdir()
    r = station.usb_write({"project": p, "path": str(stick)})
    assert r["ok"] and "models/ready_v1.tflite" in r["files"]
    fake_model(station.trainer.models_dir, name="ready_v1", classes=("deer",))
    mine = [m for m in station.trainer.list_models() if m["name"] == "ready_v1"]
    assert len(mine) == 1 and not mine[0].get("bundled") and mine[0]["classes"] == ["deer"]


def test_unknown_model_is_an_error(station):
    r = station.check(full_project(recognition__model="never_trained"))
    assert any("not on this laptop" in e for e in r["errors"])


def test_pi_refuses_a_file_that_is_not_a_model(pi):
    agent, addr, tmp, _ = pi
    r = pilink.send_model(addr, {"name": "x", "tflite_b64": __import__("base64").b64encode(
        b"MZ" + bytes(5000)).decode(), "labels": "a\n"})
    assert not r["ok"] and "not a TensorFlow Lite" in r["message"]
    r = pilink.send_model(addr, {"name": "x", "tflite_b64": "!!!notbase64", "labels": "a"})
    assert not r["ok"] and "damaged" in r["message"]
    from silm_recon.status import write_status
    write_status(agent.status_file, {"armed": True})
    good = fake_model(str(tmp / "m"))
    r = pilink.send_model(addr, {"name": "x", "tflite_b64": __import__("base64").b64encode(good).decode(),
                                 "labels": "a\n"})
    assert not r["ok"] and "ARMED" in r["message"]


def test_training_without_tools_explains_how_to_install(station, monkeypatch):
    monkeypatch.setattr(training, "tools_available",
                        lambda: {"available": False, "why": "the training tools are not installed"})
    st = station.train_state()
    assert st["tools"]["available"] is False
    r = station.trainer.start(station.dataset)
    assert not r["ok"] and "not installed" in r["message"]


def test_http_training_endpoints(http):
    url, st = http
    import base64
    r, _ = call(url + "/api/train/upload", {"files": [
        {"name": "combine.jpg", "data": "data:image/jpeg;base64," + base64.b64encode(jpeg_bytes()).decode()},
        {"name": "notes.jpg", "data": base64.b64encode(b"hello").decode()}]})
    assert r["added"] and r["failed"] and "not a JPEG" in r["message"]
    name = r["added"][0]
    r, _ = call(url + "/api/train/boxes", {"name": name, "boxes": [{"cls": 4, "x0": .1, "y0": .1, "x1": .5, "y1": .5}]})
    assert r["ok"] and r["boxes"] == 1
    b, _ = call(url + "/api/train/boxes?n=" + urllib.parse.quote(name))
    assert b["boxes"][0]["cls"] == 4
    img = urllib.request.urlopen(url + "/api/train/img?n=" + urllib.parse.quote(name), timeout=5)
    assert img.headers["Content-Type"] == "image/jpeg"
    state, _ = call(url + "/api/train/state")
    assert state["stats"]["per_class"]["combine"]["boxes"] == 1 and "quick" in state["presets"]
    r, _ = call(url + "/api/train/class", {"name": "sprayer"})
    assert r["classes"][-1] == "sprayer"
    r, _ = call(url + "/api/train/boxes", {"name": "../../x.jpg", "boxes": []})
    assert r["ok"] is False
    assert call(url + "/api/train/delete", {"name": name})[0]["ok"]


# ------------------------------------------------------------ guided setup
def test_setup_progress_is_kept_on_the_laptop(station):
    s = station.setup_state()
    assert s["steps"][0] == "need" and len(s["steps"]) == 10 and not s["done"] and not s["complete"]
    r = station.setup_mark({"step": "need"})
    assert r["ok"] and "need" in r["done"]
    assert "need" in station.setup_state()["done"]                     # read back from the file
    assert not station.setup_mark({"step": "nonsense"})["ok"]
    station.setup_mark({"step": "need", "done": False})
    assert "need" not in station.setup_state()["done"]
    for k in station.SETUP_STEPS:
        station.setup_mark({"step": k})
    assert station.setup_state()["complete"]
    assert station.setup_mark({"reset": True})["done"] == {}


def test_http_setup_endpoints(http):
    url, _ = http
    assert call(url + "/api/setup")[0]["done"] == {}
    assert call(url + "/api/setup", {"step": "bench"})[0]["done"].keys() == {"bench"}
    assert "bench" in call(url + "/api/setup")[0]["done"]
    html, _ = call(url + "/")
    assert 'data-tab="setup"' in html and "/static/wizard.js" in html
    js, hdr = call(url + "/static/wizard.js")
    assert "WZ_STEPS" in js and hdr["Content-Type"] == "text/javascript"
    font, hdr = None, call(url + "/static/fonts/plex-mono-400.woff2")[1]
    assert hdr["Content-Type"] == "font/woff2"


def test_flight_controller_port_to_the_pi_is_set():
    p = full_project()
    params = P.make_params(p)
    assert params["SERIAL2_PROTOCOL"] == 2 and params["SERIAL2_BAUD"] == 115      # the defaults
    p["link"].update(fc_serial_for_pi="1", pi_baud="921600")
    params = P.make_params(p)
    assert params["SERIAL1_PROTOCOL"] == 2 and params["SERIAL1_BAUD"] == 921 and "SERIAL2_BAUD" not in params
    p["link"]["pi_serial"] = "/dev/ttyACM0"                                        # Pi on USB: no port to set
    assert not any(k.startswith("SERIAL") for k in P.make_params(p))
    assert "SERIAL1_BAUD" in P.param_file_text(full_project(link__fc_serial_for_pi="1"))


def test_ports_say_which_are_usb(monkeypatch):
    import types
    fake = [types.SimpleNamespace(device="/dev/ttyS0", description="n/a", vid=None),
            types.SimpleNamespace(device="/dev/ttyACM0", description="MatekH743", vid=0x1209)]
    mod = types.SimpleNamespace(comports=lambda: fake)
    monkeypatch.setitem(sys.modules, "serial.tools.list_ports", mod)
    monkeypatch.setitem(sys.modules, "serial.tools", types.SimpleNamespace(list_ports=mod))
    monkeypatch.setitem(sys.modules, "serial", types.SimpleNamespace(tools=sys.modules["serial.tools"]))
    assert fc.list_ports() == [{"device": "/dev/ttyS0", "description": "n/a", "usb": False},
                               {"device": "/dev/ttyACM0", "description": "MatekH743", "usb": True}]


# ------------------------------------------------------------ Tools tab: the Pi
CAL = {"model": "fisheye", "width": 1920, "height": 1080,
       "camera_matrix": [[640.0, 0.0, 960.0], [0.0, 640.0, 540.0], [0.0, 0.0, 1.0]],
       "dist_coeffs": [0.05, -0.01, 0.002, -0.0005]}


def test_lens_calibration_travels_with_the_settings_and_the_pi_accepts_it(station, pi):
    agent, addr, tmp, _ = pi
    p = full_project()
    assert "georef" not in P.make_settings(p)                     # none measured: lens defaults
    p["camera"]["calibration"] = CAL
    assert P.make_settings(p)["georef"]["calibration"] == CAL
    assert station.check(p)["ok"], station.check(p)["errors"]
    r = station.pi_send({"project": p, "address": addr})
    assert r["ok"], r
    import run
    cfg = run.load_checked(os.path.join(ONBOARD, "config.yaml"), agent.settings_file)
    assert cfg["georef"]["calibration"]["camera_matrix"][0][0] == 640.0


def test_camera_picture_calibration_and_check_through_the_ground_station(pi, http):
    agent, addr, tmp, _ = pi
    url, _ = http
    with pytest.raises(urllib.error.HTTPError) as e:                  # no picture yet
        call(url + "/api/pi/camera.jpg?address=" + addr)
    assert e.value.code == 503
    (tmp / "pi" / "preview.jpg").write_bytes(b"\xff\xd8" + bytes(500))
    body, hdr = call(url + "/api/pi/camera.jpg?address=" + addr)
    assert hdr["Content-Type"] == "image/jpeg" and len(body) >= 500       # the picture, passed through
    st, _ = call(url + "/api/pi/calibration", {"address": addr, "action": "state"})
    assert st["shots"] == 0
    r, _ = call(url + "/api/pi/calibration", {"address": addr, "action": "capture", "cols": 9, "rows": 6})
    assert not r["ok"] and "not fully in the picture" in r["message"]
    r, _ = call(url + "/api/pi/calibration", {"address": addr, "action": "compute", "cols": 9, "rows": 6})
    assert not r["ok"] and "calibration failed" in r["message"]

    def tool(cmd, timeout=15):
        assert cmd[cmd.index("--alt-m") + 1] == "100.0"
        with open(cmd[cmd.index("--json") + 1], "w") as fh:
            json.dump({"results": [{"part": "system", "status": "PASS", "message": "ok"}],
                       "fails": 0, "warns": 0}, fh)
        return True, ""
    agent._run_tool = tool
    r, _ = call(url + "/api/pi/preflight", {"address": addr, "project": full_project()})
    assert r["ok"] and r["fails"] == 0 and r["results"][0]["part"] == "system"
    with pytest.raises(urllib.error.HTTPError) as e:                  # the Pi is not there
        call(url + "/api/pi/camera.jpg?address=127.0.0.1:9")
    assert e.value.code == 502


# ------------------------------------------------------------ Tools tab: the flight controller
def test_flight_controller_tool_routes(http, tmp_path, monkeypatch):
    from groundstation import fctools
    url, st = http
    monkeypatch.setattr(fctools, "firmware_dir", lambda: str(tmp_path))
    r, _ = call(url + "/api/fc/servos", {"port": ""})
    assert not r["ok"] and "choose the flight controller's port" in r["message"]
    assert call(url + "/api/fc/job")[0]["state"] == "idle"
    r, _ = call(url + "/api/fc/job/start", {"kind": "level", "port": ""})
    assert r["ok"]
    t0 = time.time()
    while call(url + "/api/fc/job")[0]["state"] == "running" and time.time() - t0 < 10:
        time.sleep(0.1)
    j = call(url + "/api/fc/job")[0]
    assert j["state"] == "failed" and "port" in j["result"]
    assert not call(url + "/api/fc/job/answer", {})[0]["ok"]          # nothing waiting
    import base64
    import json as js
    import zlib
    apj = js.dumps({"board_id": 1013, "image_size": 4,
                    "image": base64.b64encode(zlib.compress(b"abcd")).decode()}).encode()
    r, _ = call(url + "/api/fc/firmware/upload", {"name": "plane.apj", "data_b64": base64.b64encode(apj).decode()})
    assert r["ok"] and r["board_id"] == 1013
    fw, _ = call(url + "/api/fc/firmware")
    assert [f["file"] for f in fw["files"]] == ["plane.apj"] and "MatekH743" in fw["boards"]
    r, _ = call(url + "/api/fc/firmware/upload", {"name": "x.apj", "data_b64": base64.b64encode(b"junk").decode()})
    assert not r["ok"] and "not an ArduPilot firmware" in r["message"]


# ------------------------------------------------------------ Tools tab: aircraft software and Wi-Fi
def _pi_software_root(agent, tmp):
    """Point the agent at a throw-away software folder (an old version)."""
    root = tmp / "pi_sw" / "onboard"
    (root / "models").mkdir(parents=True)
    (root / "VERSION").write_text("0.9.0\n")
    (root / "requirements.txt").write_text(open(os.path.join(ONBOARD, "requirements.txt")).read())
    agent.root = str(root)
    agent._run_tool = lambda cmd, timeout=15: (True, "config OK")
    exits = []
    agent.exit_after_update = lambda: exits.append(1)
    return root, exits


def test_pi_software_package_is_complete_and_reproducible():
    from groundstation import pisoftware
    data = pisoftware.build_package()
    assert data == pisoftware.build_package()                         # same files, same bytes
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = set(z.namelist())
        assert set(pisoftware.REQUIRED) <= names
        assert {"pi/install.sh", "pi/silm-wifi", "systemd/silm-agent.service", "models/silm_oi.tflite",
                "tools/preflight.py", "tools/calibrate_camera.py", "LICENSE"} <= names
        assert not any("__pycache__" in n or n.startswith(("tests/", "groundstation/", "training/"))
                       for n in names)
        assert (z.getinfo("pi/silm-wifi").external_attr >> 16) & 0o777 == 0o755
        assert z.read("VERSION").decode().strip() == open(os.path.join(ONBOARD, "VERSION")).read().strip()
    data2, version = pisoftware.package()
    assert version == open(os.path.join(ONBOARD, "VERSION")).read().strip()


def test_pi_software_update_from_the_app_over_wifi(http, pi):
    url, _ = http
    agent, addr, tmp, calls = pi
    root, exits = _pi_software_root(agent, tmp)
    version = open(os.path.join(ONBOARD, "VERSION")).read().strip()
    r, _ = call(url + "/api/pi/software", {"how": "info", "address": addr})
    assert r["ok"] and r["app_version"] == version and r["pi_version"] == "0.9.0"
    r, _ = call(url + "/api/pi/software", {"how": "info", "address": "127.0.0.1:9"})
    assert r["ok"] and r["pi_version"] is None and "Wi-Fi" in r["pi_message"]
    r, _ = call(url + "/api/pi/software", {"how": "wifi", "address": addr})
    assert r["ok"] and r["from_version"] == "0.9.0" and r["to_version"] == version, r
    assert pilink.status(addr)["software"] == version
    assert (root / "VERSION").read_text().strip() == version and (root / "silm_recon" / "agent.py").exists()
    assert (tmp / "pi_sw" / "onboard.old" / "VERSION").read_text().strip() == "0.9.0"
    assert ["sudo", "-n", "systemctl", "restart", "silm-recon"] in calls and exits == [1]
    assert "new Python packages" not in r["message"]                  # same requirements
    from silm_recon.status import write_status
    write_status(agent.status_file, {"armed": True})
    r, _ = call(url + "/api/pi/software", {"how": "wifi", "address": addr})
    assert not r["ok"] and "ARMED" in r["message"]


def test_pi_software_update_by_usb_stick(http, pi):
    url, _ = http
    agent, _, tmp, _ = pi
    root, exits = _pi_software_root(agent, tmp)
    stick = tmp / "stick"
    stick.mkdir()
    r, _ = call(url + "/api/pi/software", {"how": "usb", "path": str(stick)})
    assert r["ok"] and r["file"].endswith(os.path.join("to_aircraft", "software", "silm-pi-software.zip"))
    done = agent.usb_tick()
    assert "installed" in done["software"] and exits == [1]
    assert (root / "VERSION").read_text().strip() == open(os.path.join(ONBOARD, "VERSION")).read().strip()
    back, _ = call(url + "/api/usb/read", {"path": str(stick)})
    assert back["aircraft"]["software"]["ok"]                          # the answer the Send tab shows
    r, _ = call(url + "/api/pi/software", {"how": "usb", "path": str(tmp / "no_stick")})
    assert not r["ok"] and "does not exist" in r["message"]


def test_pi_wifi_from_the_app(http, pi):
    url, _ = http
    agent, addr, _, calls = pi
    listing = "silm-hotspot:802-11-wireless:yes\nhome-Farm WiFi:802-11-wireless:yes\nsiyi-camera:802-3-ethernet:yes\n"
    agent._run = lambda cmd, timeout=15: (calls.append(cmd) or True, listing if cmd[-1] == "list" else "ok")
    r, _ = call(url + "/api/pi/wifi", {"address": addr, "action": "list"})
    assert r["ok"] and [(n["name"], n["kind"]) for n in r["networks"]] == [
        ("silm-hotspot", "aircraft"), ("home-Farm WiFi", "home")]
    r, _ = call(url + "/api/pi/wifi", {"address": addr, "action": "hotspot", "name": "SILM-Tartu",
                                       "password": "long enough"})
    assert r["ok"] and calls[-1] == ["sudo", "-n", "/usr/local/sbin/silm-wifi", "hotspot", "SILM-Tartu", "long enough"]
    call(url + "/api/pi/wifi", {"address": addr, "action": "country", "country": "fi"})
    assert calls[-1][-2:] == ["country", "FI"]
    call(url + "/api/pi/wifi", {"address": addr, "action": "home-remove", "name": "Farm WiFi"})
    assert calls[-1][-2:] == ["home-remove", "Farm WiFi"]
    r, _ = call(url + "/api/pi/wifi", {"address": addr, "action": "reboot"})
    assert not r["ok"] and "unknown Wi-Fi action" in r["message"]


def test_pi_install_routes(http, monkeypatch):
    """The install job through the Ground Station's HTTP routes (SSH itself: test_piinstall.py)."""
    url, st = http
    assert call(url + "/api/pi/install")[0]["state"] == "idle"
    r, _ = call(url + "/api/pi/install/start", {"host": "silm.local", "user": "pi"})
    assert not r["ok"] and "password" in r["message"]
    seen = {}

    def connect(host, user, password, port=22):
        seen.update(host=host, port=port, user=user)
        raise piinstall.InstallError("cannot reach it (test)")
    monkeypatch.setattr(st.piinstall, "_connect", connect)
    r, _ = call(url + "/api/pi/install/start", {"host": "silm.local:2222", "user": "pi", "password": "pw",
                                                "hotspot_password": "aircraft-1"})
    assert r["ok"], r
    t0 = time.time()
    while call(url + "/api/pi/install")[0]["state"] == "running" and time.time() - t0 < 10:
        time.sleep(0.05)
    s, _ = call(url + "/api/pi/install")
    assert s["state"] == "failed" and "cannot reach" in s["result"]
    assert seen == {"host": "silm.local", "port": 2222, "user": "pi"}
    assert not call(url + "/api/pi/install/cancel", {})[0]["ok"]


def test_finer_tiles_reach_the_pi_and_the_planner(station):
    """recognition.tiles: the Pi's detector.tile_grid, accepted by its own
    validation, and the planner's pixels-on-target grows with it."""
    p2 = full_project()
    p3 = full_project(recognition__tiles="3")
    s2, s3 = P.make_settings(p2), P.make_settings(p3)
    assert s2["detector"]["tile_grid"] == [2, 2] and s3["detector"]["tile_grid"] == [3, 3]
    assert s3["detector"]["tiled"] is True
    assert not P.check_settings_against_config(p3)                   # the Pi accepts it
    assert P.tiles(full_project(recognition__tiles="9")) == 4 and P.tiles(full_project(recognition__tiles="x")) == 2
    b2 = P.plan_body(p2, station.camera)
    b3 = P.plan_body(p3, station.camera)
    assert b2["camera"]["tiles"] == 2 and b3["camera"]["tiles"] == 3
    import survey
    cam2 = survey.Camera(**{k: (tuple(v) if k == "roi" else v) for k, v in b2["camera"].items()})
    cam3 = survey.Camera(**{k: (tuple(v) if k == "roi" else v) for k, v in b3["camera"].items()})
    assert cam3.pixels_at_model(3.0, 100) > cam2.pixels_at_model(3.0, 100)
    assert cam3.max_alt_for(3.0) > cam2.max_alt_for(3.0)
