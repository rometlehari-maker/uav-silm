#!/usr/bin/env python3
"""Cut large labelled pictures into tiles, like the aircraft does in flight.

In flight the Pi cuts the middle of every 1920x1080 camera picture into tiles
(detector.tile_grid) and gives each tile to the model at 320 px. A drone
picture shrunk whole to 320 px makes a cow one or two pixels - nothing to
learn from. This cuts every picture larger than --tile into overlapping
--tile x --tile pieces, keeps each box that lies mostly inside a piece
(clipped to it), and leaves smaller pictures as they are.

    python3 tile_dataset.py SRC --out datasets/src_tiled --tile 640

SRC is a YOLO dataset: {train,valid,val,test}/images + labels (Roboflow
export) or images/{train,val} + labels/{train,val}. data.yaml is copied.
Tiles with no box are kept at --empty-frac (default 0.3) so the model also
sees empty field.
"""

from __future__ import annotations

import argparse
import os
import random
import shutil

import cv2

SPLITS = ("train", "valid", "val", "test")


def find_splits(src: str) -> list[tuple[str, str, str]]:
    """[(split, images_dir, labels_dir)] for both YOLO layouts."""
    out = []
    for s in SPLITS:
        if os.path.isdir(os.path.join(src, s, "images")):
            out.append((s, os.path.join(src, s, "images"), os.path.join(src, s, "labels")))
        elif os.path.isdir(os.path.join(src, "images", s)):
            out.append((s, os.path.join(src, "images", s), os.path.join(src, "labels", s)))
    return out


def tile_boxes(boxes, x0, y0, tw, th, w, h, min_keep=0.5):
    """boxes: [(cls, cx, cy, bw, bh)] normalised to the picture -> normalised to the tile."""
    out = []
    for c, cx, cy, bw, bh in boxes:
        bx0, by0, bx1, by1 = (cx - bw / 2) * w, (cy - bh / 2) * h, (cx + bw / 2) * w, (cy + bh / 2) * h
        ix0, iy0 = max(bx0, x0), max(by0, y0)
        ix1, iy1 = min(bx1, x0 + tw), min(by1, y0 + th)
        if ix1 <= ix0 or iy1 <= iy0:
            continue
        area = (bx1 - bx0) * (by1 - by0)
        if area <= 0 or (ix1 - ix0) * (iy1 - iy0) / area < min_keep:
            continue
        out.append((c, ((ix0 + ix1) / 2 - x0) / tw, ((iy0 + iy1) / 2 - y0) / th,
                    (ix1 - ix0) / tw, (iy1 - iy0) / th))
    return out


def starts(length: int, tile: int, overlap: float) -> list[int]:
    if length <= tile:
        return [0]
    step = int(tile * (1 - overlap))
    s = list(range(0, length - tile, step))
    return s + [length - tile]


def tile_dataset(src: str, out: str, tile: int = 640, overlap: float = 0.15, empty_frac: float = 0.3,
                 seed: int = 1) -> dict:
    rnd = random.Random(seed)
    counts = {}
    for split, idir, ldir in find_splits(src):
        oi, ol = os.path.join(out, split, "images"), os.path.join(out, split, "labels")
        os.makedirs(oi, exist_ok=True)
        os.makedirs(ol, exist_ok=True)
        n = 0
        for f in sorted(os.listdir(idir)):
            stem, ext = os.path.splitext(f)
            if ext.lower() not in (".jpg", ".jpeg", ".png"):
                continue
            img = cv2.imread(os.path.join(idir, f))
            if img is None:
                continue
            h, w = img.shape[:2]
            lp = os.path.join(ldir, stem + ".txt")
            boxes = []
            for line in open(lp) if os.path.isfile(lp) else []:
                p = line.split()
                if len(p) >= 5:
                    boxes.append((int(p[0]), *map(float, p[1:5])))
            if max(w, h) <= tile * 1.25:                      # small enough: as it is
                shutil.copyfile(os.path.join(idir, f), os.path.join(oi, f))
                shutil.copyfile(lp, os.path.join(ol, stem + ".txt")) if os.path.isfile(lp) else \
                    open(os.path.join(ol, stem + ".txt"), "w").close()
                n += 1
                continue
            for y0 in starts(h, tile, overlap):
                for x0 in starts(w, tile, overlap):
                    tw, th = min(tile, w - x0), min(tile, h - y0)
                    tb = tile_boxes(boxes, x0, y0, tw, th, w, h)
                    if not tb and rnd.random() > empty_frac:
                        continue
                    name = f"{stem}_t{x0}_{y0}"
                    cv2.imwrite(os.path.join(oi, name + ".jpg"), img[y0:y0 + th, x0:x0 + tw],
                                [cv2.IMWRITE_JPEG_QUALITY, 92])
                    with open(os.path.join(ol, name + ".txt"), "w") as fh:
                        fh.writelines(f"{c} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}\n" for c, cx, cy, bw, bh in tb)
                    n += 1
        counts[split] = n
    for f in ("data.yaml", "README.roboflow.txt", "README.dataset.txt"):
        if os.path.isfile(os.path.join(src, f)):
            shutil.copyfile(os.path.join(src, f), os.path.join(out, f))
    return counts


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src")
    ap.add_argument("--out", required=True)
    ap.add_argument("--tile", type=int, default=640)
    ap.add_argument("--overlap", type=float, default=0.15)
    ap.add_argument("--empty-frac", type=float, default=0.3)
    a = ap.parse_args()
    print(tile_dataset(a.src, a.out, a.tile, a.overlap, a.empty_frac))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
