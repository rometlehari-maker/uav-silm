"""Mission planner: survey geometry, mission safety checks, .waypoints files,
MAVLink upload/download against a fake flight controller, and the GUI's HTTP
API."""

import json
import math
import os
import socket
import sys
import threading
import time
import urllib.request

import pytest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "planner"))

import survey  # noqa: E402
from silm_recon import mavmission as mm  # noqa: E402

LAUNCH = (59.4000, 24.7000)
FIELD = [(59.4030, 24.7030), (59.4030, 24.7135), (59.4066, 24.7135), (59.4066, 24.7030)]


def base(**kw):
    return survey.PlanInput(launch=LAUNCH, area=FIELD, landing_heading_deg=270, **kw)


def survey_wps(r):
    its = r["items"]
    i0 = next(i for i, it in enumerate(its) if it.command == mm.DO_CHANGE_SPEED) + 1
    i1 = next(i for i, it in enumerate(its) if it.command == mm.DO_LAND_START)
    return its[i0:i1]


# ------------------------------------------------------------ geometry

def test_mission_structure_for_no_rc():
    r = survey.plan(base())
    names = [mm.NAMES[it.command] for it in r["items"]]
    assert names[:3] == ["WAYPOINT", "TAKEOFF", "DO_CHANGE_SPEED"]       # home, takeoff, speed
    assert names[-3:] == ["DO_LAND_START", "WAYPOINT", "LAND"]
    assert r["errors"] == []
    assert r["items"][1].params[0] == 15                                 # takeoff pitch
    assert r["items"][-1].lat == LAUNCH[0] and r["items"][-1].alt == 0  # lands at launch by default


def test_swath_spacing_and_coverage():
    r = survey.plan(base(alt_m=100, overlap=0.2))
    cam = survey.Camera()
    assert r["swath_m"] == pytest.approx(cam.swath_m(100), abs=0.1)
    assert r["swath_m"] == pytest.approx(2 * 100 * math.tan(math.radians(40)), rel=1e-3)
    assert r["spacing_m"] == pytest.approx(r["swath_m"] * 0.8, abs=0.1)
    loc = survey.Local(*LAUNCH)
    ys = sorted({round(loc.to_xy(it.lat, it.lon)[1], 1) for it in survey_wps(r)})
    gaps = [b - a for a, b in zip(ys, ys[1:])]
    assert all(g == pytest.approx(r["spacing_m"], abs=0.5) for g in gaps)
    fy = [loc.to_xy(la, lo)[1] for la, lo in FIELD]
    # lines cover the field: first/last line within half a spacing of the edges
    assert ys[0] - min(fy) <= r["spacing_m"] / 2 + 0.5
    assert max(fy) - ys[-1] <= r["spacing_m"] / 2 + 0.5
    for it in survey_wps(r):
        assert it.alt == 100


def test_lines_extend_past_the_field_edges():
    r = survey.plan(base(edge_extend_m=40))
    loc = survey.Local(*LAUNCH)
    fx = [loc.to_xy(la, lo)[0] for la, lo in FIELD]
    xs = [loc.to_xy(it.lat, it.lon)[0] for it in survey_wps(r)]
    assert min(xs) == pytest.approx(min(fx) - 40, abs=1) and max(xs) == pytest.approx(max(fx) + 40, abs=1)


def test_turn_order_keeps_consecutive_lines_a_turn_apart():
    r = survey.plan(base(alt_m=40))                     # narrow swath -> spacing < turn diameter
    assert r["spacing_m"] < 2 * r["turn_radius_m"]
    loc = survey.Local(*LAUNCH)
    ys = [loc.to_xy(it.lat, it.lon)[1] for it in survey_wps(r)][::2]   # one y per line
    gaps = [abs(b - a) for a, b in zip(ys, ys[1:])]
    short = [g for g in gaps if g < 2 * r["turn_radius_m"] - 1]
    assert len(short) <= math.ceil(2 * r["turn_radius_m"] / r["spacing_m"])  # only pass changes
    assert any("interleaved" in w for w in r["warnings"])


@pytest.mark.parametrize("n,spacing,diam", [(7, 50, 60), (10, 20, 75), (5, 100, 70), (1, 30, 70)])
def test_line_order_is_a_permutation(n, spacing, diam):
    o = survey.line_order(n, spacing, diam)
    assert sorted(o) == list(range(n))


def test_explicit_line_angle():
    r = survey.plan(base(line_angle_deg=90))
    assert r["line_angle_deg"] == 90
    wps = survey_wps(r)
    assert abs(wps[0].lon - wps[1].lon) < 1e-6         # north-south line


def test_landing_approach_geometry():
    r = survey.plan(base(approach_len_m=300, approach_alt_m=30))
    loc = survey.Local(*LAUNCH)
    ax, ay = loc.to_xy(*r["approach_start"])
    assert math.hypot(ax, ay) == pytest.approx(300, abs=1)
    assert ax == pytest.approx(300, abs=1)              # landing heading 270 (west): approach from the east
    assert r["glide_deg"] == pytest.approx(math.degrees(math.atan2(30, 300)), abs=0.1)


def test_warnings_fence_altitude_endurance_and_detection():
    r = survey.plan(base(fence_radius_m=600, alt_m=150, alt_limit_m=120, endurance_min=5,
                         target_size_m=0.5))
    text = " ".join(r["warnings"] + r["errors"])
    assert "FENCE_RADIUS" in text and "above the 120 m limit" in text and "exceeds the usable" in text
    assert r["detection"]["verdict"] == "too small" and "fly at or below" in text
    assert r["errors"]                                  # outside fence / above limit are errors


def test_detection_matches_pixels_on_target_tool():
    sys.path.insert(0, os.path.join(HERE, "..", "tools"))
    import pixels_on_target as pot
    r = survey.plan(base(alt_m=100, target_size_m=3.5))
    fx = pot.focal_px(1920, 160, None, "fisheye")
    _, pm = pot.pixels(3.5, 100, fx, 1920, 320, 2, 0.5)
    assert r["detection"]["px_at_model"] == pytest.approx(pm, abs=0.1)


def test_concave_area_gives_multiple_segments():
    L = [(59.4030, 24.7030), (59.4030, 24.7135), (59.4066, 24.7135), (59.4066, 24.7100),
         (59.4040, 24.7085), (59.4066, 24.7065), (59.4066, 24.7030)]     # notch in the north edge
    r = survey.plan(survey.PlanInput(launch=LAUNCH, area=L, alt_m=40, line_angle_deg=0))
    assert r["errors"] == [] and len(survey_wps(r)) >= 2 * r["lines"]


# ------------------------------------------------------------ files + checks

def test_waypoints_file_round_trip():
    r = survey.plan(base())
    text = mm.to_wpl(r["items"])
    assert text.startswith("QGC WPL 110\n")
    back = mm.from_wpl(text)
    assert [b.command for b in back] == [a.command for a in r["items"]]
    for a, b in zip(r["items"], back):
        assert (b.lat, b.lon, b.alt) == pytest.approx((a.lat, a.lon, a.alt), abs=1e-7)
    assert back[0].frame == mm.FRAME_GLOBAL and back[1].frame == mm.FRAME_GLOBAL_RELATIVE_ALT


def test_check_mission_catches_unsafe_no_rc_missions():
    home = mm.Item(mm.NAV_WAYPOINT, *LAUNCH, 0)
    to = mm.Item(mm.NAV_TAKEOFF, *LAUNCH, 50, params=[15, 0, 0, 0])
    wp = mm.Item(mm.NAV_WAYPOINT, 59.405, 24.705, 100)
    dls = mm.Item(mm.DO_LAND_START, *LAUNCH, 0)
    land = mm.Item(mm.NAV_LAND, *LAUNCH, 0)
    ok, _ = mm.check_mission([home, to, wp, dls, wp, land])
    assert ok == []
    assert any("LAND" in e for e in mm.check_mission([home, to, wp])[0])
    assert any("DO_LAND_START" in e for e in mm.check_mission([home, to, wp, land])[0])
    assert any("first item" in e for e in mm.check_mission([home, wp, dls, land])[0])
    assert any("LOITER_UNLIM" in e for e in
               mm.check_mission([home, to, mm.Item(mm.NAV_LOITER_UNLIM, 59.405, 24.705, 100), dls, land])[0])
    assert any("END" in e for e in mm.check_mission([home, to, dls, land, wp])[0])
    far = mm.Item(mm.NAV_WAYPOINT, 59.42, 24.70, 100)                       # 2.2 km
    assert any("FENCE_RADIUS" in e for e in mm.check_mission([home, to, far, dls, land], 1500)[0])


# ------------------------------------------------------------ fake vehicle

def free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class FakeVehicle(threading.Thread):
    """Minimal ArduPilot mission + parameter protocol over UDP."""

    def __init__(self, port, armed=False):
        super().__init__(daemon=True)
        from pymavlink import mavutil
        self.mu = mavutil
        self.m = mavutil.mavlink_connection(f"udpout:127.0.0.1:{port}", source_system=1)
        self.stop_evt = threading.Event()
        self.mission = []
        self.params = {"FENCE_RADIUS": 1500.0}
        self.armed = armed
        self.mode = 0

    def run(self):
        ml = self.mu.mavlink
        mav = self.m.mav
        expect, incoming, last_hb = 0, [], 0.0
        while not self.stop_evt.is_set():
            if time.time() - last_hb > 0.2:
                mav.heartbeat_send(ml.MAV_TYPE_FIXED_WING, ml.MAV_AUTOPILOT_ARDUPILOTMEGA,
                                   (ml.MAV_MODE_FLAG_SAFETY_ARMED if self.armed else 0) |
                                   ml.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED, self.mode, ml.MAV_STATE_STANDBY)
                last_hb = time.time()
            msg = self.m.recv_match(blocking=True, timeout=0.05)
            if msg is None:
                continue
            t = msg.get_type()
            if t == "MISSION_COUNT":
                expect, incoming = msg.count, []
                mav.mission_request_int_send(255, 0, 0)
            elif t == "MISSION_ITEM_INT" and len(incoming) < expect and msg.seq == len(incoming):
                incoming.append(msg)
                if len(incoming) < expect:
                    mav.mission_request_int_send(255, 0, len(incoming))
                else:
                    self.mission = incoming
                    mav.mission_ack_send(255, 0, ml.MAV_MISSION_ACCEPTED)
            elif t == "MISSION_REQUEST_LIST":
                mav.mission_count_send(255, 0, len(self.mission))
            elif t == "MISSION_REQUEST_INT":
                it = self.mission[msg.seq]
                mav.mission_item_int_send(255, 0, it.seq, it.frame, it.command, 0, it.autocontinue,
                                          it.param1, it.param2, it.param3, it.param4, it.x, it.y, it.z)
            elif t == "SET_MODE":
                self.mode = msg.custom_mode
            elif t == "COMMAND_LONG" and msg.command == ml.MAV_CMD_COMPONENT_ARM_DISARM:
                # like ARMING_MIS_ITEMS=13: needs takeoff + DO_LAND_START + land
                cmds = {it.command for it in self.mission}
                if msg.param1 == 1 and not {22, 189, 21} <= cmds:
                    mav.statustext_send(3, b"PreArm: Mission missing items")
                    mav.command_ack_send(msg.command, ml.MAV_RESULT_FAILED)
                else:
                    self.armed = msg.param1 == 1
                    mav.command_ack_send(msg.command, ml.MAV_RESULT_ACCEPTED)
                    last_hb = 0.0
            elif t == "PARAM_SET":
                self.params[msg.param_id] = msg.param_value
                mav.param_value_send(msg.param_id.encode(), msg.param_value, 9, 1, 0)
            elif t == "PARAM_REQUEST_READ" and msg.param_id in self.params:
                mav.param_value_send(msg.param_id.encode(), self.params[msg.param_id], 9, 1, 0)


@pytest.fixture
def vehicle():
    pytest.importorskip("pymavlink")
    port = free_port()
    v = FakeVehicle(port)
    v.start()
    yield port, v
    v.stop_evt.set()


def test_upload_download_round_trip(vehicle):
    from pymavlink import mavutil
    port, v = vehicle
    items = survey.plan(base())["items"]
    m = mavutil.mavlink_connection(f"udpin:127.0.0.1:{port}", source_system=255)
    assert m.wait_heartbeat(timeout=5)
    mm.upload(m, items)
    assert [x.command for x in v.mission] == [it.command for it in items]
    back = mm.download(m)
    assert [b.command for b in back] == [it.command for it in items]
    for a, b in zip(items[1:], back[1:]):
        assert (b.lat, b.lon, b.alt) == pytest.approx((a.lat, a.lon, a.alt), abs=1e-6)
    assert mm.set_param(m, "FENCE_RADIUS", 1200) and mm.get_param(m, "FENCE_RADIUS") == 1200
    m.close()


# ------------------------------------------------------------ GUI server

@pytest.fixture
def server():
    import app
    srv = app.make_server(0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def call(url, data=None):
    req = urllib.request.Request(url, data=None if data is None else json.dumps(data).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        body = r.read().decode()
        return json.loads(body) if r.headers.get_content_type() == "application/json" else body


def plan_body(srv):
    d = call(srv + "/api/defaults")
    b = dict(d["plan"], launch=list(LAUNCH), area=[list(p) for p in FIELD], landing=list(LAUNCH),
             landing_heading_deg=270, target_size_m=3.5)
    return d, b


def test_api_defaults_come_from_aircraft_config(server):
    d = call(server + "/api/defaults")
    c = d["plan"]["camera"]
    assert c["lens"] == "fisheye" and c["hfov_deg"] == 160 and c["width"] == 1920
    assert c["roi"] == [0.25, 0.25, 0.75, 0.75] and c["tiles"] == 2
    assert d["plan"]["fence_radius_m"] == 1500 and d["plan"]["speed_mps"] == 19
    assert any(t["name"] == "combine" and t["size_m"] == 3.5 for t in d["targets"])


def test_api_plan_and_waypoints(server):
    _, b = plan_body(server)
    r = call(server + "/api/plan", b)
    assert r["errors"] == [] and r["items"][1]["name"] == "TAKEOFF" and r["items"][-1]["name"] == "LAND"
    assert r["detection"]["verdict"] == "good" and len(r["path"]) >= 4
    text = call(server + "/api/waypoints", b)
    assert text.startswith("QGC WPL 110") and len(mm.from_wpl(text)) == len(r["items"])


def test_api_serves_page_and_blocks_traversal(server):
    page = call(server + "/")
    assert "<title>SILM Mission Planner</title>" in page and "leaflet@1.9.4" in page
    with pytest.raises(urllib.error.HTTPError):
        call(server + "/static/../app.py")


def test_api_bad_plan_is_a_clean_400(server):
    with pytest.raises(urllib.error.HTTPError) as e:
        call(server + "/api/plan", {"launch": list(LAUNCH), "area": [[59.4, 24.7]]})
    assert e.value.code == 400


def test_api_upload_to_fake_vehicle(server, vehicle):
    port, v = vehicle
    _, b = plan_body(server)
    r = call(server + "/api/upload", {"plan": b, "port": f"udpin:127.0.0.1:{port}", "set_fence": True})
    assert r["ok"], r["message"]
    assert "identical" in r["message"] and v.params["FENCE_RADIUS"] >= 1000
    assert v.mission[-1].command == mm.NAV_LAND


def test_api_upload_refuses_armed_vehicle(server):
    pytest.importorskip("pymavlink")
    port = free_port()
    v = FakeVehicle(port, armed=True)
    v.start()
    try:
        _, b = plan_body(server)
        r = call(server + "/api/upload", {"plan": b, "port": f"udpin:127.0.0.1:{port}"})
        assert not r["ok"] and "ARMED" in r["message"] and v.mission == []
    finally:
        v.stop_evt.set()


def test_api_arm_checks_mission_then_arms_in_auto_and_disarms(server, vehicle):
    port, v = vehicle
    url = f"udpin:127.0.0.1:{port}"
    _, b = plan_body(server)
    r = call(server + "/api/upload", {"plan": b, "port": url})
    assert r["ok"], r
    r = call(server + "/api/arm", {"action": "arm", "port": url})
    assert r["ok"], r
    assert v.armed and v.mode == mm.MODE_AUTO and "throw" in r["message"]
    r = call(server + "/api/arm", {"action": "disarm", "port": url})
    assert r["ok"] and not v.armed


def test_api_arm_refuses_aircraft_without_a_landing(server, vehicle):
    from pymavlink import mavutil
    port, v = vehicle
    items = survey.plan(base())["items"]
    no_land = [it for it in items if it.command not in (mm.NAV_LAND, mm.DO_LAND_START)]
    m = mavutil.mavlink_connection(f"udpin:127.0.0.1:{port}", source_system=255)
    assert m.wait_heartbeat(timeout=5)
    mm.upload(m, no_land)
    m.close()
    r = call(server + "/api/arm", {"action": "arm", "port": f"udpin:127.0.0.1:{port}"})
    assert not r["ok"] and "LAND" in r["message"] and not v.armed


def test_arm_disarm_reports_autopilot_refusal(vehicle):
    from pymavlink import mavutil
    port, v = vehicle                       # empty mission: the fake refuses like ArduPilot
    m = mavutil.mavlink_connection(f"udpin:127.0.0.1:{port}", source_system=255)
    assert m.wait_heartbeat(timeout=5)
    ok, msg = mm.arm_disarm(m, True)
    assert not ok and "Mission missing items" in msg


def test_preflight_mission_check_against_vehicle(vehicle):
    from pymavlink import mavutil
    sys.path.insert(0, os.path.join(HERE, "..", "tools"))
    import preflight
    port, v = vehicle
    m = mavutil.mavlink_connection(f"udpin:127.0.0.1:{port}", source_system=255)
    assert m.wait_heartbeat(timeout=5)
    mm.upload(m, survey.plan(base())["items"])
    preflight.RESULTS.clear()
    preflight.check_onboard_mission(m, 1500, 120)
    assert [r[1] for r in preflight.RESULTS] == ["PASS"], preflight.RESULTS
    items = survey.plan(base())["items"]
    mm.upload(m, items[:-1])                # LAND removed
    preflight.RESULTS.clear()
    preflight.check_onboard_mission(m, 1500, 120)
    assert any(r[1] == "FAIL" and "LAND" in r[2] for r in preflight.RESULTS)
