#!/usr/bin/env python3
"""Merge labelled datasets into one training set, remapping classes BY NAME.

When you add an object, its labels usually come from somewhere new: your own
flights labelled in a labelling tool, a Roboflow export, a public dataset. Each
of those numbers its classes differently ("deer" may be class 0 there and class
15 in training/classes.yaml). This tool reads each source's own class names,
maps them onto classes.yaml by name, rewrites every label file with the right
index, and writes one dataset ready for train.py.

    python3 merge_datasets.py --out datasets/merged \\
        datasets/synth  datasets/my_flights  datasets/roboflow_deer

Accepted sources (any mix):
  * a YOLO dataset folder (or its data.yaml): images/{train,val} + labels/...,
    or Roboflow-style {train,valid,test}/images + .../labels
  * a "flat" labelling-tool folder: images with a .txt beside each and a
    classes.txt. Subfolders are fine (see splitting below), each may have its
    own classes.txt: an image uses the nearest one above it.

Class names are matched case-insensitively, with spaces and hyphens treated as
underscores ("Combine-Harvester" == "combine_harvester"). Names that still
don't match stop the merge and are listed, so nothing is silently mislabelled:
  --alias "Combine Harvester=combine"   map a source name onto one of yours
  --drop-unknown                        drop boxes of unmatched classes
                                        (their images become background)

Splitting: images in a source's train/val/test folders keep that split, so to
hold a fully labelled flight out as a TEST set, put it under a folder named
`test` (e.g. to_label/test/flight_1120/).
Images with no split folder are assigned to val by --val-frac, per SUBFOLDER:
put each flight's frames in its own subfolder so a whole flight lands in train
or val. Consecutive frames from one flight are nearly identical; splitting them
across train and val makes the validation score meaningless. The assignment is
deterministic, so re-running after adding images doesn't reshuffle old ones.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
from collections import defaultdict

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
IMG_EXT = (".jpg", ".jpeg", ".png", ".bmp", ".webp")
SPLIT_WORDS = {"train": "train", "training": "train", "val": "val", "valid": "val",
               "validation": "val", "test": "test", "testing": "test"}


def norm(name: str) -> str:
    return str(name).strip().lower().replace(" ", "_").replace("-", "_")


def load_target_classes(path: str | None) -> list[str]:
    with open(path or os.path.join(HERE, "classes.yaml")) as fh:
        names = yaml.safe_load(fh)["names"]
    return [names[i] for i in sorted(names)] if isinstance(names, dict) else list(names)


def label_path_for(img: str) -> str:
    """Ultralytics' rule: .../images/... -> .../labels/..., else beside the image."""
    parts = img.split(os.sep)
    stem = os.path.splitext(parts[-1])[0] + ".txt"
    for i in range(len(parts) - 2, -1, -1):
        if parts[i] == "images":
            return os.sep.join(parts[:i] + ["labels"] + parts[i + 1:-1] + [stem])
    return os.path.join(os.path.dirname(img), stem)


def yaml_names(yaml_path: str | None):
    if not (yaml_path and os.path.exists(yaml_path)):
        return None
    with open(yaml_path) as fh:
        d = yaml.safe_load(fh) or {}
    names = d.get("names")
    if isinstance(names, dict):
        return tuple(names[i] for i in sorted(names))
    if isinstance(names, list):
        return tuple(names)
    return None


def discover(src: str):
    """Return (root, items); items = (img, label_or_None, split_or_None, group, names).

    `names` is the class list that image's label indices refer to: the
    classes.txt in the image's folder or the nearest folder above it (labelling
    tools often export each flight with its own), otherwise the source's
    data.yaml."""
    if os.path.isfile(src):
        root, yaml_path = os.path.dirname(os.path.abspath(src)), src
    else:
        root = os.path.abspath(src)
        yaml_path = next((os.path.join(root, f) for f in ("data.yaml", "dataset.yaml")
                          if os.path.exists(os.path.join(root, f))), None)
    root_names = yaml_names(yaml_path)
    cache: dict[str, tuple | None] = {}

    def names_for(d: str):
        if d in cache:
            return cache[d]
        ct = os.path.join(d, "classes.txt")
        if os.path.exists(ct):
            res = tuple(ln.strip() for ln in open(ct) if ln.strip())
        elif os.path.normpath(d) == os.path.normpath(root) or os.path.dirname(d) == d:
            res = root_names
        else:
            res = names_for(os.path.dirname(d))
        cache[d] = res
        return res

    items, missing = [], set()
    for dirpath, dirnames, files in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d != "runs")
        for f in sorted(files):
            if not f.lower().endswith(IMG_EXT):
                continue
            img = os.path.join(dirpath, f)
            rel_parts = os.path.relpath(img, root).split(os.sep)
            split = next((SPLIT_WORDS[p.lower()] for p in rel_parts[:-1]
                          if p.lower() in SPLIT_WORDS), None)
            lbl = label_path_for(img)
            lbl = lbl if os.path.exists(lbl) else None
            names = names_for(dirpath)
            if lbl and names is None:
                missing.add(os.path.relpath(dirpath, root))
            group = os.path.dirname(os.path.relpath(img, root)) or f  # subfolder, else per image
            items.append((img, lbl, split, group, names))
    if missing:
        raise SystemExit(f"{root}: no classes.txt or data.yaml names for labels in "
                         f"{sorted(missing)} - can't tell which class each label index means")
    return root, items


def parse_label(path: str):
    """Yield (cls, xc, yc, w, h) or raise ValueError. Accepts polygon lines
    (cls x1 y1 x2 y2 ...) from segmentation exports by taking their bounds."""
    out = []
    for n, line in enumerate(open(path), 1):
        vals = line.split()
        if not vals:
            continue
        if len(vals) == 5:
            c, x, y, w, h = int(float(vals[0])), *map(float, vals[1:])
        elif len(vals) >= 7 and len(vals) % 2 == 1:
            c = int(float(vals[0]))
            xs, ys = list(map(float, vals[1::2])), list(map(float, vals[2::2]))
            x, y = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
            w, h = max(xs) - min(xs), max(ys) - min(ys)
        else:
            raise ValueError(f"{path}:{n}: expected 'class xc yc w h', got {line.strip()!r}")
        out.append((c, x, y, w, h))
    return out


def in_val(seed: int, src_key: str, group: str, frac: float) -> bool:
    h = hashlib.sha1(f"{seed}|{src_key}|{group}".encode()).hexdigest()
    return int(h[:8], 16) / 0xFFFFFFFF < frac


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sources", nargs="+")
    ap.add_argument("--out", required=True)
    ap.add_argument("--classes", default=None, help="target class list (default training/classes.yaml)")
    ap.add_argument("--alias", action="append", default=[], metavar="SOURCE_NAME=TARGET_NAME")
    ap.add_argument("--drop-unknown", action="store_true")
    ap.add_argument("--val-frac", type=float, default=0.15)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--link", action="store_true", help="hard-link images instead of copying")
    ap.add_argument("--overwrite", action="store_true", help="replace an existing --out dataset")
    args = ap.parse_args(argv)

    target = load_target_classes(args.classes)
    tindex = {norm(n): i for i, n in enumerate(target)}
    aliases = {}
    for a in args.alias:
        if "=" not in a:
            raise SystemExit(f"--alias needs SOURCE=TARGET, got {a!r}")
        s_name, t_name = a.split("=", 1)
        if norm(t_name) not in tindex:
            raise SystemExit(f"--alias target {t_name!r} is not in the class list {target}")
        aliases[norm(s_name)] = tindex[norm(t_name)]

    out = os.path.abspath(args.out)
    if os.path.exists(os.path.join(out, "data.yaml")) or os.path.isdir(os.path.join(out, "images")):
        if not args.overwrite:
            raise SystemExit(f"{out} already holds a dataset; pass --overwrite to replace it")
        for sub in ("images", "labels"):
            if os.path.isdir(os.path.join(out, sub)):
                shutil.rmtree(os.path.join(out, sub))

    # 1) discover every source and build its class mapping
    sources, unknown = [], defaultdict(set)
    used_keys: set[str] = set()
    for src in args.sources:
        root, items = discover(src)
        key = os.path.basename(root.rstrip(os.sep)) or "src"
        base, k = key, 2
        while key in used_keys:
            key, k = f"{base}{k}", k + 1
        used_keys.add(key)
        mappings = {}  # one mapping per distinct class list in this source
        for names in {it[4] for it in items if it[4] is not None}:
            mapping = {}
            for i, n in enumerate(names):
                t = aliases.get(norm(n), tindex.get(norm(n)))
                mapping[i] = t
                if t is None:
                    unknown[n].add(key)
            mappings[names] = mapping
        sources.append((key, root, items, mappings))
        lists = sorted(mappings, key=len)
        desc = (f"classes {list(lists[0])}" if len(lists) == 1 else
                f"{len(lists)} class lists: " + "; ".join(str(list(n)) for n in lists))
        print(f"source {key}: {len(items)} images, {desc if lists else 'no labels'}")

    if unknown and not args.drop_unknown:
        print("\nThese source class names don't match training/classes.yaml:")
        for n, where in sorted(unknown.items()):
            print(f"  {n!r}  (in {', '.join(sorted(where))})")
        print("Fix by: adding the class to classes.yaml, mapping it with "
              "--alias 'NAME=your_class', or --drop-unknown to ignore those boxes.")
        return 2

    # 2) copy images, rewrite labels
    counts = {s: defaultdict(int) for s in ("train", "val", "test")}
    n_imgs = defaultdict(int)
    n_bg = dropped = 0
    errors = []
    for key, root, items, mappings in sources:
        for img, lbl, split, group, names in items:
            split = split or ("val" if in_val(args.seed, key, group, args.val_frac) else "train")
            rows = []
            mapping = mappings.get(names, {})
            if lbl:
                try:
                    for c, x, y, w, h in parse_label(lbl):
                        if c not in mapping:
                            raise ValueError(f"{lbl}: class index {c} but its class list "
                                             f"declares only {len(names)} classes")
                        t = mapping[c]
                        if t is None:
                            dropped += 1
                            continue
                        rows.append(f"{t} {x:.6f} {y:.6f} {w:.6f} {h:.6f}")
                        counts[split][t] += 1
                except ValueError as exc:
                    errors.append(str(exc))
                    continue
            if not rows:
                n_bg += 1
            rel = os.path.relpath(img, root).replace(os.sep, "__")
            stem = f"{key}__{os.path.splitext(rel)[0]}"
            ext = os.path.splitext(img)[1].lower()
            di = os.path.join(out, "images", split)
            dl = os.path.join(out, "labels", split)
            os.makedirs(di, exist_ok=True)
            os.makedirs(dl, exist_ok=True)
            dst = os.path.join(di, stem + ext)
            if args.link:
                try:
                    os.link(img, dst)
                except OSError:
                    shutil.copy2(img, dst)
            else:
                shutil.copy2(img, dst)
            with open(os.path.join(dl, stem + ".txt"), "w") as fh:
                fh.write("\n".join(rows) + ("\n" if rows else ""))
            n_imgs[split] += 1

    if errors:
        print(f"\n{len(errors)} label file(s) could not be read and were skipped:")
        for e in errors[:20]:
            print("  " + e)

    data = {"path": out, "train": "images/train", "val": "images/val"}
    if n_imgs["test"]:
        data["test"] = "images/test"
    data["names"] = {i: n for i, n in enumerate(target)}
    with open(os.path.join(out, "data.yaml"), "w") as fh:
        yaml.safe_dump(data, fh, sort_keys=False)

    lines = [f"merged {sum(n_imgs.values())} images -> {out}",
             f"  train {n_imgs['train']}, val {n_imgs['val']}, test {n_imgs['test']}, "
             f"background (no boxes) {n_bg}, dropped boxes {dropped}",
             "", f"  {'class':16s} {'train':>7s} {'val':>6s} {'test':>6s}"]
    for i, n in enumerate(target):
        lines.append(f"  {n:16s} {counts['train'][i]:7d} {counts['val'][i]:6d} {counts['test'][i]:6d}")
    report = "\n".join(lines)
    print("\n" + report)
    with open(os.path.join(out, "merge_report.txt"), "w") as fh:
        fh.write(report + "\n")
    print(f"\nnext: python3 check_dataset.py {os.path.join(out, 'data.yaml')} --preview 16")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
