#!/usr/bin/env python3
"""Build a REAL-photo training set for the SILM classes from Google's Open Images.

No account, API key or FiftyOne needed: it streams Open Images' public
annotation files, picks photos, downloads them and writes a YOLO dataset
(images/ + labels/ train/val + data.yaml) using the class numbers in
classes.yaml, ready for train.py.

    python3 openimages_dataset.py --out datasets/openimages --per-class 400

How each class is labelled:
  car, truck            Open Images' own human-drawn boxes
  livestock             its Cattle, Horse and Sheep boxes
  tractor               photos people VERIFIED as showing a tractor; their
                        "Land vehicle" / "Vehicle" boxes become tractor boxes
                        (Open Images has no tractor box class). Photos that
                        also have car or truck boxes are skipped, so a box is
                        never ambiguous.
  combine               NOT included by default: Open Images' "Harvester" photos
                        turned out to be mostly tractors with implements, hay
                        rakes and sprayers (2 of 8 checked were combines), so
                        their boxes would teach the model the wrong thing.
                        --combine adds them anyway. Use your own pictures.
Drawings (IsDepiction) and boxes around a whole group (IsGroupOf) are dropped.

Licences: the annotations are CC BY 4.0; the photos are mostly CC BY 2.0
(each photo's own licence and author are in Open Images' image list). Keep
attribution if you publish the photos.

IMPORTANT - viewpoint: these are mostly ground-level photos. The aircraft
looks STRAIGHT DOWN. A model trained only on these learns what the objects
look like, but recognises them from above less well. Add your own aerial
pictures (Ground Station > Teach new objects; the ordinary pictures saved in
flight) and retrain - that is what makes it good on your fields.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import csv
import hashlib
import io
import os
import random
import sys
import urllib.request

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
OI = "https://storage.googleapis.com/openimages"
IMG_URL = "https://open-images-dataset.s3.amazonaws.com/train/{}.jpg"

CAR, TRUCK, CATTLE, HORSE, SHEEP = "/m/0k4j", "/m/07r04", "/m/01xq0k1", "/m/03k3r", "/m/07bgp"
LAND_VEHICLE, VEHICLE = "/m/01prls", "/m/07yv9"
TRACTOR_IMG, HARVESTER_IMG = "/m/013xlm", "/m/01_cf6"
BOX_LABELS = {CAR, TRUCK, CATTLE, HORSE, SHEEP, LAND_VEHICLE, VEHICLE}
DIRECT = {CAR: "car", TRUCK: "truck", CATTLE: "livestock", HORSE: "livestock", SHEEP: "livestock"}


def silm_classes() -> list[str]:
    names = yaml.safe_load(open(os.path.join(HERE, "classes.yaml")))["names"]
    return [names[i] for i in sorted(names)]


def stream_lines(url: str):
    with urllib.request.urlopen(url, timeout=120) as r:
        for raw in io.TextIOWrapper(r, encoding="utf-8", newline=""):
            yield raw.rstrip("\r\n")


def cached(cache: str, name: str, url: str, keep) -> str:
    """Stream a big CSV once, keep only matching lines in the cache."""
    p = os.path.join(cache, name)
    if os.path.isfile(p):
        return p
    os.makedirs(cache, exist_ok=True)
    tmp = p + ".part"
    n = 0
    with open(tmp, "w") as out:
        for line in stream_lines(url):
            if keep(line):
                out.write(line + "\n")
                n += 1
    os.replace(tmp, p)
    print(f"  {name}: {n} lines kept")
    return p


def load(cache: str):
    print("Reading Open Images annotations (first run streams ~5 GB, keeps a few MB)...")
    lab = cached(cache, "imagelabels_farm.csv",
                 f"{OI}/v7/oidv7-train-annotations-human-imagelabels.csv",
                 lambda ln: (f",{TRACTOR_IMG}," in ln or f",{HARVESTER_IMG}," in ln)
                 and ln.rsplit(",", 1)[-1] in ("1", "1.0"))      # human-verified: present
    box = cached(cache, "bbox_vehicles.csv", f"{OI}/v6/oidv6-train-annotations-bbox.csv",
                 lambda ln: any(f",{m}," in ln for m in BOX_LABELS))
    verified = {}
    for row in csv.reader(open(lab)):
        verified.setdefault(row[0], set()).add(row[2])
    boxes = {}
    for row in csv.reader(open(box)):
        # ImageID,Source,LabelName,Confidence,XMin,XMax,YMin,YMax,IsOccluded,
        # IsTruncated,IsGroupOf,IsDepiction,IsInside,...
        if len(row) < 12 or row[10] == "1" or row[11] == "1":
            continue
        x0, x1, y0, y1 = map(float, row[4:8])
        boxes.setdefault(row[0], []).append((row[2], x0, y0, x1, y1))
    return verified, boxes


def choose(verified, boxes, per_class: int, seed: int = 1, combine: bool = False):
    """{image_id: [(class_name, x0, y0, x1, y1)]} with ~per_class images per class."""
    rnd = random.Random(seed)
    picked: dict[str, list] = {}
    # tractor / combine from verified photos
    farm = [(TRACTOR_IMG, "tractor")] + ([(HARVESTER_IMG, "combine")] if combine else [])
    for img_label, cls in farm:
        cands = []
        for img, labels in verified.items():
            if img_label not in labels or img not in boxes:
                continue
            if not combine and HARVESTER_IMG in labels:
                continue                          # harvesting machinery: tractor or combine?
            bx = boxes[img]
            if any(b[0] in (CAR, TRUCK) for b in bx):
                continue                          # ambiguous: skip
            veh = [b for b in bx if b[0] in (LAND_VEHICLE, VEHICLE)]
            if not veh or len(veh) > 6:
                continue
            cands.append((img, [(cls, *b[1:]) for b in veh] +
                          [(DIRECT[b[0]], *b[1:]) for b in bx if b[0] in DIRECT]))
        rnd.shuffle(cands)
        for img, lab in cands[:per_class]:
            picked[img] = lab
    farm_imgs = {i for i, ls in verified.items() if ls & {TRACTOR_IMG, HARVESTER_IMG}}
    # car / truck / livestock from their own boxes
    for cls, mids in (("truck", {TRUCK}), ("livestock", {CATTLE, HORSE, SHEEP}), ("car", {CAR})):
        cands = []
        for img, bx in boxes.items():
            if img in picked or img in farm_imgs:
                continue
            mine = [b for b in bx if b[0] in mids]
            if not mine or len(bx) > 10:
                continue
            if any(b[0] in (LAND_VEHICLE, VEHICLE) for b in bx):
                continue                          # unnamed vehicles: could be anything
            if max((b[3] - b[1]) * (b[4] - b[2]) for b in mine) < 0.003:
                continue                          # only specks
            cands.append((img, [(DIRECT[b[0]], *b[1:]) for b in bx if b[0] in DIRECT]))
        rnd.shuffle(cands)
        for img, lab in cands[:per_class]:
            picked[img] = lab
    return picked


def fetch(img_id: str, dest: str, max_side: int) -> bool:
    if os.path.isfile(dest):
        return True
    try:
        with urllib.request.urlopen(IMG_URL.format(img_id), timeout=60) as r:
            data = r.read()
    except Exception:
        return False
    try:
        import cv2
        import numpy as np
        im = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if im is None:
            return False
        h, w = im.shape[:2]
        s = max_side / max(h, w)
        if s < 1:
            im = cv2.resize(im, (round(w * s), round(h * s)), interpolation=cv2.INTER_AREA)
        cv2.imwrite(dest, im, [cv2.IMWRITE_JPEG_QUALITY, 90])
    except ImportError:
        with open(dest, "wb") as fh:
            fh.write(data)
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=os.path.join(HERE, "datasets", "openimages"))
    ap.add_argument("--cache", default=os.path.join(HERE, "datasets", ".openimages_cache"))
    ap.add_argument("--per-class", type=int, default=400)
    ap.add_argument("--val-share", type=float, default=0.15)
    ap.add_argument("--max-side", type=int, default=640, help="resize photos to save space")
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--combine", action="store_true",
                    help="also use 'Harvester' photos as combine (noisy, see above)")
    args = ap.parse_args()

    names = silm_classes()
    idx = {n: i for i, n in enumerate(names)}
    verified, boxes = load(args.cache)
    picked = choose(verified, boxes, args.per_class, combine=args.combine)
    counts = {}
    for lab in picked.values():
        for c in {x[0] for x in lab}:
            counts[c] = counts.get(c, 0) + 1
    print(f"chose {len(picked)} photos; photos per class: {counts}")

    jobs = []
    for img, lab in picked.items():
        split = "val" if int(hashlib.sha1(img.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF < args.val_share else "train"
        for sub in ("images", "labels"):
            os.makedirs(os.path.join(args.out, sub, split), exist_ok=True)
        jobs.append((img, lab, split))
    ok = 0
    with cf.ThreadPoolExecutor(args.workers) as ex:
        futs = {ex.submit(fetch, img, os.path.join(args.out, "images", split, img + ".jpg"),
                          args.max_side): (img, lab, split) for img, lab, split in jobs}
        for n, f in enumerate(cf.as_completed(futs), 1):
            img, lab, split = futs[f]
            if f.result():
                ok += 1
                with open(os.path.join(args.out, "labels", split, img + ".txt"), "w") as fh:
                    for c, x0, y0, x1, y1 in lab:
                        fh.write(f"{idx[c]} {(x0 + x1) / 2:.6f} {(y0 + y1) / 2:.6f} "
                                 f"{x1 - x0:.6f} {y1 - y0:.6f}\n")
            if n % 100 == 0:
                print(f"  downloaded {n}/{len(jobs)}")
    data = {"path": os.path.abspath(args.out), "train": "images/train", "val": "images/val",
            "names": {i: n for i, n in enumerate(names)}}
    with open(os.path.join(args.out, "data.yaml"), "w") as fh:
        yaml.safe_dump(data, fh, sort_keys=False)
    with open(os.path.join(args.out, "SOURCE.txt"), "w") as fh:
        fh.write("Photos and boxes from Open Images V6/V7 (https://storage.googleapis.com/openimages/"
                 "web/index.html). Annotations CC BY 4.0; photos mostly CC BY 2.0 - see Open Images' "
                 "image list for each photo's licence and author.\n")
    print(f"{ok}/{len(jobs)} photos downloaded -> {args.out}/data.yaml")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
