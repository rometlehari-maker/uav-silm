"""Raspberry Pi 4 (2 GB) robustness: the flight program must keep going when
the camera is late or drops out, survive a bad frame, adapt to the camera's
real frame size, watch memory, and publish a status file for the ground agent."""

import json
import os
import threading
import time

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from silm_recon import camera as camera_mod  # noqa: E402
from silm_recon.camera import Camera, CameraError  # noqa: E402
from silm_recon.health import HealthMonitor, read_mem_available_mb  # noqa: E402
from silm_recon.recon import Recon  # noqa: E402
from silm_recon.status import armed_or_unknown_flying, read_status, write_status  # noqa: E402
from silm_recon.telemetry import StateView  # noqa: E402


class FakeCap:
    def __init__(self, shape=(720, 1280, 3)):
        self.shape = shape

    def isOpened(self):
        return True

    def read(self):
        time.sleep(0.005)
        return True, np.zeros(self.shape, np.uint8)

    def release(self):
        pass


def _patch_open(monkeypatch, open_fn):
    monkeypatch.setattr(Camera, "_rtsp_open_fn", lambda self: open_fn)


# ------------------------------------------------------------------ camera --

def test_camera_not_sending_at_power_on_is_not_fatal(monkeypatch):
    def never():
        raise RuntimeError("camera still booting")
    _patch_open(monkeypatch, never)
    cam = Camera({"source": "rtsp", "open_timeout_s": 0.3}, strict=False)   # must not raise
    assert cam.read_with_time() == (None, 0.0)
    assert cam.seconds_since_frame() is None
    assert cam.frame_size == (1920, 1080)          # config size until the first frame
    cam.release()


def test_camera_strict_mode_still_reports_a_dead_camera(monkeypatch):
    def never():
        raise RuntimeError("no camera")
    _patch_open(monkeypatch, never)
    with pytest.raises(CameraError):
        Camera({"source": "rtsp", "open_timeout_s": 0.3}, strict=True)


def test_late_camera_starts_delivering_frames(monkeypatch):
    calls = []

    def late():
        calls.append(1)
        if len(calls) < 2:
            raise RuntimeError("not yet")
        return FakeCap()
    _patch_open(monkeypatch, late)
    cam = Camera({"source": "rtsp", "open_timeout_s": 0.2}, strict=False)
    t0 = time.time()
    frame = None
    while frame is None and time.time() - t0 < 5:
        frame, _ = cam.read_with_time()
        time.sleep(0.02)
    assert frame is not None and frame.shape == (720, 1280, 3)
    assert cam.seconds_since_frame() < 1.0
    cam.release()


# ------------------------------------------------------------------ status --

def test_status_file_is_atomic_and_tracks_freshness(tmp_path):
    p = str(tmp_path / "run" / "status.json")
    assert write_status(p, {"armed": True, "rel_alt_m": 80})
    st = read_status(p)
    assert st["fresh"] and st["armed"] and armed_or_unknown_flying(st)
    assert not read_status(p, now=time.time() + 60)["fresh"]       # program stopped
    assert not [f for f in os.listdir(tmp_path / "run") if f.startswith(".status-")]
    with open(p, "w") as fh:
        fh.write("{half a fi")
    assert read_status(p) == {"fresh": False}
    assert read_status(str(tmp_path / "missing.json")) == {"fresh": False}


def test_status_write_never_raises_on_unwritable_place(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    assert write_status(str(blocker / "status.json"), {"armed": False}) is False


def test_landed_disarmed_is_not_flying():
    assert not armed_or_unknown_flying({"fresh": True, "armed": False, "rel_alt_m": 0.4})
    assert armed_or_unknown_flying({"fresh": True, "armed": False, "rel_alt_m": 40})
    assert not armed_or_unknown_flying({"fresh": False, "armed": True})


# ------------------------------------------------------------------ recon --

class FakeState:
    def __init__(self, st):
        self.st = st

    def snapshot(self):
        return self.st


class FakeLink:
    master = None

    def __init__(self, st):
        self.state = FakeState(st)

    def start(self):
        pass

    def stop(self):
        pass


class SeqCamera:
    """Hands out frames of a given size; frame_size is what the config said."""

    def __init__(self, shape, cfg_size=(1920, 1080)):
        self.shape = shape
        self.frame_size = cfg_size

    def read_with_time(self):
        return np.zeros(self.shape, np.uint8), time.time()

    def read(self):
        return self.read_with_time()[0]

    def seconds_since_frame(self, now=None):
        return 0.1

    def release(self):
        pass


def flying(**kw):
    base = dict(have_fix=True, lat=58.378, lon=24.5, alt_amsl=140.0, rel_alt=100.0, roll=0.0,
                pitch=0.0, yaw=0.0, ground_speed=19.0, armed=True, mode="AUTO",
                age_pos=0.1, age_att=0.1, age_hb=0.1)
    base.update(kw)
    return StateView(**base)


def make(tmp_path, st, shape=(720, 1280, 3)):
    cfg = {"camera": {"source": "file"}, "detector": {"enabled": False}, "targets": [],
           "fire": {"enabled": True, "detect_smoke": False}, "georef": {"hfov_deg": 86},
           "failsafe": {"enabled": False}, "confirm": {"frames": 1},
           "health": {"interval_s": 0.2}, "storage": {"output_dir": str(tmp_path / "caps")},
           "activation": {}, "status": {"path": str(tmp_path / "status.json")}}
    return Recon(cfg, link=FakeLink(st), camera=SeqCamera(shape))


def _run_for(r, seconds):
    threading.Timer(seconds, lambda: setattr(r, "_stop", True)).start()
    r.run()


def test_geo_referencing_adapts_to_the_real_frame_size(tmp_path):
    r = make(tmp_path, flying())
    assert (r.geo.W, r.geo.H) == (1920, 1080)
    r.process_frame(np.zeros((720, 1280, 3), np.uint8), flying(), now=1.0)
    assert (r.geo.W, r.geo.H) == (1280, 720)
    r._shutdown()


def test_a_crashing_frame_does_not_stop_the_flight_program(tmp_path):
    r = make(tmp_path, flying())
    calls = []

    def boom(*a, **k):
        calls.append(1)
        raise ValueError("corrupt frame")
    r.process_frame = boom
    _run_for(r, 0.6)                      # returns normally: the loop survived
    assert len(calls) > 3 and r.frame_errors == len(calls)


def test_status_file_reports_state_for_the_ground_agent(tmp_path):
    r = make(tmp_path, flying(armed=False, mode="AUTO", rel_alt=0.0))
    _run_for(r, 0.5)
    st = read_status(str(tmp_path / "status.json"))
    assert st["fresh"] and st["armed"] is False and st["mode"] == "AUTO"
    assert st["recording"] is False and st["have_fix"] is True
    assert st["mission_dir"].startswith(str(tmp_path / "caps"))
    assert st["camera_frame_age_s"] == 0.1


def test_status_shows_saved_pictures_while_flying(tmp_path):
    f = np.full((720, 1280, 3), 60, np.uint8)
    f[300:380, 900:1000] = (20, 120, 255)       # "fire" -> one saved picture
    r = make(tmp_path, flying())
    r.camera.read_with_time = lambda: (f.copy(), time.time())
    _run_for(r, 0.6)
    st = read_status(str(tmp_path / "status.json"))
    assert st["armed"] and st["recording"] and st["saved"] >= 1
    assert st["last_saved"].endswith(".jpg")
    rec = json.loads(open(os.path.join(st["mission_dir"], "detections.ndjson")).readline())
    assert rec["detections"][0]["label"] == "fire"


# ------------------------------------------------------------------ health --

def test_memory_is_read_and_low_memory_warns(tmp_path):
    mi = tmp_path / "meminfo"
    mi.write_text("MemTotal:  1919644 kB\nMemFree:   80000 kB\nMemAvailable:  150528 kB\n")
    assert read_mem_available_mb(str(mi)) == pytest.approx(147.0)
    h = HealthMonitor({"interval_s": 0}, storage_dir=str(tmp_path), temp_reader=lambda: 60.0,
                      throttle_reader=lambda: 0, disk_reader=lambda: 5000.0,
                      mem_reader=lambda: 147.0)
    s = h.maybe_sample(now=100.0)
    assert s["mem_available_mb"] == 147.0
    assert any("memory low" in w for w in s["warnings"])


def test_pi4_temperature_threshold_and_unplugged_storage(tmp_path):
    def gone():
        raise OSError("USB stick pulled")
    h = HealthMonitor({}, storage_dir=str(tmp_path), temp_reader=lambda: 72.0,
                      throttle_reader=lambda: 0, disk_reader=gone, mem_reader=lambda: 900.0)
    s = h.maybe_sample(now=100.0)
    assert s["warnings"] == []           # 72 C is normal for a Pi 4 (warn from 75 C)
    assert s["free_mb"] is None and not h.critical_disk
    h2 = HealthMonitor({}, storage_dir=str(tmp_path), temp_reader=lambda: 78.0,
                       throttle_reader=lambda: 0, disk_reader=lambda: 9000.0,
                       mem_reader=lambda: 900.0)
    assert "CPU 78C" in h2.maybe_sample(now=100.0)["warnings"]


def test_camera_preview_only_on_the_ground_and_only_when_asked(tmp_path):
    ground = flying(armed=False, rel_alt=0.0)
    r = make(tmp_path, ground)
    pv, req = tmp_path / "preview.jpg", tmp_path / "preview_request"
    assert not r.maybe_preview(ground) and not pv.exists()          # nobody asked
    req.write_text("x")
    assert r.maybe_preview(ground) and pv.read_bytes()[:2] == b"\xff\xd8"
    assert not r.maybe_preview(ground)                              # at most once a second
    assert r.maybe_preview(ground, now=time.time() + 1.5)
    old = time.time() - 60
    os.utime(req, (old, old))
    assert not r.maybe_preview(ground, now=time.time() + 5)          # the request ran out
    req.write_text("x")
    assert not r.maybe_preview(flying(), now=time.time() + 10)      # never when armed
    r._shutdown()


def test_preview_is_written_by_the_running_program(tmp_path):
    r = make(tmp_path, flying(armed=False, mode="AUTO", rel_alt=0.0))
    (tmp_path / "preview_request").write_text("x")
    _run_for(r, 0.6)
    assert (tmp_path / "preview.jpg").stat().st_size > 100
