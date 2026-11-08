#!/usr/bin/env python3
"""Export trained YOLO weights to the formats the aircraft runs.

Produces a TFLite model for the Raspberry Pi, and optionally an Edge-TPU model
if you add a Coral stick. Also copies a labels file next to the model so the
onboard detector can name classes.

Precision: the network body is quantised to INT8 (about 2x faster on the Pi's
CPU) but the detection head stays in float. Fully-INT8 YOLO exports clip the
class scores (every detection pinned at ~0.50, measured Nov 2026 with
Ultralytics 8.4 + LiteRT), which breaks the score thresholds. If the quantised
model still scores clearly lower than the original, the float model is used
instead (slower, but right). `--fp32` skips quantisation.

Examples:
    python3 export.py --weights runs/detect/synth_proof/weights/best.pt --imgsz 320
    python3 export.py --weights runs/detect/farm_v1/weights/best.pt --imgsz 320 --edgetpu

Outputs (in --out, default ../models):
    <model-name>.tflite         the model the aircraft runs
    <model-name>_labels.txt     one class name per line
"""

from __future__ import annotations

import argparse
import os
import shutil

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))


def write_labels(path: str, names) -> None:
    """Write one class name per line, in the model's own class order.

    The names come from the trained weights, NOT from classes.yaml: if
    classes.yaml was edited after training (e.g. a new class added), using it
    here would silently give every detection the wrong name."""
    if isinstance(names, dict):
        names = [names[i] for i in sorted(names)]
    with open(path, "w") as fh:
        for n in names:
            fh.write(f"{n}\n")


def check_against_classes_yaml(names) -> None:
    try:
        with open(os.path.join(HERE, "classes.yaml")) as fh:
            want = yaml.safe_load(fh)["names"]
        want = [want[i] for i in sorted(want)]
    except Exception:
        return
    have = [names[i] for i in sorted(names)] if isinstance(names, dict) else list(names)
    if have != want:
        print("NOTE: this model's classes differ from training/classes.yaml")
        print(f"  model:        {have}")
        print(f"  classes.yaml: {want}")
        print("  The labels file is written from the MODEL (correct for this model).")


def find_training_data(weights: str, model) -> str | None:
    """The data.yaml the weights were trained on: from the training run's
    args.yaml (runs/detect/NAME/args.yaml), else from the checkpoint."""
    run_dir = os.path.dirname(os.path.dirname(os.path.abspath(weights)))
    cand = None
    args_yaml = os.path.join(run_dir, "args.yaml")
    if os.path.exists(args_yaml):
        cand = (yaml.safe_load(open(args_yaml)) or {}).get("data")
    if not cand:
        cand = (getattr(model, "ckpt", None) or {}).get("train_args", {}).get("data")
    return cand if cand and os.path.exists(cand) else None


def confidence_check(model, tflite_path, labels_path, data_yaml, imgsz, n=12) -> float | None:
    """Compare the exported model's confidence with the original's on a few
    images. INT8 export with poor calibration data squashes the scores (e.g.
    every detection at ~0.5), which silently breaks thresholds."""
    import statistics
    import sys
    try:
        import cv2
        sys.path.insert(0, os.path.join(HERE, ".."))
        from check_dataset import split_images
        from silm_recon.detector import ObjectDetector
        _, splits = split_images(data_yaml)
        imgs = (splits.get("val") or splits.get("train") or [])[:n]
        det = ObjectDetector(tflite_path, labels_path, 0.05, fmt="yolo")
    except Exception as exc:  # the check is advisory
        print(f"(confidence check skipped: {exc})")
        return None
    ratios, pt_max, tf_max = [], [], []
    for p in imgs:
        r = model.predict(p, imgsz=imgsz, conf=0.05, verbose=False)[0]
        a = max(r.boxes.conf.tolist(), default=0.0)
        b = max((d.score for d in det.detect(cv2.imread(p))), default=0.0)
        if a >= 0.3:
            ratios.append(b / a)
            pt_max.append(a)
            tf_max.append(b)
    if not ratios:
        print("(confidence check: no confident detections on the sample images)")
        return None
    med = statistics.median(ratios)
    print(f"confidence check on {len(ratios)} images: top score original "
          f"{statistics.median(pt_max):.2f}, exported {statistics.median(tf_max):.2f} "
          f"(ratio {med:.2f})")
    if med < 0.85:
        print("WARNING: the exported model is much less confident than the original. "
              "Calibration data probably doesn't match the model (check --data), or INT8 "
              "quantization hurts this model. Thresholds tuned on the original won't transfer.")
    return med


# Tensors of the YOLO detection head (box/class convolutions, DFL, box decoding,
# sigmoid, output concat) as litert-torch names them. Kept in float.
HEAD_REGEX = r".*(head\.Detect.*|_NormalizeCoords;\d+|serving_default_output.*)$"


def _letterbox_nchw(path: str, size: int):
    import cv2
    import numpy as np
    im = cv2.imread(path)
    if im is None:
        return None
    im = im[:, :, ::-1]
    h, w = im.shape[:2]
    r = size / max(h, w)
    im = cv2.resize(im, (max(1, round(w * r)), max(1, round(h * r))))
    out = np.full((size, size, 3), 114, np.uint8)
    y0, x0 = (size - im.shape[0]) // 2, (size - im.shape[1]) // 2
    out[y0:y0 + im.shape[0], x0:x0 + im.shape[1]] = im
    return np.ascontiguousarray(np.transpose(out.astype(np.float32) / 255.0, (2, 0, 1))[None])


def quantize_body_int8(float_tflite: str, dst: str, data_yaml: str, imgsz: int, n: int = 200) -> bool:
    """Static INT8 for the network body, float for the detection head.
    Returns False (and prints why) if it can't be done here."""
    try:
        import sys
        from ai_edge_quantizer import calibrator, qtyping, quantizer, recipe
        from ai_edge_litert.interpreter import Interpreter
        sys.path.insert(0, HERE)
        from check_dataset import split_images
    except Exception as exc:
        print(f"(INT8 quantisation not available here: {exc}; using the float model)")
        return False
    inp = Interpreter(float_tflite).get_input_details()[0]
    if list(inp["shape"][1:2]) != [3]:
        print("(INT8 quantisation: model input isn't NCHW as expected; using the float model)")
        return False
    _, splits = split_images(data_yaml)
    imgs = list(splits.get("train") or []) + list(splits.get("val") or [])
    step = max(1, len(imgs) // n)
    samples = []
    for p in imgs[::step][:n]:
        x = _letterbox_nchw(p, imgsz)
        if x is not None:
            samples.append({inp["name"].split(":")[0].replace("serving_default_", ""): x})
    if len(samples) < 20:
        print(f"(INT8 quantisation: only {len(samples)} calibration images; using the float model)")
        return False
    print(f"Quantising the network body to INT8 ({len(samples)} calibration images), head stays float ...")
    qt = quantizer.Quantizer(float_tflite)
    qt.load_quantization_recipe(recipe.static_wi8_ai8())
    for op in (qtyping.TFLOperationName.INPUT, qtyping.TFLOperationName.OUTPUT):
        qt.update_quantization_recipe(regex=".*", operation_name=op,
                                      algorithm_key=recipe.AlgorithmName.NO_QUANTIZE)
    qt.update_quantization_recipe(regex=HEAD_REGEX, operation_name=qtyping.TFLOperationName.ALL_SUPPORTED,
                                  algorithm_key=recipe.AlgorithmName.NO_QUANTIZE)
    result = qt.calibrate({"serving_default": samples},
                          mode=calibrator.CalibrationMode.CALIBRATION_PROFILER_BASED)
    qt.quantize(calibration_result=result).export_model(dst, overwrite=True)
    return os.path.exists(dst)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--weights", required=True)
    ap.add_argument("--imgsz", type=int, default=320)
    ap.add_argument("--edgetpu", action="store_true")
    ap.add_argument("--fp32", action="store_true", help="keep the whole model in float (no INT8)")
    ap.add_argument("--out", default=os.path.join(HERE, "..", "models"))
    ap.add_argument("--model-name", default="silm_farm")
    ap.add_argument("--data", default=None,
                    help="data.yaml for INT8 calibration (default: the one the weights were "
                         "trained on). Must contain images like those the model will see.")
    args = ap.parse_args()

    from ultralytics import YOLO

    model = YOLO(args.weights)
    # INT8 quantization measures the range of every layer's values on sample
    # images. If Ultralytics isn't given data it silently falls back to a few
    # COCO street photos, on which farm classes never fire, and the class
    # scores get clipped (we saw every detection pinned at 0.50). So always
    # calibrate on the model's own training data.
    data = args.data or find_training_data(args.weights, model)
    if not data or not os.path.exists(data):
        print("ERROR: can't find the training data.yaml for INT8 calibration. "
              "Pass --data path/to/data.yaml (the dataset the model was trained on).")
        return 2
    weights_dir = os.path.dirname(os.path.abspath(args.weights))
    stem = os.path.splitext(os.path.basename(args.weights))[0]
    # Ultralytics has used two layouts: a <stem>_saved_model/ folder (older TF
    # export) and flat <stem>.tflite / <stem>_int8.tflite files next to the
    # weights (LiteRT export).
    search_dirs = [os.path.join(weights_dir, stem + "_saved_model"), weights_dir]

    def find_tflite(want_int8: bool):
        for d in search_dirs:
            if not os.path.isdir(d):
                continue
            tfl = sorted(f for f in os.listdir(d) if f.endswith(".tflite") and "edgetpu" not in f
                         and "_body_int8" not in f)
            q = [f for f in tfl if "int8" in f or "quant" in f]
            pick = q if want_int8 else [f for f in tfl if f not in q]
            if pick:
                return os.path.join(d, pick[0])
        return None

    os.makedirs(os.path.abspath(args.out), exist_ok=True)
    dst = os.path.join(os.path.abspath(args.out), args.model_name + ".tflite")
    labels = os.path.join(os.path.abspath(args.out), args.model_name + "_labels.txt")
    write_labels(labels, model.names)
    check_against_classes_yaml(model.names)

    print("Exporting float TFLite ...")
    model.export(format="tflite", imgsz=args.imgsz)
    fsrc = find_tflite(False)
    chosen = None
    if fsrc and not args.fp32:
        qpath = os.path.join(weights_dir, stem + "_body_int8.tflite")
        if quantize_body_int8(fsrc, qpath, data, args.imgsz):
            shutil.copy(qpath, dst)
            ratio = confidence_check(model, dst, labels, data, args.imgsz)
            if ratio is None or ratio >= 0.85:
                chosen = "INT8 body + float head"
            else:
                print("-> the quantised model loses confidence; using the float model instead.")
    if chosen is None and fsrc:
        shutil.copy(fsrc, dst)
        confidence_check(model, dst, labels, data, args.imgsz)
        chosen = "float32"
    if chosen is None:
        # Older Ultralytics (TensorFlow route) without a float file: its own INT8.
        print(f"Exporting INT8 TFLite, calibrating on {data} ...")
        model.export(format="tflite", int8=True, imgsz=args.imgsz, data=data)
        src = find_tflite(True)
        if src is None:
            print("ERROR: no .tflite produced; check the export log above.")
            return 1
        shutil.copy(src, dst)
        confidence_check(model, dst, labels, data, args.imgsz)
        chosen = "INT8 (Ultralytics)"
    print(f"\nCPU model  -> {dst}   ({chosen}, {os.path.getsize(dst) / 1e6:.1f} MB)")
    print(f"labels     -> {labels}")

    if args.edgetpu:
        print("\nExporting Edge-TPU model ...")
        try:
            model.export(format="edgetpu", imgsz=args.imgsz, data=data)
            etpu = []
            for d in search_dirs:
                if os.path.isdir(d):
                    etpu += [os.path.join(d, f) for f in os.listdir(d)
                             if f.endswith("edgetpu.tflite")]
            if etpu:
                edst = os.path.join(os.path.abspath(args.out), args.model_name + "_edgetpu.tflite")
                shutil.copy(sorted(etpu)[0], edst)
                print(f"Edge-TPU model -> {edst}")
        except Exception as exc:
            print(f"Edge-TPU export skipped ({exc}). Needs the edgetpu compiler.")

    print("\nSet in config.yaml:")
    print("  detector.format: yolo")
    print(f"  detector.model:  models/{args.model_name}.tflite")
    print(f"  detector.labels: models/{args.model_name}_labels.txt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
