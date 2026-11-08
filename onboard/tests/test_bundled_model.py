"""The ready-made farm model in models/ (silm_oi: tractor, car, truck,
livestock), run through the aircraft's own detector code.

These are regression checks, not an accuracy measurement: the measured
precision/recall are in training/README.md. They catch a broken export (the
fully-INT8 export pinned every score at 0.50), a label file in the wrong
order, an input layout the detector can't feed, and a model that stopped
recognising the sample photos in tests/data/oi_samples.
"""

import json
import os
import sys

import numpy as np
import pytest
import yaml

ONBOARD = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ONBOARD)

MODEL = os.path.join(ONBOARD, "models", "silm_oi.tflite")
LABELS = os.path.join(ONBOARD, "models", "silm_oi_labels.txt")
INFO = os.path.join(ONBOARD, "models", "silm_oi.json")
SAMPLES = os.path.join(os.path.dirname(__file__), "data", "oi_samples")

pytestmark = pytest.mark.skipif(not os.path.isfile(MODEL), reason="models/silm_oi.tflite not present")


@pytest.fixture(scope="module")
def det():
    pytest.importorskip("ai_edge_litert", reason="needs the TFLite runtime")
    from silm_recon.detector import ObjectDetector
    return ObjectDetector(MODEL, LABELS, 0.25, fmt="auto")


def test_labels_follow_classes_yaml():
    want = yaml.safe_load(open(os.path.join(ONBOARD, "training", "classes.yaml")))
    names = want["names"] if isinstance(want, dict) and "names" in want else want
    if isinstance(names, dict):
        names = [names[k] for k in sorted(names)]
    got = [x.strip() for x in open(LABELS) if x.strip()]
    assert got == list(names)


def test_description_lists_what_it_finds():
    info = json.load(open(INFO))
    labels = {x.strip() for x in open(LABELS) if x.strip()}
    assert set(info["finds"]) == {"tractor", "car", "truck", "livestock"}
    assert set(info["finds"]) <= labels and info["classes"] == [x.strip() for x in open(LABELS) if x.strip()]
    assert info.get("title")


def test_loads_as_yolo_with_the_expected_input(det):
    assert det.fmt == "yolo"
    assert det.input_size == (320, 320)


def test_blank_picture_gives_no_detections(det):
    """A model whose scores are clipped at 0.50 'finds' things everywhere."""
    for v in (0, 114, 255):
        assert det.detect(np.full((720, 1280, 3), v, np.uint8)) == []


def test_tiled_detection_runs_on_a_camera_frame(det):
    rng = np.random.default_rng(1)
    frame = rng.integers(0, 255, (1080, 1920, 3), dtype=np.uint8)
    out = det.detect_roi(frame, roi=(0.25, 0.25, 0.75, 0.75), grid=(2, 2))
    assert all(0.0 <= d.box[0] <= d.box[2] <= 1.0 and 0.0 <= d.box[1] <= d.box[3] <= 1.0 for d in out)


def _samples():
    p = os.path.join(SAMPLES, "samples.json")
    return json.load(open(p))["samples"] if os.path.isfile(p) else []


@pytest.mark.parametrize("s", _samples(), ids=lambda s: s["file"])
def test_recognises_the_sample_photos(det, s):
    import cv2
    im = cv2.imread(os.path.join(SAMPLES, s["file"]))
    assert im is not None
    found = [d for d in det.detect(im) if d.label == s["expect"]]
    assert found, f"no {s['expect']} found in {s['file']}"
    assert max(d.score for d in found) >= 0.35
