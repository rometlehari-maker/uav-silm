import json
import math
import os
import subprocess
import sys
import time
from types import SimpleNamespace

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from silm_recon.fire import FireDetector  # noqa: E402
from silm_recon.georef import GroundPoint  # noqa: E402
from silm_recon.store import DetectionStore  # noqa: E402

TOOLS = os.path.join(os.path.dirname(__file__), "..", "tools")


def state():
    return SimpleNamespace(lat=58.378, lon=24.5, rel_alt=100.0, alt_amsl=140.0, roll=0.0,
                           pitch=0.0, yaw=0.0, ground_speed=18.0, mode="AUTO", armed=True)


def test_dedup_rules(tmp_path):
    s = DetectionStore({"output_dir": str(tmp_path), "cooldown_s": 8, "min_separation_m": 40})
    assert not s.is_duplicate("car", 58.378, 24.5)
    s._recent["car"] = [(time.time(), 58.378, 24.5)]
    assert s.is_duplicate("car", 58.37801, 24.50001)       # ~1 m
    assert not s.is_duplicate("car", 58.3787, 24.5)         # ~78 m
    assert not s.is_duplicate("truck", 58.378, 24.5)        # other class
    s.close()


def test_save_writes_all_outputs_and_geojson(tmp_path):
    s = DetectionStore({"output_dir": str(tmp_path)})
    frame = np.full((360, 640, 3), 60, np.uint8)
    ev = [{"label": "combine", "score": 0.8, "box": (0.4, 0.4, 0.5, 0.5),
           "ground": GroundPoint(58.3781, 24.5002, 100.0, True)}]
    rec = s.save(frame, ev, state(), None)
    s.close()
    files = sorted(os.listdir(s.dir))
    assert any(f.endswith(".jpg") for f in files) and "detections.csv" in files
    nd = os.path.join(s.dir, "detections.ndjson")
    subprocess.run([sys.executable, os.path.join(TOOLS, "make_geojson.py"), nd], check=True,
                   capture_output=True)
    gj = json.load(open(os.path.join(s.dir, "detections.geojson")))
    f0 = gj["features"][0]
    assert f0["geometry"]["coordinates"] == pytest.approx([24.5002, 58.3781])
    assert f0["properties"]["label"] == "combine" and rec.seq == 1


def test_fire_detected_on_flame_coloured_blob():
    f = np.full((360, 640, 3), 60, np.uint8)
    f[150:210, 300:380] = (20, 120, 255)
    hits = FireDetector({"enabled": True, "detect_smoke": False}).detect(f)
    assert [h.label for h in hits] == ["fire"]
    x0, y0, x1, y1 = hits[0].box
    assert x0 == pytest.approx(300 / 640, abs=0.02) and y1 == pytest.approx(210 / 360, abs=0.02)


def test_fire_not_triggered_by_green_field():
    rng = np.random.default_rng(0)
    f = np.clip(rng.normal((60, 140, 70), 15, (360, 640, 3)), 0, 255).astype(np.uint8)
    assert FireDetector({"enabled": True}).detect(f) == []
