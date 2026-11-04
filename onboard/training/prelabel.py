#!/usr/bin/env python3
"""Draft labels for new photos with your current model (model-assisted labelling).

Drawing every box by hand is the slowest part of adding data. This runs your
current model over a folder of photos (e.g. a flight's `frames/`) and writes a
labelling-tool folder: each image with a YOLO .txt of the model's guesses and a
classes.txt. Open it in a labelling tool, fix the wrong boxes, delete false
ones, draw what was missed - then feed the folder to merge_datasets.py.

    # draft labels for every frame of a flight
    python3 prelabel.py --weights runs/detect/farm_v2/weights/best.pt \\
        --images /media/sd/mission_20261110_101500/frames --out to_label/flight_1110

    # adding a NEW class the model doesn't know yet: list it with --add-class so
    # it appears in classes.txt and you can draw it in the labelling tool
    python3 prelabel.py --weights ... --images ... --out ... --add-class deer

    # only the 200 frames the model was least sure about (most worth labelling)
    python3 prelabel.py --weights ... --images ... --out ... --select uncertain --max 200

--weights takes a .pt (Ultralytics, on your training PC) or the deployed
.tflite (needs --labels), so it works on the Pi too.
"""

from __future__ import annotations

import argparse
import os
import random
import shutil
import sys

IMG_EXT = (".jpg", ".jpeg", ".png", ".bmp", ".webp")


def find_images(folder: str) -> list[str]:
    out = []
    for dp, dn, fs in os.walk(folder):
        dn[:] = sorted(dn)
        out += [os.path.join(dp, f) for f in sorted(fs) if f.lower().endswith(IMG_EXT)]
    return out


def predict_pt(weights, images, conf, imgsz):
    from ultralytics import YOLO
    model = YOLO(weights)
    names = [model.names[i] for i in sorted(model.names)]
    preds = {}
    for img in images:
        r = model.predict(img, conf=conf, imgsz=imgsz, verbose=False)[0]
        rows = []
        for (xc, yc, w, h), c, s in zip(r.boxes.xywhn.tolist(), r.boxes.cls.tolist(),
                                        r.boxes.conf.tolist()):
            rows.append((int(c), float(s), xc, yc, w, h))
        preds[img] = rows
    return names, preds


def predict_tflite(weights, labels, images, conf):
    import cv2
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    from silm_recon.detector import ObjectDetector
    det = ObjectDetector(weights, labels, conf, fmt="auto")
    names = [det.labels[i] for i in sorted(det.labels)]
    idx = {n: i for i, n in enumerate(names)}
    preds = {}
    for img in images:
        im = cv2.imread(img)
        rows = []
        if im is not None:
            for d in det.detect(im):
                x0, y0, x1, y1 = d.box
                rows.append((idx[d.label], d.score, (x0 + x1) / 2, (y0 + y1) / 2, x1 - x0, y1 - y0))
        preds[img] = rows
    return names, preds


def select_images(preds: dict, mode: str, max_n: int | None, sure: float, seed: int = 0):
    """'all' keeps every image; 'uncertain' ranks images by how many of their
    detections are borderline (confidence below `sure`)."""
    imgs = list(preds)
    if mode == "uncertain":
        rng = random.Random(seed)
        rng.shuffle(imgs)  # random tie-break
        imgs.sort(key=lambda p: -sum(1 for r in preds[p] if r[1] < sure))
    if max_n:
        imgs = imgs[:max_n]
    return imgs


def write_prelabels(preds: dict, names: list[str], images: list[str], src_root: str,
                    out: str, add_classes=(), min_conf: float = 0.0) -> list[str]:
    classes = list(names) + [c for c in add_classes if c not in names]
    os.makedirs(out, exist_ok=True)
    for img in images:
        rel = os.path.relpath(img, src_root)
        dst = os.path.join(out, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(img, dst)
        rows = [r for r in preds[img] if r[1] >= min_conf]
        with open(os.path.splitext(dst)[0] + ".txt", "w") as fh:
            for c, _s, xc, yc, w, h in rows:
                fh.write(f"{c} {xc:.6f} {yc:.6f} {w:.6f} {h:.6f}\n")
    with open(os.path.join(out, "classes.txt"), "w") as fh:
        fh.write("\n".join(classes) + "\n")
    return classes


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--weights", required=True, help=".pt or .tflite")
    ap.add_argument("--labels", help="labels file (needed for .tflite)")
    ap.add_argument("--images", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--conf", type=float, default=0.25, help="keep guesses above this")
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--add-class", action="append", default=[], metavar="NAME",
                    help="new class name(s) to offer in the labelling tool")
    ap.add_argument("--select", choices=["all", "uncertain"], default="all")
    ap.add_argument("--sure", type=float, default=0.6,
                    help="detections below this count as uncertain")
    ap.add_argument("--max", type=int, default=None)
    args = ap.parse_args(argv)

    images = find_images(args.images)
    if not images:
        print(f"no images in {args.images}")
        return 1
    print(f"running the model over {len(images)} images ...")
    if args.weights.endswith(".tflite"):
        if not args.labels:
            print("--labels is required with a .tflite model")
            return 2
        names, preds = predict_tflite(args.weights, args.labels, images, args.conf)
    else:
        names, preds = predict_pt(args.weights, images, args.conf, args.imgsz)

    chosen = select_images(preds, args.select, args.max, args.sure)
    classes = write_prelabels(preds, names, chosen, args.images, args.out, args.add_class)
    n_boxes = sum(len(preds[p]) for p in chosen)
    print(f"wrote {len(chosen)} images with {n_boxes} draft boxes -> {args.out}")
    print(f"classes.txt: {classes}")
    print("next: open the folder in a labelling tool, correct every image, then merge it:")
    print(f"  python3 merge_datasets.py --out datasets/merged <your other datasets> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
