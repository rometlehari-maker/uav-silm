#!/usr/bin/env python3
"""Pick a confidence threshold per class from measured precision and recall.

Runs the deployed model (through the aircraft's own detector code) over a
LABELLED split - ideally your held-out test flight - and, for each class and
each threshold, matches detections to the true boxes and reports:

  precision  of the boxes it reported, how many were right (low = false alarms)
  recall     of the real objects, how many it found         (low = misses)
  FP/img     false alarms per image

    python3 tools/threshold_sweep.py --model models/farm_v2.tflite \\
        --labels models/farm_v2_labels.txt --data training/datasets/merged_v2/data.yaml \\
        --split test --classes deer

Choose the threshold that gives the recall you need with false alarms you can
live with, and put it in config.yaml under detector.class_thresholds.
"""

from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "training"))


def iou(a, b):
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def sweep(dets, gts, thresholds, iou_thr=0.5):
    """dets / gts: {image: [(label, score_or_None, (x0, y0, x1, y1)), ...]}.
    Returns {label: {thr: dict(tp, fp, fn, precision, recall, fp_per_img)}}."""
    labels = sorted({d[0] for v in dets.values() for d in v} | {g[0] for v in gts.values() for g in v})
    n_img = max(1, len(gts))
    out = {}
    for lab in labels:
        out[lab] = {}
        for thr in thresholds:
            tp = fp = fn = 0
            for img, gt in gts.items():
                g = [x[2] for x in gt if x[0] == lab]
                d = sorted((x for x in dets.get(img, []) if x[0] == lab and x[1] >= thr),
                           key=lambda x: -x[1])
                used = set()
                for _, _, box in d:
                    best, bi = 0.0, None
                    for j, gb in enumerate(g):
                        if j not in used:
                            v = iou(box, gb)
                            if v > best:
                                best, bi = v, j
                    if bi is not None and best >= iou_thr:
                        used.add(bi)
                        tp += 1
                    else:
                        fp += 1
                fn += len(g) - len(used)
            out[lab][thr] = dict(tp=tp, fp=fp, fn=fn,
                                 precision=tp / (tp + fp) if tp + fp else None,
                                 recall=tp / (tp + fn) if tp + fn else None,
                                 fp_per_img=fp / n_img)
    return out


def load_split(data_yaml, split):
    from check_dataset import label_path_for, split_images
    names, splits = split_images(data_yaml)
    if split not in splits:
        raise SystemExit(f"{data_yaml} has no '{split}' split (has: {sorted(splits)})")
    gts = {}
    for img in splits[split]:
        rows = []
        lp = label_path_for(img)
        if os.path.exists(lp):
            for line in open(lp):
                v = line.split()
                if len(v) == 5:
                    c, x, y, w, h = int(v[0]), *map(float, v[1:])
                    rows.append((names[c], None, (x - w / 2, y - h / 2, x + w / 2, y + h / 2)))
        gts[img] = rows
    return gts


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", required=True)
    ap.add_argument("--labels", required=True)
    ap.add_argument("--format", default="auto", choices=["auto", "ssd", "yolo"])
    ap.add_argument("--data", required=True, help="a dataset's data.yaml")
    ap.add_argument("--split", default="test")
    ap.add_argument("--classes", default=None, help="comma-separated classes to report (default all)")
    ap.add_argument("--thresholds", default="0.25,0.35,0.45,0.55,0.65,0.75")
    ap.add_argument("--iou", type=float, default=0.5)
    a = ap.parse_args(argv)

    import cv2
    from silm_recon.detector import ObjectDetector

    thresholds = [float(t) for t in a.thresholds.split(",")]
    gts = load_split(a.data, a.split)
    det = ObjectDetector(a.model, a.labels, min(thresholds), fmt=a.format)
    dets = {}
    for img in gts:
        im = cv2.imread(img)
        dets[img] = [] if im is None else [(d.label, d.score, d.box) for d in det.detect(im)]

    res = sweep(dets, gts, thresholds, a.iou)
    want = [c.strip() for c in a.classes.split(",")] if a.classes else sorted(res)
    print(f"{len(gts)} images in '{a.split}', IoU >= {a.iou}")
    for lab in want:
        if lab not in res:
            print(f"\n{lab}: not in the model's output or the labels")
            continue
        n_true = res[lab][thresholds[0]]["tp"] + res[lab][thresholds[0]]["fn"]
        print(f"\n{lab}  ({n_true} labelled)")
        print(f"  {'threshold':>9s} {'precision':>9s} {'recall':>7s} {'found':>6s} {'false':>6s} {'FP/img':>7s}")
        for t in thresholds:
            r = res[lab][t]
            p = "-" if r["precision"] is None else f"{r['precision']:.2f}"
            rc = "-" if r["recall"] is None else f"{r['recall']:.2f}"
            print(f"  {t:9.2f} {p:>9s} {rc:>7s} {r['tp']:6d} {r['fp']:6d} {r['fp_per_img']:7.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
