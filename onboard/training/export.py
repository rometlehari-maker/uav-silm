#!/usr/bin/env python3
"""Export trained YOLO weights to the formats the aircraft runs.

Produces an INT8-quantised TFLite model for the Raspberry Pi, and optionally an
Edge-TPU model if you add a Coral stick. Also copies a labels file next to the
model so the onboard detector can name classes.

Examples:
    python3 export.py --weights runs/detect/synth_proof/weights/best.pt --imgsz 320
    python3 export.py --weights runs/detect/farm_v1/weights/best.pt --imgsz 320 --edgetpu

Outputs (in the weights' folder):
    best_saved_model/best_full_integer_quant.tflite   <- copied to --out
    labels.txt
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


def confidence_check(model, tflite_path, labels_path, data_yaml, imgsz, n=12) -> None:
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
        return
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
        return
    med = statistics.median(ratios)
    print(f"confidence check on {len(ratios)} images: top score original "
          f"{statistics.median(pt_max):.2f}, exported {statistics.median(tf_max):.2f} "
          f"(ratio {med:.2f})")
    if med < 0.85:
        print("WARNING: the exported model is much less confident than the original. "
              "Calibration data probably doesn't match the model (check --data), or INT8 "
              "quantization hurts this model. Thresholds tuned on the original won't transfer.")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--weights", required=True)
    ap.add_argument("--imgsz", type=int, default=320)
    ap.add_argument("--edgetpu", action="store_true")
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
    print(f"Exporting INT8 TFLite, calibrating on {data} ...")
    model.export(format="tflite", int8=True, imgsz=args.imgsz, data=data)

    weights_dir = os.path.dirname(os.path.abspath(args.weights))
    stem = os.path.splitext(os.path.basename(args.weights))[0]
    # Ultralytics has used two layouts: a <stem>_saved_model/ folder (older TF
    # export) and a flat <stem>_int8.tflite next to the weights (LiteRT export).
    search_dirs = [os.path.join(weights_dir, stem + "_saved_model"), weights_dir]
    src = None
    for d in search_dirs:
        if not os.path.isdir(d):
            continue
        tfl = [f for f in os.listdir(d) if f.endswith(".tflite")]
        # prefer an int8/quant file
        pref = [f for f in tfl if "int8" in f or "quant" in f]
        chosen = sorted(pref)[0] if pref else (sorted(tfl)[0] if tfl else None)
        if chosen:
            src = os.path.join(d, chosen)
            break
    if src is None:
        print("ERROR: no .tflite produced; check the export log above.")
        return 1

    os.makedirs(os.path.abspath(args.out), exist_ok=True)
    dst = os.path.join(os.path.abspath(args.out), args.model_name + ".tflite")
    shutil.copy(src, dst)
    labels = os.path.join(os.path.abspath(args.out), args.model_name + "_labels.txt")
    write_labels(labels, model.names)
    check_against_classes_yaml(model.names)
    confidence_check(model, dst, labels, data, args.imgsz)
    print(f"\nCPU model  -> {dst}")
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
