#!/usr/bin/env python3
"""VisDrone (drone pictures of traffic) -> YOLO labels, for MEASURING a model
from above with tools/eval_detections.py.

VisDrone's licence is non-commercial share-alike (CC BY-NC-SA 3.0 on its
mirrors): use it here to measure, don't train the published model on it and
don't publish the pictures. Download it yourself:

    curl -L -o val.zip https://github.com/ultralytics/assets/releases/download/v0.0.0/VisDrone2019-DET-val.zip
    unzip val.zip
    python3 training/visdrone_to_yolo.py VisDrone2019-DET-val --out visdrone_eval

VisDrone categories -> our names: pedestrian, people -> person; car, van -> car;
truck -> truck; bus -> bus. "Ignored regions" (0) and "others" (11) become
"ignore": regions whose objects weren't labelled, not counted either way.
"""

from __future__ import annotations

import argparse
import os
import shutil

import cv2

NAMES = ["person", "car", "truck", "bus", "ignore"]
MAP = {1: "person", 2: "person", 4: "car", 5: "car", 6: "truck", 9: "bus", 0: "ignore", 11: "ignore"}


def convert(src: str, out: str) -> int:
    img_dir, ann_dir = os.path.join(src, "images"), os.path.join(src, "annotations")
    os.makedirs(os.path.join(out, "images"), exist_ok=True)
    os.makedirs(os.path.join(out, "labels"), exist_ok=True)
    with open(os.path.join(out, "names.txt"), "w") as fh:
        fh.write("\n".join(NAMES) + "\n")
    n = 0
    for f in sorted(os.listdir(img_dir)):
        if not f.lower().endswith(".jpg"):
            continue
        stem = os.path.splitext(f)[0]
        img = cv2.imread(os.path.join(img_dir, f))
        if img is None:
            continue
        h, w = img.shape[:2]
        rows = []
        ann = os.path.join(ann_dir, stem + ".txt")
        for line in open(ann) if os.path.isfile(ann) else []:
            p = [int(v) for v in line.strip().rstrip(",").split(",")[:6]]
            if len(p) < 6:
                continue
            x, y, bw, bh, _, cat = p
            name = MAP.get(cat)
            if name is None or bw <= 0 or bh <= 0:
                continue
            rows.append(f"{NAMES.index(name)} {(x + bw / 2) / w:.6f} {(y + bh / 2) / h:.6f} "
                        f"{bw / w:.6f} {bh / h:.6f}")
        shutil.copyfile(os.path.join(img_dir, f), os.path.join(out, "images", f))
        with open(os.path.join(out, "labels", stem + ".txt"), "w") as fh:
            fh.write("\n".join(rows) + ("\n" if rows else ""))
        n += 1
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", help="unpacked VisDrone2019-DET-* folder (with images/ and annotations/)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    print(f"{convert(a.src, a.out)} pictures -> {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
