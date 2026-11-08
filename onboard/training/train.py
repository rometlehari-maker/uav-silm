#!/usr/bin/env python3
"""Train / fine-tune the SILM detector with Ultralytics YOLO.

Transfer-learns from a small pretrained YOLO (yolo11n / yolov8n by default) onto
your dataset. Works the same whether the data is the synthetic bootstrap set or
a real labelled set exported from Roboflow / CVAT in YOLO format.

Examples:
    # quick proof on the synthetic set (CPU-friendly)
    python3 train.py --data datasets/synth/data.yaml --model yolo11n.pt \
        --epochs 20 --imgsz 320 --name synth_proof

    # real training run on your own data (use a GPU machine)
    python3 train.py --data datasets/farm/data.yaml --model yolo11s.pt \
        --epochs 150 --imgsz 640 --name farm_v1

The best weights land in runs/detect/<name>/weights/best.pt -> feed to export.py.
"""

from __future__ import annotations

import argparse


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, help="path to a YOLO data.yaml")
    ap.add_argument("--model", default="yolo11n.pt",
                    help="pretrained base: yolo11n/yolo11s/yolov8n.pt ...")
    ap.add_argument("--epochs", type=int, default=100)
    ap.add_argument("--imgsz", type=int, default=640,
                    help="use 320 on CPU / for Pi-sized models")
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--name", default="silm")
    ap.add_argument("--device", default=None, help="'' or 0 for GPU, 'cpu' for CPU")
    ap.add_argument("--patience", type=int, default=30)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--project", default=None,
                    help="folder for the run (default: Ultralytics' runs/detect)")
    args = ap.parse_args()

    from ultralytics import YOLO

    model = YOLO(args.model)
    results = model.train(
        data=args.data,
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        name=args.name,
        device=args.device,
        patience=args.patience,
        resume=args.resume,
        **({"project": args.project} if args.project else {}),
        # Augmentation tuned for top-down aerial imagery, where targets are
        # small, appear at any rotation, and lighting/scale vary a lot. These
        # materially help real-world generalisation from a modest dataset.
        degrees=180.0,      # any rotation when seen from above
        fliplr=0.5,
        flipud=0.5,
        mosaic=1.0,         # 4-image mosaics: more small objects per batch
        mixup=0.1,
        copy_paste=0.1,     # paste objects into new scenes -> more instances
        scale=0.5,          # simulate different altitudes (object size)
        translate=0.1,
        hsv_h=0.015, hsv_s=0.7, hsv_v=0.4,  # season/lighting/time-of-day shift
        close_mosaic=10,    # turn mosaic off for the last epochs to settle
    )
    print("\nTraining done.")
    print("Best weights:", getattr(results, "save_dir", "runs/detect/" + args.name))
    print("Next: python3 export.py --weights runs/detect/%s/weights/best.pt" % args.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
