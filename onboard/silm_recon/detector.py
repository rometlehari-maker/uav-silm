"""Object detector.

Runs a single-shot detector (SSD-MobileNet style) with TensorFlow Lite. If a
Coral USB Edge TPU is plugged in and a ``*_edgetpu.tflite`` model is given, it
is used automatically (30+ FPS on a Pi); otherwise the CPU interpreter runs the
plain quantised model on the Pi 4 CPU (tools/preflight.py measures the speed).

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

    # Interpreter backend, in order of preference:
    #   1. tflite_runtime      - classic lightweight Pi package
    #   2. ai_edge_litert      - its current/renamed successor (pip install
    #                            ai-edge-litert); what newer systems ship
    #   3. tensorflow.lite     - full TF fallback (heavy)
    Interpreter = None
    load_delegate = None
    for mod in ("tflite_runtime.interpreter", "ai_edge_litert.interpreter"):
        try:
            m = __import__(mod, fromlist=["Interpreter", "load_delegate"])
            Interpreter = m.Interpreter
            load_delegate = getattr(m, "load_delegate", None)
            break
        except Exception:
            continue
    if Interpreter is None:
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
    def __init__(self, model_path: str, labels_path: str, score_threshold: float = 0.5,
                 fmt: str = "auto", iou_threshold: float = 0.45,
                 class_thresholds: dict | None = None):
        self.interpreter = _load_interpreter(model_path)
        self.labels = load_labels(labels_path)
        self.score_threshold = score_threshold
        self.iou_threshold = iou_threshold
        # Optional per-class confidence thresholds, e.g. {"fire": 0.3,
        # "person": 0.6}; classes not listed use score_threshold.
        self.class_thresholds = {str(k): float(v) for k, v in (class_thresholds or {}).items()}
        unknown = set(self.class_thresholds) - set(self.labels.values())
        if unknown:
            print(f"[detector] WARNING: class_thresholds for unknown classes {sorted(unknown)}")

        inp = self.interpreter.get_input_details()[0]
        self._in_index = inp["index"]
        shape = list(inp["shape"])
        # Support both NHWC [1,H,W,3] (classic TFLite) and NCHW [1,3,H,W]
        # (produced by some torch->LiteRT exports).
        if len(shape) == 4 and shape[1] == 3 and shape[3] != 3:
            self._nchw = True
            self._in_h, self._in_w = int(shape[2]), int(shape[3])
        else:
            self._nchw = False
            self._in_h, self._in_w = int(shape[1]), int(shape[2])
        self._in_dtype = inp["dtype"]
        # Quantised models carry a (scale, zero_point) for input.
        self._in_quant = inp.get("quantization", (0.0, 0))

        self._out = self.interpreter.get_output_details()

        # Decide the output format. SSD-MobileNet produces 4 output tensors;
        # YOLO (Ultralytics export) produces a single [1, 4+nc, N] tensor.
        if fmt == "auto":
            fmt = "yolo" if len(self._out) == 1 else "ssd"
        self.fmt = fmt

        # A YOLO head is [1, 4+nc, N] or [1, N, 4+nc]: check the labels file has
        # exactly nc names, so a model/labels mix-up is caught at start-up
        # instead of silently mislabelling detections.
        self.num_classes = None
        if self.fmt == "yolo":
            shp = list(self._out[0]["shape"])
            if len(shp) == 3:
                self.num_classes = int(min(shp[1], shp[2])) - 4
                if self.num_classes != len(self.labels):
                    print(f"[detector] WARNING: model has {self.num_classes} classes but "
                          f"{labels_path} has {len(self.labels)} names - wrong labels file?")

    @property
    def input_size(self) -> tuple[int, int]:
        return self._in_w, self._in_h

    def _preprocess(self, frame_bgr: np.ndarray) -> np.ndarray:
        import cv2

        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        if getattr(self, "fmt", "yolo") == "yolo":
            # YOLO is trained on LETTERBOXED images: scaled to fit with the
            # aspect ratio kept, padded with grey (114). Squashing a 16:9 frame
            # into a square instead would make every object look ~1.8x taller
            # than anything the model saw in training.
            h0, w0 = rgb.shape[:2]
            r = min(self._in_w / w0, self._in_h / h0)
            nw, nh = max(1, round(w0 * r)), max(1, round(h0 * r))
            px, py = (self._in_w - nw) // 2, (self._in_h - nh) // 2
            resized = np.full((self._in_h, self._in_w, 3), 114, np.uint8)
            resized[py:py + nh, px:px + nw] = cv2.resize(rgb, (nw, nh), interpolation=cv2.INTER_LINEAR)
            self._lb = (r, px, py, w0, h0)
        else:
            # SSD-MobileNet models are trained on plainly resized images.
            resized = cv2.resize(rgb, (self._in_w, self._in_h))
            self._lb = None
        scale, zero = self._in_quant if self._in_quant else (0.0, 0)
        if self._in_dtype in (np.int8, np.uint8):
            # Full-integer model. YOLO's real input range is 0..1, so quantise
            # the 0..1 pixels with the model's own (scale, zero_point).
            real = resized.astype(np.float32) / 255.0
            if scale and scale != 0.0:
                q = np.round(real / scale + zero)
            else:  # no quant params: assume standard int8 mapping
                q = np.round(real * 255.0) - (128 if self._in_dtype == np.int8 else 0)
            info = np.iinfo(self._in_dtype)
            tensor = np.clip(q, info.min, info.max).astype(self._in_dtype)
        elif getattr(self, "fmt", "yolo") == "ssd":
            tensor = (resized.astype(np.float32) - 127.5) / 127.5   # float SSD: -1..1
        else:
            tensor = resized.astype(np.float32) / 255.0  # YOLO expects 0..1
        tensor = tensor[np.newaxis, ...]            # -> NHWC
        if self._nchw:
            tensor = np.transpose(tensor, (0, 3, 1, 2))  # -> NCHW
        return tensor

    def detect(self, frame_bgr: np.ndarray) -> list[Detection]:
        tensor = self._preprocess(frame_bgr)
        self.interpreter.set_tensor(self._in_index, tensor)
        self.interpreter.invoke()
        if self.fmt == "yolo":
            return self._detect_yolo()
        return self._detect_ssd()

    def detect_tiled(self, frame_bgr: np.ndarray, grid=(2, 2),
                     overlap: float = 0.15) -> list[Detection]:
        """Run detection on overlapping tiles and merge, then NMS globally.

        Aerial targets are small; a 256-px model on a 1280-px frame downsamples
        a combine to a few pixels. Splitting the frame into tiles keeps targets
        large relative to the model input and sharply improves small-object
        recall -- at the cost of running inference grid[0]*grid[1] times, so use
        it only with a Coral or a strong companion (document: FLIGHT_READY D/C).
        """
        h, w = frame_bgr.shape[:2]
        gx, gy = grid
        tw, th = int(w / gx), int(h / gy)
        ox, oy = int(tw * overlap), int(th * overlap)
        all_dets: list[Detection] = []
        for iy in range(gy):
            for ix in range(gx):
                x0 = max(0, ix * tw - ox); y0 = max(0, iy * th - oy)
                x1 = min(w, (ix + 1) * tw + ox); y1 = min(h, (iy + 1) * th + oy)
                tile = frame_bgr[y0:y1, x0:x1]
                if tile.size == 0:
                    continue
                self.interpreter.set_tensor(self._in_index, self._preprocess(tile))
                self.interpreter.invoke()
                dets = self._detect_yolo() if self.fmt == "yolo" else self._detect_ssd()
                # Map tile-normalised boxes back to full-frame-normalised.
                for d in dets:
                    bx0 = (x0 + d.box[0] * (x1 - x0)) / w
                    by0 = (y0 + d.box[1] * (y1 - y0)) / h
                    bx1 = (x0 + d.box[2] * (x1 - x0)) / w
                    by1 = (y0 + d.box[3] * (y1 - y0)) / h
                    all_dets.append(Detection(d.label, d.score, (bx0, by0, bx1, by1)))
        return self._merge_global(all_dets)

    def detect_roi(self, frame_bgr: np.ndarray, roi=None, grid=(1, 1)) -> list[Detection]:
        """Detect inside a region of interest (normalised x0, y0, x1, y1),
        optionally tiled, and return boxes in FULL-frame normalised coords.

        For a downward fisheye, the centre of the image has the best ground
        resolution and the edges show distant, squashed ground; running the
        model only on a central crop (and tiling it) gives it far more pixels
        per object for the same computing."""
        h, w = frame_bgr.shape[:2]
        if roi is None:
            x0, y0, x1, y1 = 0, 0, w, h
        else:
            x0, y0 = int(roi[0] * w), int(roi[1] * h)
            x1, y1 = int(roi[2] * w), int(roi[3] * h)
        crop = frame_bgr[y0:y1, x0:x1]
        if crop.size == 0:
            return []
        dets = (self.detect_tiled(crop, grid=grid) if tuple(grid) != (1, 1)
                else self.detect(crop))
        cw, ch = x1 - x0, y1 - y0
        return [Detection(d.label, d.score,
                          ((x0 + d.box[0] * cw) / w, (y0 + d.box[1] * ch) / h,
                           (x0 + d.box[2] * cw) / w, (y0 + d.box[3] * ch) / h))
                for d in dets]

    def _merge_global(self, dets: list[Detection]) -> list[Detection]:
        if not dets:
            return []
        out: list[Detection] = []
        by_label: dict[str, list[Detection]] = {}
        for d in dets:
            by_label.setdefault(d.label, []).append(d)
        for label, group in by_label.items():
            x0 = np.array([g.box[0] for g in group]); y0 = np.array([g.box[1] for g in group])
            x1 = np.array([g.box[2] for g in group]); y1 = np.array([g.box[3] for g in group])
            sc = np.array([g.score for g in group])
            for i in self._nms(x0, y0, x1, y1, sc, self.iou_threshold):
                out.append(group[i])
        return out

    def _detect_ssd(self) -> list[Detection]:
        boxes, classes, scores, count = self._read_outputs()
        results: list[Detection] = []
        n = int(count) if count is not None else len(scores)
        for i in range(min(n, len(scores))):
            score = float(scores[i])
            if score < self._min_threshold():
                continue
            cls = int(classes[i])
            label = self.labels.get(cls, str(cls))
            if score < self._threshold(label):
                continue
            ymin, xmin, ymax, xmax = (float(v) for v in boxes[i])
            box = (
                max(0.0, xmin), max(0.0, ymin),
                min(1.0, xmax), min(1.0, ymax),
            )
            results.append(Detection(label=label, score=score, box=box))
        return results

    def _detect_yolo(self) -> list[Detection]:
        """Decode an Ultralytics YOLO TFLite head: one tensor [1, 4+nc, N]
        (or [1, N, 4+nc]) of xywh (input-normalised) + per-class scores."""
        det = self._out[0]
        raw = self.interpreter.get_tensor(det["index"])
        scale, zero = det.get("quantization", (0.0, 0))
        arr = raw.astype(np.float32)
        if scale and scale != 0.0:  # int8/uint8 -> real values
            arr = (arr - zero) * scale
        arr = np.squeeze(arr, axis=0)  # (4+nc, N) or (N, 4+nc)
        if arr.shape[0] < arr.shape[1]:
            arr = arr.T  # -> (N, 4+nc)

        nc = arr.shape[1] - 4
        xywh = arr[:, :4]
        cls_scores = arr[:, 4:4 + nc]
        cls_ids = np.argmax(cls_scores, axis=1)
        confs = cls_scores[np.arange(cls_scores.shape[0]), cls_ids]

        keep = confs >= self._min_threshold()
        xywh, cls_ids, confs = xywh[keep], cls_ids[keep], confs[keep]
        if len(confs) == 0:
            return []

        # xywh (cx,cy,w,h, normalised 0..1) -> xyxy normalised
        cx, cy, w, h = xywh[:, 0], xywh[:, 1], xywh[:, 2], xywh[:, 3]
        x0, y0, x1, y1 = cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2
        lb = getattr(self, "_lb", None)
        if lb is not None:
            # undo the letterbox: model-input coords -> original image coords
            r, px, py, w0, h0 = lb
            x0 = (x0 * self._in_w - px) / r / w0
            x1 = (x1 * self._in_w - px) / r / w0
            y0 = (y0 * self._in_h - py) / r / h0
            y1 = (y1 * self._in_h - py) / r / h0
        x0, y0 = np.clip(x0, 0, 1), np.clip(y0, 0, 1)
        x1, y1 = np.clip(x1, 0, 1), np.clip(y1, 0, 1)

        # Class-aware NMS.
        results: list[Detection] = []
        for c in np.unique(cls_ids):
            m = cls_ids == c
            idxs = self._nms(x0[m], y0[m], x1[m], y1[m], confs[m], self.iou_threshold)
            bx0, by0, bx1, by1 = x0[m], y0[m], x1[m], y1[m]
            sc = confs[m]
            label = self.labels.get(int(c), str(int(c)))
            thr = self._threshold(label)
            for i in idxs:
                if sc[i] < thr:
                    continue
                results.append(Detection(
                    label=label, score=float(sc[i]),
                    box=(float(bx0[i]), float(by0[i]), float(bx1[i]), float(by1[i])),
                ))
        return results

    def _threshold(self, label: str) -> float:
        return getattr(self, "class_thresholds", {}).get(label, self.score_threshold)

    def _min_threshold(self) -> float:
        ct = getattr(self, "class_thresholds", {})
        return min([self.score_threshold] + list(ct.values()))

    @staticmethod
    def _nms(x0, y0, x1, y1, scores, iou_thr) -> list[int]:
        areas = (x1 - x0) * (y1 - y0)
        order = scores.argsort()[::-1]
        keep = []
        while order.size > 0:
            i = order[0]
            keep.append(int(i))
            if order.size == 1:
                break
            xx0 = np.maximum(x0[i], x0[order[1:]])
            yy0 = np.maximum(y0[i], y0[order[1:]])
            xx1 = np.minimum(x1[i], x1[order[1:]])
            yy1 = np.minimum(y1[i], y1[order[1:]])
            w = np.maximum(0.0, xx1 - xx0)
            h = np.maximum(0.0, yy1 - yy0)
            inter = w * h
            iou = inter / (areas[i] + areas[order[1:]] - inter + 1e-9)
            order = order[1:][iou <= iou_thr]
        return keep

    def _read_outputs(self):
        """Handle the common SSD output orderings robustly.

        The standard TF Lite detection postprocess produces four tensors:
        locations (boxes), classes, scores, num_detections. Their index order
        varies between exports, so we identify them by shape/rank.
        """
        def squeeze(arr):
            return np.squeeze(arr, axis=0) if arr.ndim >= 1 and arr.shape[0] == 1 else arr

        named = {}
        for d in self._out:
            name = str(d.get("name", ""))
            if name.startswith("TFLite_Detection_PostProcess"):
                suffix = name.rsplit(":", 1)[1] if ":" in name else "0"
                named[suffix] = self._dequant(d, self.interpreter.get_tensor(d["index"]))
        if set(named) >= {"0", "1", "2"}:
            # The standard post-process names its outputs: 0 boxes, 1 classes,
            # 2 scores, 3 count. Trust the names over the tensor order.
            boxes, classes, scores = (squeeze(named[k]) for k in ("0", "1", "2"))
            count = float(np.squeeze(named["3"])) if "3" in named else len(scores)
            return boxes, classes, scores, count

        boxes, count, vecs = None, None, []
        for d in self._out:
            a = squeeze(self._dequant(d, self.interpreter.get_tensor(d["index"])))
            if a.ndim == 2 and a.shape[-1] == 4:
                boxes = a
            elif a.ndim == 1 and a.size == 1 and count is None:
                count = float(a[0])
            elif a.ndim == 1:
                vecs.append(a)
        classes = scores = None
        if len(vecs) == 2:
            # Unnamed outputs: the class ids are whole numbers and can exceed 1;
            # the scores lie in 0..1. Decide by the values, not by the order.
            def looks_like_ids(v):
                return bool(np.all(np.abs(v - np.round(v)) < 1e-4)) and float(np.max(v, initial=0)) > 1.0
            a, b = vecs
            if looks_like_ids(a) and not looks_like_ids(b):
                classes, scores = a, b
            elif looks_like_ids(b) and not looks_like_ids(a):
                classes, scores = b, a
            else:                       # ambiguous (e.g. all class 0/1): standard order
                classes, scores = a, b
        if boxes is None or scores is None or classes is None:
            # Last resort: the standard declared order boxes, classes, scores.
            b = np.squeeze(self.interpreter.get_tensor(self._out[0]["index"]))
            c = np.squeeze(self.interpreter.get_tensor(self._out[1]["index"]))
            s = np.squeeze(self.interpreter.get_tensor(self._out[2]["index"]))
            boxes, classes, scores = b, c, s
            count = len(s)
        return boxes, classes, scores, count

    @staticmethod
    def _dequant(detail, arr):
        """Real values from a quantised output tensor (no-op for float)."""
        q = detail.get("quantization", (0.0, 0)) or (0.0, 0)
        scale, zero = q
        if scale and np.issubdtype(arr.dtype, np.integer):
            return (arr.astype(np.float32) - zero) * scale
        return arr
