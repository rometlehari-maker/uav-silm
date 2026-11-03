#!/usr/bin/env python3
"""Fetch REAL labelled images to train a field-usable detector.

A model is only as real-world-capable as its data. This pulls real images for
the SILM classes from two sources and writes them in YOLO format mapped onto
`classes.yaml`:

  --source roboflow   (BEST for aerial): download a Roboflow Universe dataset
                      you've chosen, using your free API key. Roboflow Universe
                      has many *drone/aerial* agriculture + vehicle + wildfire
                      datasets, which match your camera's viewpoint far better
                      than ground-level photos. This is the recommended path.

  --source openimages : download real (mostly ground-level) images for the
                      overlapping classes via FiftyOne/Open Images V7. Useful to
                      bulk up classes like car/truck/tractor/cattle, but note the
                      VIEWPOINT GAP: ground-level photos are not top-down aerial,
                      so always mix in real aerial data before trusting it.

Examples:
    # Roboflow (pick a dataset on universe.roboflow.com, copy its slug/version)
    python3 fetch_real_data.py --source roboflow \
        --rf-api-key $ROBOFLOW_KEY \
        --rf-workspace <ws> --rf-project <proj> --rf-version 3 \
        --out datasets/real

    # Open Images overlapping classes
    python3 fetch_real_data.py --source openimages \
        --oi-classes Car Truck Tractor Cattle Sheep --max 400 --out datasets/oi

Then merge/point a data.yaml at the result and run train.py.
"""

from __future__ import annotations

import argparse
import os

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))

# Map Open Images class names -> our class indices in classes.yaml.
OI_TO_SILM = {
    "Person": "person",
    "Car": "car",
    "Truck": "truck",
    "Van": "truck",
    "Tractor": "tractor",
    "Cattle": "livestock",
    "Sheep": "livestock",
    "Barn": "barn",
    "Silo": "silo",
}


def load_names() -> dict[str, int]:
    with open(os.path.join(HERE, "classes.yaml")) as fh:
        names = yaml.safe_load(fh)["names"]
    return {v: k for k, v in names.items()}


def fetch_roboflow(args) -> int:
    try:
        from roboflow import Roboflow
    except Exception:
        print("Install the client first:  python3 -m pip install roboflow")
        return 1
    rf = Roboflow(api_key=args.rf_api_key)
    project = rf.workspace(args.rf_workspace).project(args.rf_project)
    version = project.version(int(args.rf_version))
    print(f"downloading Roboflow {args.rf_workspace}/{args.rf_project} v{args.rf_version} ...")
    # yolov11 == yolov8 label format (YOLO txt). Exports images+labels+data.yaml.
    dataset = version.download("yolov11", location=os.path.abspath(args.out))
    print(f"downloaded to {dataset.location}")
    print("IMPORTANT: check the downloaded data.yaml class names/order and remap")
    print("them to training/classes.yaml before merging with other data.")
    return 0


def fetch_openimages(args) -> int:
    try:
        import fiftyone as fo
        import fiftyone.zoo as foz
    except Exception:
        print("Install FiftyOne first:  python3 -m pip install fiftyone")
        return 1

    name2idx = load_names()
    oi_classes = args.oi_classes or list(OI_TO_SILM.keys())
    unknown = [c for c in oi_classes if c not in OI_TO_SILM]
    if unknown:
        print(f"note: no SILM mapping for {unknown}; they will be skipped on export")

    print(f"downloading Open Images V7 detections for {oi_classes} (max {args.max}/split)")
    out = os.path.abspath(args.out)
    for split in ("train", "validation"):
        ds = foz.load_zoo_dataset(
            "open-images-v7", split=split, label_types=["detections"],
            classes=oi_classes, max_samples=args.max, dataset_name=f"oi_{split}_{os.getpid()}",
        )
        # Export YOLO; FiftyOne writes images/ + labels/ + a dataset.yaml.
        yolo_split = "train" if split == "train" else "val"
        ds.export(
            export_dir=out,
            dataset_type=fo.types.YOLOv5Dataset,
            label_field="ground_truth",
            split=yolo_split,
            classes=[c for c in oi_classes],
        )
        ds.delete()

    print(f"exported to {out}")
    print("Remap the exported class indices to training/classes.yaml. The mapping")
    print("this tool assumes is:")
    for k, v in OI_TO_SILM.items():
        if k in oi_classes:
            print(f"  {k:10s} -> {v} (idx {name2idx.get(v, '?')})")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, choices=["roboflow", "openimages"])
    ap.add_argument("--out", default=os.path.join(HERE, "datasets", "real"))
    # roboflow
    ap.add_argument("--rf-api-key", default=os.environ.get("ROBOFLOW_KEY"))
    ap.add_argument("--rf-workspace")
    ap.add_argument("--rf-project")
    ap.add_argument("--rf-version")
    # openimages
    ap.add_argument("--oi-classes", nargs="*")
    ap.add_argument("--max", type=int, default=300)
    args = ap.parse_args()

    if args.source == "roboflow":
        if not (args.rf_api_key and args.rf_workspace and args.rf_project and args.rf_version):
            print("roboflow needs --rf-api-key --rf-workspace --rf-project --rf-version")
            return 2
        return fetch_roboflow(args)
    return fetch_openimages(args)


if __name__ == "__main__":
    raise SystemExit(main())
