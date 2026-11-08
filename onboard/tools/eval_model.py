#!/usr/bin/env python3
"""Run the ONBOARD detector over a folder of images and report what it sees.

This exercises the exact same code path the aircraft uses (silm_recon.detector),
so if a model works here it will work in flight. It prints per-class counts and
optionally writes annotated images so you can eyeball quality.

    python3 tools/eval_model.py --model models/silm_farm.tflite \
        --labels models/silm_farm_labels.txt --format yolo \
        --images some/folder --out /tmp/annotated --score 0.4
"""

from __future__ import annotations

import argparse
import glob
import os
import sys
from collections import Counter

import cv2

# Allow running as `python3 tools/eval_model.py` from the onboard/ dir.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from silm_recon.detector import ObjectDetector


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--labels", required=True)
    ap.add_argument("--format", default="auto", choices=["auto", "ssd", "yolo"])
    ap.add_argument("--images", required=True, help="file or folder of images")
    ap.add_argument("--out", default=None, help="write annotated images here")
    ap.add_argument("--score", type=float, default=0.4)
    ap.add_argument("--limit", type=int, default=50)
    args = ap.parse_args()

    det = ObjectDetector(args.model, args.labels, args.score, fmt=args.format)
    print(f"model input {det.input_size}, format={det.fmt}, "
          f"{len(det.labels)} labels")

    if os.path.isdir(args.images):
        files = sorted(
            glob.glob(os.path.join(args.images, "*.jpg"))
            + glob.glob(os.path.join(args.images, "*.png"))
        )[:args.limit]
    else:
        files = [args.images]

    if args.out:
        os.makedirs(args.out, exist_ok=True)

    counts: Counter = Counter()
    frames_with_det = 0
    for f in files:
        img = cv2.imread(f)
        if img is None:
            continue
        dets = det.detect(img)
        if dets:
            frames_with_det += 1
        for d in dets:
            counts[d.label] += 1
        if args.out:
            h, w = img.shape[:2]
            for d in dets:
                x0, y0, x1, y1 = d.box
                cv2.rectangle(img, (int(x0 * w), int(y0 * h)),
                              (int(x1 * w), int(y1 * h)), (0, 255, 255), 2)
                cv2.putText(img, f"{d.label} {d.score:.2f}",
                            (int(x0 * w), max(12, int(y0 * h) - 5)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
            cv2.imwrite(os.path.join(args.out, os.path.basename(f)), img)

    print(f"\nimages: {len(files)}  with >=1 detection: {frames_with_det}")
    print("detections per class:")
    for label, n in counts.most_common():
        print(f"  {label:16s} {n}")
    if not counts:
        print("  (none above score threshold)")
    if args.out:
        print(f"annotated images -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
