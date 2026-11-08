"""The detector with the REAL bundled model (COCO SSD MobileNet v1, quantised).

Fakes alone missed a decoding bug: the model's class-id and score outputs were
read in the wrong order, so every detection came out as "person" with scores
like 86.0. These tests run the actual .tflite through the onboard code.
They are skipped if the model or a TFLite runtime is not installed
(pi/install.sh and the CI workflow download both)."""

import os

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from silm_recon.detector import ObjectDetector, load_labels  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ONBOARD = os.path.join(HERE, "..")
MODEL = os.path.join(ONBOARD, "models", "coco_ssd_mobilenet_v1_quant.tflite")
LABELS = os.path.join(ONBOARD, "models", "coco_labels.txt")
AIRCRAFT_PHOTO = os.path.join(ONBOARD, "..", "images", "assembled.jpg")


def _runtime():
    for mod in ("tflite_runtime.interpreter", "ai_edge_litert.interpreter",
                "tensorflow.lite.python.interpreter"):
        try:
            __import__(mod)
            return True
        except Exception:
            continue
    return False


needs_model = pytest.mark.skipif(not (os.path.isfile(MODEL) and _runtime()),
                                 reason="real model / TFLite runtime not installed")


def test_label_file_matches_the_models_class_ids():
    labels = load_labels(LABELS)
    assert len(labels) == 90                     # COCO ids 0..89, with gaps marked ???
    assert labels[0] == "person" and labels[2] == "car" and labels[7] == "truck"
    assert labels[11] == "???" and labels[12] == "stop sign"   # the first gap
    assert labels[89] == "toothbrush"


@needs_model
def test_real_model_recognises_the_aircraft_photo():
    d = ObjectDetector(MODEL, LABELS, 0.4)
    assert d.fmt == "ssd" and d.input_size == (300, 300)
    # the configured detection mode: central half of the frame, 2x2 tiles
    dets = d.detect_roi(cv2.imread(AIRCRAFT_PHOTO), (0.25, 0.25, 0.75, 0.75), (2, 2))
    assert dets, "nothing detected on the aircraft photo"
    assert all(0.0 <= x.score <= 1.0 for x in dets)
    assert all(0.0 <= v <= 1.0 for x in dets for v in x.box)
    best = max(dets, key=lambda x: x.score)
    assert best.label == "airplane" and best.score > 0.5


@needs_model
def test_real_model_finds_nothing_in_a_blank_frame():
    d = ObjectDetector(MODEL, LABELS, 0.45)
    assert d.detect(np.full((1080, 1920, 3), 90, np.uint8)) == []


@needs_model
def test_real_model_tiled_roi_on_a_full_hd_frame():
    d = ObjectDetector(MODEL, LABELS, 0.4)
    img = cv2.imread(AIRCRAFT_PHOTO)
    frame = cv2.resize(img, (1920, 1080))
    dets = d.detect_roi(frame, (0.0, 0.0, 1.0, 1.0), (2, 2))
    assert all(x.label != "???" for x in dets)
    assert all(0.0 <= x.score <= 1.0 for x in dets)


# ---- the decoding logic itself, with outputs in an unusual order -----------

class Interp:
    def __init__(self, tensors):
        self.t = tensors

    def get_tensor(self, i):
        return self.t[i]


def fake_detector(outputs, labels=("person", "bicycle", "car", "motorcycle")):
    d = object.__new__(ObjectDetector)
    d.interpreter = Interp({i: o["arr"] for i, o in enumerate(outputs)})
    d._out = [{"index": i, "name": o.get("name", ""), "quantization": o.get("q", (0.0, 0))}
              for i, o in enumerate(outputs)]
    d.labels = dict(enumerate(labels))
    d.score_threshold, d.iou_threshold, d.fmt, d.class_thresholds = 0.4, 0.45, "ssd", {}
    return d


BOXES = np.array([[[0.1, 0.1, 0.3, 0.3], [0.5, 0.5, 0.9, 0.9]]], np.float32)
CLASSES = np.array([[2.0, 3.0]], np.float32)
SCORES = np.array([[0.9, 0.6]], np.float32)
COUNT = np.array([2.0], np.float32)


def test_named_outputs_win_over_their_order():
    d = fake_detector([
        {"arr": SCORES, "name": "TFLite_Detection_PostProcess:2"},
        {"arr": COUNT, "name": "TFLite_Detection_PostProcess:3"},
        {"arr": BOXES, "name": "TFLite_Detection_PostProcess"},
        {"arr": CLASSES, "name": "TFLite_Detection_PostProcess:1"},
    ])
    out = d._detect_ssd()
    assert [(x.label, round(x.score, 2)) for x in out] == [("car", 0.9), ("motorcycle", 0.6)]


@pytest.mark.parametrize("order", [(0, 1, 2, 3), (0, 2, 1, 3), (3, 2, 1, 0)])
def test_unnamed_outputs_are_told_apart_by_their_values(order):
    arrs = [BOXES, CLASSES, SCORES, COUNT]
    d = fake_detector([{"arr": arrs[i]} for i in order])
    out = d._detect_ssd()
    assert [(x.label, round(x.score, 2)) for x in out] == [("car", 0.9), ("motorcycle", 0.6)]


def test_quantised_scores_are_dequantised():
    q = np.array([[230, 153]], np.uint8)            # 230/255 = 0.90, 153/255 = 0.60
    d = fake_detector([
        {"arr": BOXES, "name": "TFLite_Detection_PostProcess"},
        {"arr": CLASSES, "name": "TFLite_Detection_PostProcess:1"},
        {"arr": q, "name": "TFLite_Detection_PostProcess:2", "q": (1 / 255.0, 0)},
        {"arr": COUNT, "name": "TFLite_Detection_PostProcess:3"},
    ])
    assert [round(x.score, 2) for x in d._detect_ssd()] == [0.9, 0.6]
