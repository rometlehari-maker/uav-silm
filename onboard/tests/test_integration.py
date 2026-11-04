"""Integration tests of code paths unit tests don't reach:
  * TelemetryLink against a fake ArduPlane speaking real MAVLink over UDP
    (mode/arm decoding, position, attitude, history, GPS monitor, reconnect)
  * Recon.run(): the real main loop end to end, with a file camera
"""

import json
import math
import os
import socket
import threading
import time

import numpy as np
import pytest

mavutil = pytest.importorskip("pymavlink.mavutil")
cv2 = pytest.importorskip("cv2")

from silm_recon.failsafe import GpsHealthMonitor  # noqa: E402
from silm_recon.recon import Recon  # noqa: E402
from silm_recon.telemetry import TelemetryLink  # noqa: E402


def free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class FakePlane(threading.Thread):
    """Sends ArduPlane-like MAVLink at 20 Hz: AUTO, armed, flying north."""

    def __init__(self, port, fix_type=3):
        super().__init__(daemon=True)
        self.m = mavutil.mavlink_connection(f"udpout:127.0.0.1:{port}", source_system=1)
        self.stop_evt = threading.Event()
        self.pause = threading.Event()
        self.fix_type = fix_type
        self.t0 = time.time()

    def run(self):
        mav = self.m.mav
        ml = mavutil.mavlink
        while not self.stop_evt.is_set():
            if self.pause.is_set():
                time.sleep(0.05)
                continue
            t = time.time() - self.t0
            mav.heartbeat_send(ml.MAV_TYPE_FIXED_WING, ml.MAV_AUTOPILOT_ARDUPILOTMEGA,
                               ml.MAV_MODE_FLAG_SAFETY_ARMED | ml.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
                               10, ml.MAV_STATE_ACTIVE)                 # custom mode 10 = AUTO
            lat = 59.40 + t * 19 / 111320.0                             # 19 m/s north
            mav.global_position_int_send(int(t * 1000), int(lat * 1e7), int(24.70 * 1e7),
                                         130000, 100000, 1900, 0, 0, 0)
            mav.attitude_send(int(t * 1000), 0.05, -0.02, math.radians(1.0), 0, 0, 0)
            mav.vfr_hud_send(19.0, 19.0, 0, 50, 100.0, 0.0)
            mav.gps_raw_int_send(int(t * 1e6), self.fix_type, int(lat * 1e7), int(24.70 * 1e7),
                                 130000, 80, 100, 1900, 0, 14)
            mav.ekf_status_report_send((1 << 4) | (1 << 2), 0.1, 0.1, 0.1, 0.1, 0.1)
            time.sleep(0.05)


def wait_for(cond, timeout=8.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_telemetry_against_fake_arduplane():
    port = free_port()
    mon = GpsHealthMonitor(log_dir=None)
    link = TelemetryLink(f"udpin:127.0.0.1:{port}", msg_callback=mon.ingest)
    link.start()
    plane = FakePlane(port)
    plane.start()
    try:
        assert wait_for(lambda: link.state.snapshot().telemetry_ok())
        st = link.state.snapshot()
        assert st.mode == "AUTO" and st.armed
        assert st.lat == pytest.approx(59.40, abs=0.01) and st.lon == pytest.approx(24.70, abs=1e-6)
        assert st.rel_alt == pytest.approx(100.0) and st.roll == pytest.approx(0.05)
        assert st.ground_speed == pytest.approx(19.0)
        assert mon.state == "OK"
        # history: state half a second ago is ~9.5 m further south
        assert wait_for(lambda: len(link._hist) > 40)   # > 1 s of history
        now = time.time()
        past = link.state_at(now - 0.5)
        cur = link.state_at(now)
        assert (cur.lat - past.lat) * 111320 == pytest.approx(9.5, abs=2.5)

        # link drop: no messages for > 2 s -> reader reconnects, data resumes
        plane.pause.set()
        time.sleep(3.0)
        assert link.state.snapshot().age_pos > 2.0
        plane.pause.clear()
        assert wait_for(lambda: link.state.snapshot().age_pos < 0.5, timeout=20)
    finally:
        plane.stop_evt.set()
        link.stop()


def test_gps_monitor_sees_loss_from_real_messages():
    port = free_port()
    mon = GpsHealthMonitor(log_dir=None)
    link = TelemetryLink(f"udpin:127.0.0.1:{port}", msg_callback=mon.ingest)
    link.start()
    plane = FakePlane(port, fix_type=1)          # no fix
    plane.start()
    try:
        assert wait_for(lambda: mon.state == "LOST")
    finally:
        plane.stop_evt.set()
        link.stop()


class FakeLink:
    """Stands in for TelemetryLink: a flying aircraft."""
    master = None

    def __init__(self):
        from silm_recon.telemetry import StateView
        self.view = StateView(True, 59.40, 24.70, 130, 100, 0.0, 0.0, 0.0, 19, True, "AUTO",
                              0.1, 0.1, 0.1)
        self.started = self.stopped = False

        class S:
            pass
        self.state = S()
        self.state.snapshot = lambda: self.view

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def state_at(self, t):
        return self.view


def test_main_loop_runs_end_to_end(tmp_path):
    imgs = tmp_path / "imgs"
    imgs.mkdir()
    f = np.full((1080, 1920, 3), 60, np.uint8)
    f[500:580, 920:1000] = (20, 120, 255)
    for i in range(3):
        cv2.imwrite(str(imgs / f"{i}.jpg"), f)
    cfg = {"camera": {"source": "file", "device": str(imgs)},
           "detector": {"enabled": False}, "targets": [],
           "fire": {"enabled": True, "detect_smoke": False},
           "georef": {"lens": "fisheye", "hfov_deg": 160},
           "gimbal": {"enabled": False}, "failsafe": {"enabled": True, "max_rate_hz": 5},
           "confirm": {"frames": 2}, "health": {"interval_s": 0.5},
           "storage": {"output_dir": str(tmp_path / "out")},
           "activation": {"active_modes": ["AUTO"], "min_alt_m": 20}}
    link = FakeLink()
    r = Recon(cfg, link=link)
    threading.Timer(2.5, lambda: setattr(r, "_stop", True)).start()
    r.run()                                         # returns when _stop is set
    assert link.started and link.stopped
    d = r.store.dir
    recs = [json.loads(ln) for ln in open(os.path.join(d, "detections.ndjson"))]
    assert recs and recs[0]["detections"][0]["label"] == "fire"
    assert recs[0]["aircraft"]["mode"] == "AUTO"
    assert len(open(os.path.join(d, "track.ndjson")).readlines()) >= 2
    assert os.path.getsize(os.path.join(d, "health.ndjson")) > 0
    assert len(recs) == 1          # confirmed once, then de-duplicated


def test_main_loop_stays_idle_when_not_flying(tmp_path):
    imgs = tmp_path / "imgs"
    imgs.mkdir()
    cv2.imwrite(str(imgs / "a.jpg"), np.zeros((108, 192, 3), np.uint8))
    cfg = {"camera": {"source": "file", "device": str(imgs)}, "detector": {"enabled": False},
           "fire": {"enabled": True}, "georef": {}, "storage": {"output_dir": str(tmp_path / "o")},
           "activation": {"active_modes": ["AUTO"], "min_alt_m": 20}}
    link = FakeLink()
    link.view = link.view.__class__(**{**link.view.__dict__, "mode": "MANUAL"})
    r = Recon(cfg, link=link)
    threading.Timer(1.0, lambda: setattr(r, "_stop", True)).start()
    r.run()
    assert r._frame_no == 0        # never processed a frame on the ground / in manual
