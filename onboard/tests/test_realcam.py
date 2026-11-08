"""Wiring for the real camera: telemetry interpolation to the frame time,
detection inside a region of interest, and gimbal-reported tilt in the
pipeline."""

import json
import math
import os

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from silm_recon.detector import Detection, ObjectDetector  # noqa: E402
from silm_recon.recon import Recon  # noqa: E402
from silm_recon.telemetry import StateView, interpolate_state  # noqa: E402


def test_interpolate_state_lerps_and_wraps_heading():
    hist = [(10.0, 58.0, 24.0, 100.0, 0.0, 0.0, math.radians(350)),
            (11.0, 58.001, 24.002, 102.0, 0.2, -0.1, math.radians(10))]
    lat, lon, alt, roll, pitch, yaw = interpolate_state(hist, 10.5)
    assert lat == pytest.approx(58.0005) and alt == pytest.approx(101.0)
    assert roll == pytest.approx(0.1) and pitch == pytest.approx(-0.05)
    assert math.degrees(yaw) % 360 == pytest.approx(0.0, abs=1e-6)   # 350 -> 10 passes north
    assert interpolate_state(hist, 9.0)[0] == 58.0                   # clamped to the start
    assert interpolate_state(hist, 20.0) is None                     # far outside: no guess
    assert interpolate_state([], 10.0) is None


def test_detect_roi_maps_boxes_back_to_full_frame():
    class Fake(ObjectDetector):
        def __init__(self):
            self.seen = []

        def detect(self, img):
            self.seen.append(img.shape)
            return [Detection("combine", 0.9, (0.5, 0.5, 0.75, 1.0))]   # in crop coords

    d = Fake()
    frame = np.zeros((1080, 1920, 3), np.uint8)
    out = d.detect_roi(frame, (0.25, 0.0, 0.75, 1.0))                 # central half
    assert d.seen == [(1080, 960, 3)]
    assert out[0].box == pytest.approx((0.5, 0.5, 0.625, 1.0))


class FakeLink:
    master = None

    def stop(self):
        pass


class FakeCamera:
    def __init__(self, frame):
        self.frame = frame
        self.frame_size = (frame.shape[1], frame.shape[0])

    def read(self):
        return self.frame.copy()

    def release(self):
        pass


def test_pipeline_uses_gimbal_tilt_and_cancels_aircraft_pitch(tmp_path):
    frame = np.full((1080, 1920, 3), 60, np.uint8)
    frame[500:580, 920:1000] = (20, 120, 255)        # fire at the image centre
    cfg = {"camera": {}, "detector": {"enabled": False}, "targets": [],
           "fire": {"enabled": True, "detect_smoke": False},
           # fixed mount says 45 deg (looking ahead); the gimbal will say 90
           "georef": {"lens": "fisheye", "hfov_deg": 160, "forward_tilt_deg": 45},
           "gimbal": {"enabled": False, "angle_frame": "earth"},
           "failsafe": {"enabled": False}, "confirm": {"frames": 1},
           "health": {"interval_s": 100}, "storage": {"output_dir": str(tmp_path)},
           "activation": {}}
    r = Recon(cfg, link=FakeLink(), camera=FakeCamera(frame))
    st = StateView(True, 58.378, 24.5, 140, 100, 0.0, math.radians(12), 0.0, 19,
                   True, "AUTO", 0.1, 0.1, 0.1)                       # nose 12 deg up
    saved = r.process_frame(frame, st, now=1.0, cam_tilt_deg=90.0)
    assert saved
    rec = json.loads(open(os.path.join(r.store.dir, "detections.ndjson")).readline())
    dn = (rec["detections"][0]["ground_lat"] - 58.378) * 111320
    assert abs(dn) < 3.0      # straight below, not 100 m ahead (45 deg) or 21 m (12 deg pitch)
    r._shutdown()


def test_letterbox_preprocess_and_box_mapping():
    d = object.__new__(ObjectDetector)
    d._in_w = d._in_h = 320
    d._in_dtype, d._in_quant, d._nchw, d.fmt = np.float32, (0.0, 0), False, "yolo"
    frame = np.full((1080, 1920, 3), 200, np.uint8)
    t = d._preprocess(frame)[0]
    r, px, py, w0, h0 = d._lb
    assert r == pytest.approx(1 / 6) and (px, py) == (0, 70) and (w0, h0) == (1920, 1080)
    assert t[0, 0, 0] == pytest.approx(114 / 255) and t[160, 160, 0] == pytest.approx(200 / 255)

    class FakeInterp:
        def get_tensor(self, i):
            out = np.zeros((1, 5, 10), np.float32)        # 10 anchors, like a (tiny) real head
            out[0, :, 0] = (0.5, 0.5, 0.1, 0.1, 0.9)     # model-input coords, class 0
            return out

    d.interpreter, d._out = FakeInterp(), [{"index": 0, "quantization": (0.0, 0)}]
    d.labels, d.score_threshold, d.iou_threshold = {0: "car"}, 0.5, 0.45
    (det,) = d._detect_yolo()
    x0, y0, x1, y1 = det.box
    assert (x0, x1) == pytest.approx((0.45, 0.55))
    assert y0 == pytest.approx((0.45 * 320 - 70) * 6 / 1080)
    assert y1 == pytest.approx((0.55 * 320 - 70) * 6 / 1080)


def test_ssd_keeps_plain_resize():
    d = object.__new__(ObjectDetector)
    d._in_w = d._in_h = 300
    d._in_dtype, d._in_quant, d._nchw, d.fmt = np.uint8, (0.0, 0), False, "ssd"
    t = d._preprocess(np.zeros((1080, 1920, 3), np.uint8))
    assert d._lb is None and t.shape == (1, 300, 300, 3)


def test_preflight_required_fps_and_bench_run(tmp_path):
    import subprocess
    import sys
    import yaml
    tools = os.path.join(os.path.dirname(__file__), "..", "tools")
    sys.path.insert(0, tools)
    import preflight
    cfg = {"camera": {"width": 1920, "height": 1080},
           "georef": {"lens": "fisheye", "hfov_deg": 160},
           "detector": {"roi": [0.25, 0.25, 0.75, 0.75]}}
    need, secs = preflight.required_fps(cfg, 100, 19)
    assert secs == pytest.approx(4.37, abs=0.05) and need == pytest.approx(2 / secs)
    # a bench run with no hardware: camera from a folder, model disabled
    imgs = tmp_path / "imgs"
    imgs.mkdir()
    cv2.imwrite(str(imgs / "a.jpg"), np.zeros((108, 192, 3), np.uint8))
    c = {"camera": {"source": "file", "device": str(imgs), "width": 192, "height": 108},
         "detector": {"enabled": False}, "georef": {"lens": "fisheye", "hfov_deg": 160},
         "storage": {"output_dir": str(tmp_path / "out")}, "telemetry": {"connection": "x"},
         "activation": {}}
    p = tmp_path / "c.yaml"
    p.write_text(yaml.safe_dump(c))
    r = subprocess.run([sys.executable, os.path.join(tools, "preflight.py"), "--config", str(p),
                        "--settings", str(tmp_path / "none.yaml"), "--skip", "fc,gimbal"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout
    assert "[PASS] camera" in r.stdout and "[SKIP] model" in r.stdout and "0 FAIL" in r.stdout
    # an incomplete configuration is a FAIL line, and the other parts are still checked
    del c["activation"]
    p.write_text(yaml.safe_dump(c))
    r = subprocess.run([sys.executable, os.path.join(tools, "preflight.py"), "--config", str(p),
                        "--settings", str(tmp_path / "none.yaml"), "--skip", "fc,gimbal"],
                       capture_output=True, text=True)
    assert r.returncode == 1 and "[FAIL] config" in r.stdout and "activation" in r.stdout
    assert "[PASS] camera" in r.stdout
