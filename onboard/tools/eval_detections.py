#!/usr/bin/env python3
"""Measure a model against labelled pictures: precision and recall per class.

Runs the ONBOARD detector (silm_recon.detector, the code the aircraft runs),
optionally tiled like in flight, over a folder of images with YOLO-format
labels (one .txt per image: "class cx cy w h", normalised), and matches its
boxes to the labelled ones (IoU >= 0.5, best score first).

    python3 tools/eval_detections.py --model models/silm_oi.tflite \
        --labels models/silm_oi_labels.txt --images DIR/images --gt DIR/labels \
        --names DIR/names.txt --classes car,truck --grid 2 --score 0.35

Labelled boxes smaller than --min-px (their longer side, in the original
picture) can't reasonably be found: they count neither as found nor as missed,
and a detection on one is not a false alarm. A label named "ignore" marks a
region whose objects weren't labelled (VisDrone has these): detections inside
it are not counted either.

Use it with your own flight pictures marked in the Teach tab (exported as
YOLO), or with a public top-down set such as VisDrone's validation pictures
(training/visdrone_to_yolo.py) - for measuring only.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from silm_recon.detector import ObjectDetector  # noqa: E402

IMG_EXT = (".jpg", ".jpeg", ".png")


def iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def read_gt(path: str, names: list[str]) -> list[tuple[str, tuple]]:
    out = []
    if not os.path.isfile(path):
        return out
    for line in open(path):
        p = line.split()
        if len(p) < 5:
            continue
        c, cx, cy, w, h = int(p[0]), *map(float, p[1:5])
        out.append((names[c], (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)))
    return out


def match_image(dets, gts, classes, img_w, img_h, min_px, iou_thr=0.5) -> dict:
    """Counts per class: tp, fp, fn. dets: [(label, score, box)], gts: [(label, box)];
    boxes normalised (x0, y0, x1, y1)."""
    ignore = [b for n, b in gts if n == "ignore"]
    res = {c: {"tp": 0, "fp": 0, "fn": 0} for c in classes}
    for c in classes:
        g = [b for n, b in gts if n == c]
        small = [max((b[2] - b[0]) * img_w, (b[3] - b[1]) * img_h) < min_px for b in g]
        used = [False] * len(g)
        for _, score, box in sorted((d for d in dets if d[0] == c), key=lambda d: -d[1]):
            best, bi = 0.0, -1
            for i, gb in enumerate(g):
                if not used[i]:
                    v = iou(box, gb)
                    if v > best:
                        best, bi = v, i
            if best >= iou_thr:
                used[bi] = True
                if not small[bi]:
                    res[c]["tp"] += 1
            elif any(iou(box, ib) > 0 or (ib[0] <= (box[0] + box[2]) / 2 <= ib[2]
                                           and ib[1] <= (box[1] + box[3]) / 2 <= ib[3]) for ib in ignore):
                pass                                   # inside an unlabelled region
            elif any(iou(box, gb) >= iou_thr for gb, s in zip(g, small) if s):
                pass
            else:
                res[c]["fp"] += 1
        res[c]["fn"] += sum(1 for u, s in zip(used, small) if not u and not s)
    return res


def summarise(totals: dict) -> dict:
    out = {}
    for c, t in list(totals.items()) + [("all", {k: sum(v[k] for v in totals.values()) for k in ("tp", "fp", "fn")})]:
        p = t["tp"] / (t["tp"] + t["fp"]) if t["tp"] + t["fp"] else 0.0
        r = t["tp"] / (t["tp"] + t["fn"]) if t["tp"] + t["fn"] else 0.0
        f1 = 2 * p * r / (p + r) if p + r else 0.0
        out[c] = {**t, "precision": round(p, 3), "recall": round(r, 3), "f1": round(f1, 3)}
    return out


def evaluate(model, labels, images, gt, names, classes, grid=1, score=0.35, min_px=16,
             fmt="auto", limit=0) -> dict:
    det = ObjectDetector(model, labels, score, fmt=fmt)
    files = sorted(f for f in os.listdir(images) if f.lower().endswith(IMG_EXT))
    if limit:
        files = files[:limit]
    totals = {c: {"tp": 0, "fp": 0, "fn": 0} for c in classes}
    for f in files:
        img = cv2.imread(os.path.join(images, f))
        if img is None:
            continue
        h, w = img.shape[:2]
        dets = det.detect_roi(img, None, (grid, grid))
        r = match_image([(d.label, d.score, d.box) for d in dets],
                        read_gt(os.path.join(gt, os.path.splitext(f)[0] + ".txt"), names),
                        classes, w, h, min_px)
        for c in classes:
            for k in ("tp", "fp", "fn"):
                totals[c][k] += r[c][k]
    return {"images": len(files), "grid": grid, "score": score, "min_px": min_px,
            "classes": summarise(totals)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", required=True)
    ap.add_argument("--labels", required=True)
    ap.add_argument("--format", default="auto", choices=["auto", "ssd", "yolo"])
    ap.add_argument("--images", required=True)
    ap.add_argument("--gt", required=True, help="folder of YOLO .txt labels")
    ap.add_argument("--names", required=True, help="text file: one label name per class number")
    ap.add_argument("--classes", required=True, help="comma-separated classes to score, e.g. car,truck")
    ap.add_argument("--grid", type=int, default=1, help="tiles per side, as in flight (detector.tile_grid)")
    ap.add_argument("--score", type=float, default=0.35)
    ap.add_argument("--min-px", type=float, default=16)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--json", default=None, help="also write the result here")
    a = ap.parse_args()
    names = [n.strip() for n in open(a.names) if n.strip()]
    res = evaluate(a.model, a.labels, a.images, a.gt, names, a.classes.split(","), a.grid, a.score,
                   a.min_px, a.format, a.limit)
    print(f"{res['images']} images, grid {a.grid}x{a.grid}, score >= {a.score}, boxes >= {a.min_px:g} px")
    print(f"{'class':10s} {'found':>6s} {'false':>6s} {'missed':>6s} {'precision':>9s} {'recall':>7s} {'F1':>6s}")
    for c, v in res["classes"].items():
        print(f"{c:10s} {v['tp']:6d} {v['fp']:6d} {v['fn']:6d} {v['precision']:9.3f} {v['recall']:7.3f} {v['f1']:6.3f}")
    if a.json:
        with open(a.json, "w") as fh:
            json.dump(res, fh, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
