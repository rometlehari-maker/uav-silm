#!/usr/bin/env python3
"""Generate a SYNTHETIC aerial dataset to bootstrap and prove the pipeline.

This paints simple, class-distinct shapes onto textured field/forest/road
backgrounds and writes YOLO-format labels. Its ONLY purpose is to let you run
the whole train -> export -> deploy chain end to end and see a real model
detect the right classes. A model trained only on this will NOT work on real
aerial footage -- synthetic shapes are not combines. Replace it with real
labelled data (see training/README.md) for anything flight-useful.

Usage:
    python3 make_synthetic.py --out datasets/synth --n-train 600 --n-val 120
"""

from __future__ import annotations

import argparse
import os
import random

import cv2
import numpy as np
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))


def load_classes() -> list[str]:
    with open(os.path.join(HERE, "classes.yaml")) as fh:
        names = yaml.safe_load(fh)["names"]
    return [names[i] for i in range(len(names))]


# A distinct colour (BGR) + shape recipe per class so the net can separate them.
# These are deliberately cartoonish; they stand in for real objects only so the
# training loop has something learnable.
def class_style(idx: int) -> dict:
    palette = [
        (60, 60, 60),    # person - dark
        (200, 40, 40),   # car - blue
        (40, 120, 200),  # truck - orange
        (40, 180, 40),   # tractor - green
        (40, 210, 210),  # combine - yellow
        (20, 150, 120),  # harvester - teal
        (120, 90, 40),   # baler - brown-blue
        (180, 200, 220), # hay_bale - pale
        (140, 140, 140), # livestock - grey
        (180, 180, 120), # silo - light
        (30, 30, 160),   # barn - red
        (80, 40, 120),   # logging_truck - purple
        (60, 100, 140),  # timber_stack - tan
        (30, 90, 255),   # fire - orange-red
        (200, 200, 200), # smoke - light grey
    ]
    return {"color": palette[idx % len(palette)]}


def make_background(w: int, h: int) -> np.ndarray:
    kind = random.choice(["field", "forest", "soil", "mixed"])
    base = {
        "field": (60, 140, 70),
        "forest": (40, 80, 45),
        "soil": (70, 95, 130),
        "mixed": (55, 110, 70),
    }[kind]
    img = np.full((h, w, 3), base, np.uint8)
    # texture: low-frequency noise + a few furrow/edge lines
    noise = (np.random.randn(h // 8, w // 8, 3) * 18).astype(np.int16)
    noise = cv2.resize(noise.astype(np.float32), (w, h)).astype(np.int16)
    img = np.clip(img.astype(np.int16) + noise, 0, 255).astype(np.uint8)
    for _ in range(random.randint(0, 4)):
        y = random.randint(0, h)
        cv2.line(img, (0, y), (w, y + random.randint(-40, 40)),
                 tuple(int(c * 0.8) for c in base), random.randint(1, 3))
    return img


def draw_object(img, idx, style):
    h, w = img.shape[:2]
    # object size as a fraction of frame (aerial: objects are smallish)
    ow = random.randint(int(w * 0.04), int(w * 0.16))
    oh = int(ow * random.uniform(0.4, 1.1))
    cx = random.randint(ow, w - ow)
    cy = random.randint(oh, h - oh)
    color = style["color"]
    angle = random.uniform(0, 360)

    overlay = img.copy()
    if idx in (13, 14):  # fire / smoke: soft irregular blobs
        for _ in range(random.randint(6, 14)):
            rx = random.randint(-ow // 2, ow // 2)
            ry = random.randint(-oh // 2, oh // 2)
            r = random.randint(ow // 6, ow // 3)
            jitter = tuple(min(255, max(0, c + random.randint(-30, 30))) for c in color)
            cv2.circle(overlay, (cx + rx, cy + ry), r, jitter, -1)
        alpha = 0.6 if idx == 14 else 0.85
        img = cv2.addWeighted(overlay, alpha, img, 1 - alpha, 0)
    else:
        # rotated rectangle body + a small detail rectangle (cab/head)
        rect = ((cx, cy), (ow, oh), angle)
        box = cv2.boxPoints(rect).astype(np.int32)
        cv2.fillConvexPoly(overlay, box, color)
        # detail
        drect = ((cx, cy), (max(3, ow // 3), max(3, oh // 3)), angle)
        dbox = cv2.boxPoints(drect).astype(np.int32)
        cv2.fillConvexPoly(overlay, dbox, tuple(int(c * 0.6) for c in color))
        img = cv2.addWeighted(overlay, 0.95, img, 0.05, 0)

    # axis-aligned bbox for YOLO label
    x0 = max(0, cx - ow // 2); y0 = max(0, cy - oh // 2)
    x1 = min(w, cx + ow // 2); y1 = min(h, cy + oh // 2)
    xc = (x0 + x1) / 2 / w; yc = (y0 + y1) / 2 / h
    bw = (x1 - x0) / w; bh = (y1 - y0) / h
    return img, (xc, yc, bw, bh)


def gen_split(out_dir, split, n, classes, img_wh):
    w, h = img_wh
    img_dir = os.path.join(out_dir, "images", split)
    lbl_dir = os.path.join(out_dir, "labels", split)
    os.makedirs(img_dir, exist_ok=True)
    os.makedirs(lbl_dir, exist_ok=True)
    for i in range(n):
        img = make_background(w, h)
        labels = []
        for _ in range(random.randint(1, 4)):
            idx = random.randint(0, len(classes) - 1)
            img, (xc, yc, bw, bh) = draw_object(img, idx, class_style(idx))
            labels.append(f"{idx} {xc:.6f} {yc:.6f} {bw:.6f} {bh:.6f}")
        name = f"{split}_{i:05d}"
        cv2.imwrite(os.path.join(img_dir, name + ".jpg"), img,
                    [cv2.IMWRITE_JPEG_QUALITY, 85])
        with open(os.path.join(lbl_dir, name + ".txt"), "w") as fh:
            fh.write("\n".join(labels) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(HERE, "datasets", "synth"))
    ap.add_argument("--n-train", type=int, default=600)
    ap.add_argument("--n-val", type=int, default=120)
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--height", type=int, default=480)
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()

    random.seed(args.seed)
    np.random.seed(args.seed)
    classes = load_classes()
    out = os.path.abspath(args.out)

    gen_split(out, "train", args.n_train, classes, (args.width, args.height))
    gen_split(out, "val", args.n_val, classes, (args.width, args.height))

    data_yaml = {
        "path": out,
        "train": "images/train",
        "val": "images/val",
        "names": {i: c for i, c in enumerate(classes)},
    }
    data_path = os.path.join(out, "data.yaml")
    with open(data_path, "w") as fh:
        yaml.safe_dump(data_yaml, fh, sort_keys=False)
    print(f"wrote synthetic dataset -> {out}")
    print(f"  train={args.n_train} val={args.n_val} classes={len(classes)}")
    print(f"  dataset config: {data_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
