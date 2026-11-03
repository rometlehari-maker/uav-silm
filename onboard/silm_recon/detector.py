"""Object detector.

Runs a single-shot detector (SSD-MobileNet style) with TensorFlow Lite. If a
Coral USB Edge TPU is plugged in and a ``*_edgetpu.tflite`` model is given, it
is used automatically (30+ FPS on a Pi); otherwise the CPU interpreter runs the
plain quantised model at ~1-3 FPS on a Pi 3.

The detector is class-agnostic: it reads a labels file and the main loop keeps
only the classes listed as ``targets`` in config. Out of the box the bundled
COCO model already recognises ``car`` and ``truck``. ``harvester`` / ``combine``
are not COCO classes, so they need a small custom-trained model dropped in via
config (see tools/train_notes.md). Fire/smoke is handled separately in fire.py.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import numpy as np


@dataclass
class Detection:
    label: str
    score: float
    # Bounding box in normalised image coords [0,1]: (xmin, ymin, xmax, ymax)
    box: tuple[float, float, float, float]

    @property
    def cx(self) -> float:
        return 0.5 * (self.box[0] + self.box[2])

    @property
    def cy(self) -> float:
        return 0.5 * (self.box[1] + self.box[3])


def _load_interpreter(model_path: str):
    """Load a TFLite interpreter, using the Edge TPU delegate if available."""
    use_tpu = model_path.endswith("_edgetpu.tflite")

    # Prefer the lightweight tflite_runtime (what you install on a Pi).
    try:
        from tflite_runtime.interpreter import Interpreter, load_delegate
    except Exception:
        from tensorflow.lite.python.interpreter import Interpreter  # type: ignore
        try:
            from tensorflow.lite.python.interpreter import load_delegate  # type: ignore
        except Exception:
            load_delegate = None  # type: ignore

    if use_tpu:
        if load_delegate is None:
            raise RuntimeError("Edge TPU model given but no delegate loader available")
        delegate_lib = {
            "posix": "libedgetpu.so.1",
        }.get(os.name, "libedgetpu.so.1")
        interpreter = Interpreter(
            model_path=model_path,
            experimental_delegates=[load_delegate(delegate_lib)],
        )
    else:
        interpreter = Interpreter(model_path=model_path)

    interpreter.allocate_tensors()
    return interpreter


def load_labels(path: str) -> dict[int, str]:
    labels: dict[int, str] = {}
    with open(path, "r", encoding="utf-8") as fh:
        for i, line in enumerate(fh):
            line = line.strip()
            if not line:
                continue
            # Support "12 car" or just "car".
            parts = line.split(maxsplit=1)
            if len(parts) == 2 and parts[0].isdigit():
                labels[int(parts[0])] = parts[1]
            else:
                labels[i] = line
    return labels


class ObjectDetector:
    def __init__(self, model_path: str, labels_path: str, score_threshold: float = 0.5):
        self.interpreter = _load_interpreter(model_path)
        self.labels = load_labels(labels_path)
        self.score_threshold = score_threshold

        inp = self.interpreter.get_input_details()[0]
        self._in_index = inp["index"]
        self._in_h, self._in_w = inp["shape"][1], inp["shape"][2]
        self._in_dtype = inp["dtype"]
        # Quantised models carry a (scale, zero_point) for input.
        self._in_quant = inp.get("quantization", (0.0, 0))

        self._out = self.interpreter.get_output_details()

    @property
    def input_size(self) -> tuple[int, int]:
        return self._in_w, self._in_h

    def _preprocess(self, frame_bgr: np.ndarray) -> np.ndarray:
        import cv2

        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        resized = cv2.resize(rgb, (self._in_w, self._in_h))
        if self._in_dtype == np.uint8:
            tensor = resized.astype(np.uint8)
        else:
            tensor = (resized.astype(np.float32) - 127.5) / 127.5
        return tensor[np.newaxis, ...]

    def detect(self, frame_bgr: np.ndarray) -> list[Detection]:
        tensor = self._preprocess(frame_bgr)
        self.interpreter.set_tensor(self._in_index, tensor)
        self.interpreter.invoke()

        boxes, classes, scores, count = self._read_outputs()
        results: list[Detection] = []
        n = int(count) if count is not None else len(scores)
        for i in range(min(n, len(scores))):
            score = float(scores[i])
            if score < self.score_threshold:
                continue
            cls = int(classes[i])
            label = self.labels.get(cls, str(cls))
            ymin, xmin, ymax, xmax = (float(v) for v in boxes[i])
            box = (
                max(0.0, xmin), max(0.0, ymin),
                min(1.0, xmax), min(1.0, ymax),
            )
            results.append(Detection(label=label, score=score, box=box))
        return results

    def _read_outputs(self):
        """Handle the common SSD output orderings robustly.

        The standard TF Lite detection postprocess produces four tensors:
        locations (boxes), classes, scores, num_detections. Their index order
        varies between exports, so we identify them by shape/rank.
        """
        tensors = {d["index"]: self.interpreter.get_tensor(d["index"]) for d in self._out}
        boxes = classes = scores = count = None
        for arr in tensors.values():
            a = np.squeeze(arr, axis=0) if arr.ndim >= 1 and arr.shape[0] == 1 else arr
            if a.ndim == 2 and a.shape[-1] == 4:
                boxes = a
            elif a.ndim == 1:
                if count is None and a.size == 1:
                    count = a[0]
                elif scores is None:
                    scores = a
                else:
                    classes = a
        # Fallback to declared order if detection failed.
        if boxes is None or scores is None or classes is None:
            b = np.squeeze(self.interpreter.get_tensor(self._out[0]["index"]))
            c = np.squeeze(self.interpreter.get_tensor(self._out[1]["index"]))
            s = np.squeeze(self.interpreter.get_tensor(self._out[2]["index"]))
            boxes, classes, scores = b, c, s
            count = len(s)
        return boxes, classes, scores, count
