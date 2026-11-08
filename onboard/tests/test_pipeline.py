"""End-to-end test of the onboard pipeline (Recon.process_frame) with an
injected camera and telemetry state, plus a YOLO-decoder unit test."""

import json
import math
import os

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from silm_recon.detector import ObjectDetector  # noqa: E402
from silm_recon.recon import Recon  # noqa: E402
from silm_recon.telemetry import StateView  # noqa: E402


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


def flying_state(**kw):
    base = dict(have_fix=True, lat=58.3780, lon=24.5000, alt_amsl=140.0, rel_alt=100.0,
                roll=0.0, pitch=0.0, yaw=0.0, ground_speed=19.0, armed=True, mode="AUTO",
                age_pos=0.1, age_att=0.1, age_hb=0.1)
    base.update(kw)
    return StateView(**base)


def fire_frame():
    f = np.full((720, 1280, 3), 60, np.uint8)
    f[300:380, 900:1000] = (20, 120, 255)  # orange "fire" right of centre
    return f


def make_recon(tmp_path, **over):
    cfg = {
        "camera": {"source": "file"},
        "detector": {"enabled": False},
        "targets": [],
        "fire": {"enabled": True, "detect_smoke": False},
        "georef": {"hfov_deg": 86},
        "failsafe": {"enabled": False},
        "confirm": {"frames": 2, "window_s": 3, "radius_m": 30},
        "health": {"interval_s": 1},
        "storage": {"output_dir": str(tmp_path)},
        "activation": {},
    }
    for k, v in over.items():
        cfg[k] = v
    return Recon(cfg, link=FakeLink(), camera=FakeCamera(fire_frame()))


def test_pipeline_confirms_geolocates_and_saves(tmp_path):
    r = make_recon(tmp_path)
    st = flying_state()
    assert r.process_frame(r.camera.read(), st, now=1000.0) == []        # 1st sighting
    saved = r.process_frame(r.camera.read(), st, now=1000.5)              # confirmed
    assert len(saved) == 1
    assert r.process_frame(r.camera.read(), st, now=1001.0) == []        # not resaved

    rec = json.loads(open(os.path.join(r.store.dir, "detections.ndjson")).readline())
    det = rec["detections"][0]
    assert det["label"] == "fire"
    # Fire is right of centre with heading north -> east of the aircraft, and
    # slightly north (above centre). Check against the camera geometry.
    dn = (det["ground_lat"] - 58.3780) * 111320
    de = (det["ground_lon"] - 24.5000) * 111320 * math.cos(math.radians(58.378))
    u, v = (950 / 1280), (340 / 720)
    exp_e = 100 * (u * 1280 - 640) / r.geo.fx
    exp_n = -100 * (v * 720 - 360) / r.geo.fy
    assert de == pytest.approx(exp_e, abs=1.0) and dn == pytest.approx(exp_n, abs=1.0)
    assert os.path.exists(os.path.join(r.store.dir, rec["jpg"]))
    r._shutdown()


def test_critical_storage_stops_saving(tmp_path):
    r = make_recon(tmp_path)
    r.health.critical_disk = True
    st = flying_state()
    r.process_frame(r.camera.read(), st, now=1.0)
    assert r.process_frame(r.camera.read(), st, now=1.5) == []
    r._shutdown()


def test_confirm_disabled_saves_first_frame(tmp_path):
    r = make_recon(tmp_path, confirm={"frames": 1})
    assert len(r.process_frame(r.camera.read(), flying_state(), now=1.0)) == 1
    r._shutdown()


# ------------------------------------------------------------- YOLO decode --

class FakeInterpreter:
    def __init__(self, out, quant=(0.0, 0)):
        self.out = out
        self.quant = quant

    def get_tensor(self, idx):
        return self.out


def make_yolo_detector(out, labels, quant=(0.0, 0)):
    d = object.__new__(ObjectDetector)
    d.interpreter = FakeInterpreter(out)
    d._out = [{"index": 0, "quantization": quant}]
    d.labels = dict(enumerate(labels))
    d.score_threshold = 0.4
    d.iou_threshold = 0.45
    d.fmt = "yolo"
    return d


def yolo_tensor(boxes, nc, n=50, transpose=False):
    """boxes: list of (cx, cy, w, h, cls, score) -> [1, 4+nc, n] tensor."""
    t = np.zeros((4 + nc, n), np.float32)
    for i, (cx, cy, w, h, c, s) in enumerate(boxes):
        t[:4, i] = (cx, cy, w, h)
        t[4 + c, i] = s
    t = t[None]
    return np.transpose(t, (0, 2, 1)) if transpose else t


@pytest.mark.parametrize("transpose", [False, True])
def test_yolo_decode_nms_and_layout(transpose):
    out = yolo_tensor([
        (0.30, 0.30, 0.10, 0.10, 1, 0.90),   # combine
        (0.305, 0.30, 0.10, 0.10, 1, 0.80),  # overlapping duplicate -> suppressed
        (0.70, 0.60, 0.20, 0.10, 0, 0.70),   # tractor
        (0.50, 0.50, 0.10, 0.10, 2, 0.20),   # below threshold
    ], nc=3, transpose=transpose)
    d = make_yolo_detector(out, ["tractor", "combine", "car"])
    dets = sorted(d._detect_yolo(), key=lambda x: x.label)
    assert [x.label for x in dets] == ["combine", "tractor"]
    assert dets[0].box == pytest.approx((0.25, 0.25, 0.35, 0.35))
    assert dets[0].score == pytest.approx(0.90)


def test_yolo_decode_int8_dequantisation():
    scale, zero = 1 / 255.0, -128
    real = yolo_tensor([(0.5, 0.5, 0.2, 0.2, 0, 0.8)], nc=1)
    q = np.clip(np.round(real / scale + zero), -128, 127).astype(np.int8)
    d = make_yolo_detector(q, ["hay_bale"], quant=(scale, zero))
    dets = d._detect_yolo()
    assert len(dets) == 1 and dets[0].label == "hay_bale"
    assert dets[0].score == pytest.approx(0.8, abs=0.01)
    assert dets[0].box == pytest.approx((0.4, 0.4, 0.6, 0.6), abs=0.01)
