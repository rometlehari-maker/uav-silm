import json
import os
import subprocess
import sys

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

TOOLS = os.path.join(os.path.dirname(__file__), "..", "tools")
sys.path.insert(0, TOOLS)
import make_review  # noqa: E402

from silm_recon.georef import GroundPoint  # noqa: E402
from silm_recon.store import DetectionStore  # noqa: E402
from types import SimpleNamespace  # noqa: E402


def make_mission(tmp_path):
    s = DetectionStore({"output_dir": str(tmp_path)})
    frame = np.full((360, 640, 3), 80, np.uint8)
    st = SimpleNamespace(lat=59.40, lon=24.70, rel_alt=100.0, alt_amsl=130.0, roll=0, pitch=0,
                         yaw=0, ground_speed=19, mode="AUTO", armed=True)
    for i, (lab, glat) in enumerate([("combine", 59.401), ("fire", 59.402), ("combine", 59.405)]):
        s.save(frame, [{"label": lab, "score": 0.7 + 0.1 * i, "box": (0.4, 0.4, 0.5, 0.5),
                        "ground": GroundPoint(glat, 24.701, 100, True)}], st, None)
    # one detection without a ground fix -> falls back to aircraft position
    s.save(frame, [{"label": "smoke", "score": 0.5, "box": (0.1, 0.1, 0.2, 0.2), "ground": None}], st, None)
    s.close()
    with open(os.path.join(s.dir, "track.ndjson"), "w") as fh:
        for k in range(30):
            fh.write(json.dumps({"t": 1000 + k, "lat": 59.40 + k * 1e-4, "lon": 24.70,
                                 "alt": 100, "mode": "AUTO", "gps": "LOST" if 10 < k < 20 else "OK"}) + "\n")
        fh.write('{"t": 1031, "lat": 59.4')  # truncated last line (power cut)
    with open(os.path.join(s.dir, "gps_health.ndjson"), "w") as fh:
        fh.write(json.dumps({"t": "2026-11-04T10:00:10Z", "event": "gps OK->LOST",
                             "satellites": 0, "hdop": 99.9}) + "\n")
    with open(os.path.join(s.dir, "health.ndjson"), "w") as fh:
        fh.write(json.dumps({"t": "2026-11-04T10:00:20Z", "warnings": ["CPU 74C"]}) + "\n")
        fh.write(json.dumps({"t": "2026-11-04T10:00:30Z", "warnings": []}) + "\n")
    return s.dir


def test_collect_reads_everything_and_survives_truncated_lines(tmp_path):
    d = make_mission(tmp_path)
    dets, track, gps, health = make_review.collect(d)
    assert [x["label"] for x in dets] == ["combine", "fire", "combine", "smoke"]
    assert dets[3]["pos_src"] == "aircraft" and dets[0]["pos_src"] == "ground"
    assert len(track) == 30 and len(gps) == 1 and len(health) == 1


def test_build_writes_page_with_embedded_data(tmp_path):
    d = make_mission(tmp_path)
    subprocess.run([sys.executable, os.path.join(TOOLS, "make_review.py"), d], check=True,
                   capture_output=True)
    page = open(os.path.join(d, "review.html"), encoding="utf-8").read()
    assert "<title>Flight review" in page and "leaflet@1.9.4" in page
    data = json.loads(page.split("const DATA = ", 1)[1].split(";</script>", 1)[0].replace("<\\/", "</"))
    assert len(data["dets"]) == 4 and set(data["colors"]) == {"combine", "fire", "smoke"}
    assert 'data-label="combine"' in page and "<b>2</b>" in page
    assert "gps OK-&gt;LOST" in page and "CPU 74C" in page


def test_html_is_escaped(tmp_path):
    d = make_mission(tmp_path)
    with open(os.path.join(d, "gps_health.ndjson"), "a") as fh:
        fh.write(json.dumps({"t": "x", "event": "<script>alert(1)</script>"}) + "\n")
    page = make_review.build(d)
    assert "<script>alert(1)</script>" not in page
