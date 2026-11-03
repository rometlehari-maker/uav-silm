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


def write_labels(path: str) -> None:
    with open(os.path.join(HERE, "classes.yaml")) as fh:
        names = yaml.safe_load(fh)["names"]
    with open(path, "w") as fh:
        for i in range(len(names)):
            fh.write(f"{names[i]}\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--weights", required=True)
    ap.add_argument("--imgsz", type=int, default=320)
    ap.add_argument("--edgetpu", action="store_true")
    ap.add_argument("--out", default=os.path.join(HERE, "..", "models"))
    ap.add_argument("--model-name", default="silm_farm")
    args = ap.parse_args()

    from ultralytics import YOLO

    model = YOLO(args.weights)
    # INT8 TFLite needs a representative dataset; Ultralytics uses the data.yaml
    # recorded in the weights. fraction keeps calibration quick.
    print("Exporting INT8 TFLite ...")
    model.export(format="tflite", int8=True, imgsz=args.imgsz)

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
    write_labels(labels)
    print(f"\nCPU model  -> {dst}")
    print(f"labels     -> {labels}")

    if args.edgetpu:
        print("\nExporting Edge-TPU model ...")
        try:
            model.export(format="edgetpu", imgsz=args.imgsz)
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
    print(f"  detector.format: yolo")
    print(f"  detector.model:  models/{args.model_name}.tflite")
    print(f"  detector.labels: models/{args.model_name}_labels.txt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
