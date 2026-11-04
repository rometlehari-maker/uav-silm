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
import hashlib
import os
import random

import cv2
import numpy as np
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))


def load_classes(path: str | None = None) -> list[str]:
    with open(path or os.path.join(HERE, "classes.yaml")) as fh:
        names = yaml.safe_load(fh)["names"]
    if isinstance(names, dict):
        return [names[i] for i in sorted(names)]
    return list(names)


# A distinct colour (BGR) per class NAME so the net can separate them, and so a
# class looks the same in every synthetic dataset whatever index it has there.
# Deliberately cartoonish: these stand in for real objects only so the
# training loop has something learnable.
KNOWN_COLORS = {
    "person": (60, 60, 60), "car": (200, 40, 40), "truck": (40, 120, 200),
    "tractor": (40, 180, 40), "combine": (40, 210, 210), "harvester": (20, 150, 120),
    "baler": (120, 90, 40), "hay_bale": (180, 200, 220), "livestock": (140, 140, 140),
    "silo": (180, 180, 120), "barn": (30, 30, 160), "logging_truck": (80, 40, 120),
    "timber_stack": (60, 100, 140), "fire": (30, 90, 255), "smoke": (200, 200, 200),
}
BLOB_CLASSES = {"fire", "smoke"}


def class_style(name: str) -> dict:
    if name in KNOWN_COLORS:
        return {"color": KNOWN_COLORS[name]}
    # New classes: a stable colour derived from the name.
    h = int(hashlib.md5(name.encode()).hexdigest()[:6], 16)
    hsv = np.uint8([[[h % 180, 160 + (h >> 8) % 80, 120 + (h >> 16) % 120]]])
    b, g, r = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)[0, 0]
    return {"color": (int(b), int(g), int(r))}


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


def draw_object(img, name, style):
    h, w = img.shape[:2]
    # object size as a fraction of frame (aerial: objects are smallish)
    ow = random.randint(int(w * 0.04), int(w * 0.16))
    oh = int(ow * random.uniform(0.4, 1.1))
    cx = random.randint(ow, w - ow)
    cy = random.randint(oh, h - oh)
    color = style["color"]
    angle = random.uniform(0, 360)

    overlay = img.copy()
    if name in BLOB_CLASSES:  # fire / smoke: soft irregular blobs
        ext = []  # extent of every circle, so the box covers the whole blob
        for _ in range(random.randint(6, 14)):
            rx = random.randint(-ow // 2, ow // 2)
            ry = random.randint(-oh // 2, oh // 2)
            r = random.randint(ow // 6, ow // 3)
            jitter = tuple(min(255, max(0, c + random.randint(-30, 30))) for c in color)
            cv2.circle(overlay, (cx + rx, cy + ry), r, jitter, -1)
            ext.append((cx + rx - r, cy + ry - r, cx + rx + r, cy + ry + r))
        bx0, by0 = min(e[0] for e in ext), min(e[1] for e in ext)
        bx1, by1 = max(e[2] for e in ext), max(e[3] for e in ext)
        alpha = 0.6 if name == "smoke" else 0.85
        img = cv2.addWeighted(overlay, alpha, img, 1 - alpha, 0)
    else:
        # rotated rectangle body + a small detail rectangle (cab/head)
        rect = ((cx, cy), (ow, oh), angle)
        box = cv2.boxPoints(rect).astype(np.int32)
        cv2.fillConvexPoly(overlay, box, color)
        # the label box must enclose the ROTATED shape, not its unrotated size
        bx0, by0 = box[:, 0].min(), box[:, 1].min()
        bx1, by1 = box[:, 0].max(), box[:, 1].max()
        # detail
        drect = ((cx, cy), (max(3, ow // 3), max(3, oh // 3)), angle)
        dbox = cv2.boxPoints(drect).astype(np.int32)
        cv2.fillConvexPoly(overlay, dbox, tuple(int(c * 0.6) for c in color))
        img = cv2.addWeighted(overlay, 0.95, img, 0.05, 0)

    # tight axis-aligned box around everything drawn, clipped to the image
    x0, y0 = max(0, int(bx0)), max(0, int(by0))
    x1, y1 = min(w, int(bx1)), min(h, int(by1))
    xc = (x0 + x1) / 2 / w; yc = (y0 + y1) / 2 / h
    bw = (x1 - x0) / w; bh = (y1 - y0) / h
    return img, (xc, yc, bw, bh)


def gen_images(img_dir, lbl_dir, prefix, n, label_names, draw_names, img_wh):
    """Write n images. Objects are drawn from `draw_names`; label indices are
    positions in `label_names` (the class list this dataset declares)."""
    w, h = img_wh
    os.makedirs(img_dir, exist_ok=True)
    os.makedirs(lbl_dir, exist_ok=True)
    for i in range(n):
        img = make_background(w, h)
        labels = []
        for _ in range(random.randint(1, 4)):
            name = random.choice(draw_names)
            img, (xc, yc, bw, bh) = draw_object(img, name, class_style(name))
            labels.append(f"{label_names.index(name)} {xc:.6f} {yc:.6f} {bw:.6f} {bh:.6f}")
        stem = f"{prefix}_{i:05d}"
        cv2.imwrite(os.path.join(img_dir, stem + ".jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 85])
        with open(os.path.join(lbl_dir, stem + ".txt"), "w") as fh:
            fh.write("\n".join(labels) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(HERE, "datasets", "synth"))
    ap.add_argument("--classes", default=None,
                    help="class list YAML (default: training/classes.yaml)")
    ap.add_argument("--only", default=None,
                    help="comma-separated class names to draw (default: all)")
    ap.add_argument("--layout", choices=["yolo", "flat"], default="yolo",
                    help="yolo: images/{train,val} + labels/{train,val} + data.yaml. "
                         "flat: one folder of images with .txt labels beside them and a "
                         "classes.txt, like a labelling tool writes (n-train images)")
    ap.add_argument("--n-train", type=int, default=600)
    ap.add_argument("--n-val", type=int, default=120)
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--height", type=int, default=480)
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()

    random.seed(args.seed)
    np.random.seed(args.seed)
    classes = load_classes(args.classes)
    draw = [c.strip() for c in args.only.split(",")] if args.only else classes
    unknown = [c for c in draw if c not in classes]
    if unknown:
        print(f"ERROR: {unknown} not in the class list {classes}")
        return 2
    out = os.path.abspath(args.out)
    wh = (args.width, args.height)

    if args.layout == "flat":
        # A labelling tool's folder: indices refer to classes.txt, which lists
        # only the classes in this folder (so they differ from classes.yaml).
        gen_images(out, out, "img", args.n_train, draw, draw, wh)
        with open(os.path.join(out, "classes.txt"), "w") as fh:
            fh.write("\n".join(draw) + "\n")
        print(f"wrote {args.n_train} images (flat layout, classes.txt = {draw}) -> {out}")
        return 0

    gen_images(os.path.join(out, "images", "train"), os.path.join(out, "labels", "train"),
               "train", args.n_train, classes, draw, wh)
    gen_images(os.path.join(out, "images", "val"), os.path.join(out, "labels", "val"),
               "val", args.n_val, classes, draw, wh)
    data_yaml = {"path": out, "train": "images/train", "val": "images/val",
                 "names": {i: c for i, c in enumerate(classes)}}
    data_path = os.path.join(out, "data.yaml")
    with open(data_path, "w") as fh:
        yaml.safe_dump(data_yaml, fh, sort_keys=False)
    print(f"wrote synthetic dataset -> {out}")
    print(f"  train={args.n_train} val={args.n_val} classes={len(classes)}")
    print(f"  dataset config: {data_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
