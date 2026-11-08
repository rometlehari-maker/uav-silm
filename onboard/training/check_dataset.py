#!/usr/bin/env python3
"""Check a YOLO dataset before training, and preview its labels.

    python3 check_dataset.py datasets/merged/data.yaml --preview 16

Reports, per split and per class, how many boxes and images there are, and
flags problems that otherwise cost you a whole training run:

  ERRORS (exit code 1)
    * label lines that aren't "class xc yc w h"
    * class indices outside the class list
    * boxes outside the image or with zero/negative size
  WARNINGS
    * boxes too small to learn at the training image size (--imgsz)
    * duplicate boxes, and the same photo in both train and val/test (leakage)
    * classes with no validation boxes (their accuracy can't be measured)
    * classes with very few training boxes

--preview N draws the boxes and class names onto N images (favouring rare
classes) in <dataset>/preview/ so you can SEE whether the labels are right.
Wrong boxes are the most common reason a new class doesn't learn.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import random
import sys
from collections import Counter, defaultdict

import yaml

IMG_EXT = (".jpg", ".jpeg", ".png", ".bmp", ".webp")


def label_path_for(img: str) -> str:
    parts = img.split(os.sep)
    stem = os.path.splitext(parts[-1])[0] + ".txt"
    for i in range(len(parts) - 2, -1, -1):
        if parts[i] == "images":
            return os.sep.join(parts[:i] + ["labels"] + parts[i + 1:-1] + [stem])
    return os.path.join(os.path.dirname(img), stem)


def split_images(data_yaml: str) -> tuple[list[str], dict[str, list[str]]]:
    with open(data_yaml) as fh:
        d = yaml.safe_load(fh)
    names = d["names"]
    names = [names[i] for i in sorted(names)] if isinstance(names, dict) else list(names)
    base = d.get("path") or os.path.dirname(os.path.abspath(data_yaml))
    if not os.path.isabs(base):
        base = os.path.join(os.path.dirname(os.path.abspath(data_yaml)), base)
    splits = {}
    for s in ("train", "val", "test"):
        entries = d.get(s)
        if not entries:
            continue
        entries = entries if isinstance(entries, list) else [entries]
        imgs = []
        for e in entries:
            p = e if os.path.isabs(e) else os.path.normpath(os.path.join(base, e))
            if os.path.isdir(p):
                for dp, _, fs in os.walk(p):
                    imgs += [os.path.join(dp, f) for f in sorted(fs) if f.lower().endswith(IMG_EXT)]
            elif os.path.isfile(p) and p.endswith(".txt"):  # list file
                imgs += [ln.strip() for ln in open(p) if ln.strip()]
        splits[s] = imgs
    return names, splits


def check(data_yaml: str, imgsz: int = 640, min_px: float = 3.0, few: int = 50):
    names, splits = split_images(data_yaml)
    nc = len(names)
    errors, warnings = [], []
    boxes = {s: Counter() for s in splits}
    images_with = {s: Counter() for s in splits}
    background = Counter()
    per_image = {}
    hashes = defaultdict(set)

    for split, imgs in splits.items():
        for img in imgs:
            try:
                with open(img, "rb") as fh:
                    hashes[hashlib.md5(fh.read()).hexdigest()].add((split, img))
            except OSError:
                errors.append(f"{img}: cannot read image file")
                continue
            lbl = label_path_for(img)
            rows = []
            if os.path.exists(lbl):
                seen = set()
                for n, line in enumerate(open(lbl), 1):
                    v = line.split()
                    if not v:
                        continue
                    where = f"{lbl}:{n}"
                    if len(v) != 5:
                        errors.append(f"{where}: expected 5 values, got {len(v)}")
                        continue
                    try:
                        c = int(v[0])
                        x, y, w, h = map(float, v[1:])
                    except ValueError:
                        errors.append(f"{where}: not numbers: {line.strip()!r}")
                        continue
                    if not 0 <= c < nc:
                        errors.append(f"{where}: class {c} but only {nc} classes (0..{nc - 1})")
                        continue
                    if w <= 0 or h <= 0:
                        errors.append(f"{where}: box has zero or negative size")
                        continue
                    if (x - w / 2 < -1e-3 or y - h / 2 < -1e-3 or
                            x + w / 2 > 1 + 1e-3 or y + h / 2 > 1 + 1e-3):
                        errors.append(f"{where}: box extends outside the image")
                        continue
                    if w * imgsz < min_px or h * imgsz < min_px:
                        warnings.append(f"{where}: {names[c]} box is {w * imgsz:.1f}x{h * imgsz:.1f} px "
                                        f"at imgsz {imgsz} - too small to learn")
                    key = (c, round(x, 4), round(y, 4), round(w, 4), round(h, 4))
                    if key in seen:
                        warnings.append(f"{where}: duplicate box")
                    seen.add(key)
                    rows.append((c, x, y, w, h))
            if not rows:
                background[split] += 1
            for c in {r[0] for r in rows}:
                images_with[split][c] += 1
            for r in rows:
                boxes[split][r[0]] += 1
            per_image[img] = rows

    for h, where in hashes.items():
        sp = {s for s, _ in where}
        if "train" in sp and len(sp) > 1:
            files = ", ".join(sorted(os.path.basename(p) for _, p in where))
            warnings.append(f"same image in {'/'.join(sorted(sp))}: {files} (leakage)")

    for i, n in enumerate(names):
        tr = boxes.get("train", Counter())[i]
        va = boxes.get("val", Counter())[i]
        if tr == 0 and va == 0:
            continue  # class declared but not in this dataset
        if "val" in splits and va == 0:
            warnings.append(f"class {n!r}: no validation boxes - its accuracy can't be measured")
        if 0 < tr < few:
            warnings.append(f"class {n!r}: only {tr} training boxes - expect weak results "
                            f"(aim for hundreds)")
        if tr == 0 and va > 0:
            warnings.append(f"class {n!r}: in validation but never in training")

    return dict(names=names, splits={s: len(v) for s, v in splits.items()}, boxes=boxes,
                images_with=images_with, background=background, errors=errors,
                warnings=warnings, per_image=per_image)


def preview(res, out_dir: str, n: int, seed: int = 0) -> int:
    import cv2
    rng = random.Random(seed)
    total = Counter()
    for c in res["boxes"].values():
        total.update(c)
    imgs = [p for p, rows in res["per_image"].items() if rows]
    # favour images containing the rarest classes
    imgs.sort(key=lambda p: min(total[r[0]] for r in res["per_image"][p]))
    pick = imgs[: n // 2] + rng.sample(imgs[n // 2:], min(len(imgs[n // 2:]), n - n // 2))
    os.makedirs(out_dir, exist_ok=True)
    names = res["names"]
    for p in pick:
        im = cv2.imread(p)
        if im is None:
            continue
        H, W = im.shape[:2]
        for c, x, y, w, h in res["per_image"][p]:
            p0 = (int((x - w / 2) * W), int((y - h / 2) * H))
            p1 = (int((x + w / 2) * W), int((y + h / 2) * H))
            cv2.rectangle(im, p0, p1, (0, 255, 255), 2)
            cv2.putText(im, names[c], (p0[0], max(12, p0[1] - 4)), cv2.FONT_HERSHEY_SIMPLEX,
                        0.5, (0, 255, 255), 1, cv2.LINE_AA)
        cv2.imwrite(os.path.join(out_dir, os.path.basename(p)), im)
    return len(pick)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("data_yaml")
    ap.add_argument("--imgsz", type=int, default=640, help="the size you will train at")
    ap.add_argument("--preview", type=int, default=0, metavar="N")
    ap.add_argument("--preview-dir", default=None)
    ap.add_argument("--max-messages", type=int, default=25)
    args = ap.parse_args(argv)

    res = check(args.data_yaml, imgsz=args.imgsz)
    names, splits = res["names"], res["splits"]
    print(f"{args.data_yaml}: {len(names)} classes, " +
          ", ".join(f"{s} {n} images ({res['background'][s]} background)" for s, n in splits.items()))
    cols = [s for s in ("train", "val", "test") if s in splits]
    print(f"\n  {'class':16s}" + "".join(f" {s + ' boxes':>12s} {'imgs':>6s}" for s in cols))
    for i, n in enumerate(names):
        print(f"  {n:16s}" + "".join(f" {res['boxes'][s][i]:12d} {res['images_with'][s][i]:6d}" for s in cols))

    for title, msgs in (("ERRORS", res["errors"]), ("WARNINGS", res["warnings"])):
        if msgs:
            print(f"\n{title} ({len(msgs)}):")
            for m in msgs[: args.max_messages]:
                print("  " + m)
            if len(msgs) > args.max_messages:
                print(f"  ... and {len(msgs) - args.max_messages} more")

    if args.preview:
        d = args.preview_dir or os.path.join(os.path.dirname(os.path.abspath(args.data_yaml)), "preview")
        k = preview(res, d, args.preview)
        print(f"\npreview: {k} images with boxes drawn -> {d}")
    print("\nOK to train" if not res["errors"] else "\nFix the errors before training")
    return 1 if res["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
