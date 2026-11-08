"""Tests for the add-a-new-object tooling: make_synthetic layouts,
merge_datasets (remapping by name), check_dataset, prelabel, export labels,
and per-class detector thresholds."""

import os
import subprocess
import sys

import numpy as np
import pytest
import yaml

cv2 = pytest.importorskip("cv2")

TRAIN = os.path.join(os.path.dirname(__file__), "..", "training")
sys.path.insert(0, TRAIN)
import check_dataset  # noqa: E402
import merge_datasets  # noqa: E402
import prelabel  # noqa: E402


def run(script, *args):
    return subprocess.run([sys.executable, os.path.join(TRAIN, script), *map(str, args)],
                          capture_output=True, text=True)


@pytest.fixture
def classes(tmp_path):
    p = tmp_path / "classes.yaml"
    p.write_text(yaml.safe_dump({"names": {0: "tractor", 1: "combine", 2: "fire", 3: "deer"}}))
    return p


def write_img(path, w=64, h=48):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cv2.imwrite(str(path), np.random.default_rng(abs(hash(str(path))) % 2**32)
                .integers(0, 255, (h, w, 3), dtype=np.uint8))


def flat_folder(root, names, files):
    """files: {relpath: ["cls xc yc w h", ...]} written beside images."""
    os.makedirs(root, exist_ok=True)
    (root / "classes.txt").write_text("\n".join(names) + "\n")
    for rel, rows in files.items():
        write_img(root / rel)
        (root / rel).with_suffix(".txt").write_text("\n".join(rows) + ("\n" if rows else ""))


def read_rows(out, split):
    rows = {}
    d = out / "labels" / split
    for f in sorted(os.listdir(d)) if d.exists() else []:
        rows[f] = [ln.split() for ln in open(d / f) if ln.strip()]
    return rows


# --------------------------------------------------------------- synthetic --

def test_make_synthetic_flat_and_yolo_layouts(tmp_path, classes):
    r = run("make_synthetic.py", "--classes", classes, "--only", "deer", "--layout", "flat",
            "--n-train", 4, "--width", 160, "--height", 120, "--out", tmp_path / "flat")
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "flat" / "classes.txt").read_text().split() == ["deer"]
    rows = [ln.split() for ln in open(tmp_path / "flat" / "img_00000.txt")]
    assert rows and all(r_[0] == "0" for r_ in rows)          # deer is index 0 HERE
    r = run("make_synthetic.py", "--classes", classes, "--n-train", 3, "--n-val", 2,
            "--width", 160, "--height", 120, "--out", tmp_path / "yolo")
    assert r.returncode == 0
    d = yaml.safe_load(open(tmp_path / "yolo" / "data.yaml"))
    assert d["names"][3] == "deer"
    assert run("make_synthetic.py", "--classes", classes, "--only", "unicorn", "--out",
               tmp_path / "x").returncode == 2


def test_synthetic_boxes_enclose_rotated_shapes(tmp_path, classes):
    run("make_synthetic.py", "--classes", classes, "--only", "tractor", "--layout", "flat",
        "--n-train", 6, "--width", 200, "--height", 200, "--out", tmp_path / "f")
    for i in range(6):
        img = cv2.imread(str(tmp_path / "f" / f"img_{i:05d}.jpg"))
        bg = np.median(img.reshape(-1, 3), axis=0)
        mask = (np.abs(img.astype(int) - bg).sum(axis=2) > 120).astype(np.uint8)
        for ln in open(tmp_path / "f" / f"img_{i:05d}.txt"):
            _, x, y, w, h = map(float, ln.split())
            x0, y0 = int((x - w / 2) * 200), int((y - h / 2) * 200)
            x1, y1 = int((x + w / 2) * 200), int((y + h / 2) * 200)
            mask[max(0, y0 - 2):y1 + 3, max(0, x0 - 2):x1 + 3] = 0
        assert mask.sum() < 25, "object pixels found outside every label box"


# ------------------------------------------------------------------ merge --

def test_merge_remaps_by_name_and_normalises(tmp_path, classes):
    flat_folder(tmp_path / "a", ["Deer", "Fire"], {"x1.jpg": ["0 .5 .5 .2 .2", "1 .3 .3 .1 .1"]})
    flat_folder(tmp_path / "b", ["combine-harvester"], {"y1.jpg": ["0 .5 .5 .2 .2"]})
    out = tmp_path / "m"
    rc = merge_datasets.main([str(tmp_path / "a"), str(tmp_path / "b"), "--out", str(out),
                              "--classes", str(classes), "--alias", "combine harvester=combine",
                              "--val-frac", "0"])
    assert rc == 0
    rows = read_rows(out, "train")
    assert rows["a__x1.txt"] == [["3", "0.500000", "0.500000", "0.200000", "0.200000"],
                                 ["2", "0.300000", "0.300000", "0.100000", "0.100000"]]
    assert rows["b__y1.txt"][0][0] == "1"
    d = yaml.safe_load(open(out / "data.yaml"))
    assert d["names"] == {0: "tractor", 1: "combine", 2: "fire", 3: "deer"}


def test_merge_refuses_unknown_names_unless_dropped(tmp_path, classes, capsys):
    flat_folder(tmp_path / "a", ["deer", "wild_boar"], {"x.jpg": ["1 .5 .5 .2 .2"], "y.jpg": ["0 .5 .5 .1 .1"]})
    out = tmp_path / "m"
    assert merge_datasets.main([str(tmp_path / "a"), "--out", str(out), "--classes", str(classes)]) == 2
    assert "wild_boar" in capsys.readouterr().out
    assert not (out / "data.yaml").exists()
    assert merge_datasets.main([str(tmp_path / "a"), "--out", str(out), "--classes", str(classes),
                                "--drop-unknown", "--val-frac", "0"]) == 0
    rows = read_rows(out, "train")
    assert rows["a__x.txt"] == []          # became a background image
    assert rows["a__y.txt"][0][0] == "3"


def test_merge_roboflow_layout_polygons_and_splits(tmp_path, classes):
    rf = tmp_path / "rf"
    (rf).mkdir()
    (rf / "data.yaml").write_text(yaml.safe_dump({"names": ["fire", "tractor"], "nc": 2,
                                                  "train": "../train/images", "val": "../valid/images"}))
    for split in ("train", "valid", "test"):
        write_img(rf / split / "images" / f"{split}1.jpg")
        (rf / split / "labels").mkdir(parents=True)
        (rf / split / "labels" / f"{split}1.txt").write_text("1 0.1 0.1 0.3 0.1 0.3 0.4 0.1 0.4\n")
    out = tmp_path / "m"
    assert merge_datasets.main([str(rf), "--out", str(out), "--classes", str(classes)]) == 0
    for split in ("train", "val", "test"):
        rows = read_rows(out, split)
        assert len(rows) == 1
        c, x, y, w, h = list(rows.values())[0][0]
        assert c == "0" and float(x) == pytest.approx(0.2) and float(h) == pytest.approx(0.3)
    assert yaml.safe_load(open(out / "data.yaml"))["test"] == "images/test"


def test_merge_keeps_flights_together_and_split_is_stable(tmp_path, classes):
    files = {f"flight_{f}/f{i}.jpg": ["0 .5 .5 .1 .1"] for f in range(12) for i in range(5)}
    flat_folder(tmp_path / "mine", ["deer"], files)
    out = tmp_path / "m"
    assert merge_datasets.main([str(tmp_path / "mine"), "--out", str(out), "--classes", str(classes),
                                "--val-frac", "0.3"]) == 0
    val = set(read_rows(out, "val"))
    flights_in_val = {n.split("__")[1] for n in val}
    for fl in flights_in_val:   # whole flights only
        assert {f"mine__{fl}__f{i}.txt" for i in range(5)} <= val
    assert 0 < len(flights_in_val) < 12
    # add a new flight and re-merge: old assignments unchanged
    flat_folder(tmp_path / "mine", ["deer"], {"flight_99/a.jpg": ["0 .5 .5 .1 .1"]})
    merge_datasets.main([str(tmp_path / "mine"), "--out", str(out), "--classes", str(classes),
                         "--val-frac", "0.3", "--overwrite"])
    assert {n for n in read_rows(out, "val") if "flight_99" not in n} == val


def test_merge_will_not_overwrite_by_accident(tmp_path, classes):
    flat_folder(tmp_path / "a", ["deer"], {"x.jpg": ["0 .5 .5 .2 .2"]})
    out = tmp_path / "m"
    merge_datasets.main([str(tmp_path / "a"), "--out", str(out), "--classes", str(classes)])
    with pytest.raises(SystemExit):
        merge_datasets.main([str(tmp_path / "a"), "--out", str(out), "--classes", str(classes)])


# ------------------------------------------------------------------ check --

def make_yolo(root, names, split_files):
    (root).mkdir(parents=True, exist_ok=True)
    (root / "data.yaml").write_text(yaml.safe_dump({"path": str(root), "train": "images/train",
                                                    "val": "images/val",
                                                    "names": dict(enumerate(names))}))
    for split, files in split_files.items():
        for name, rows in files.items():
            write_img(root / "images" / split / name)
            lp = root / "labels" / split / (os.path.splitext(name)[0] + ".txt")
            lp.parent.mkdir(parents=True, exist_ok=True)
            lp.write_text("\n".join(rows) + "\n")


def test_check_dataset_clean(tmp_path):
    make_yolo(tmp_path / "d", ["a", "b"], {"train": {"t1.jpg": ["0 .5 .5 .2 .2", "1 .2 .2 .1 .1"]},
                                           "val": {"v1.jpg": ["0 .5 .5 .2 .2", "1 .5 .5 .1 .1"]}})
    res = check_dataset.check(str(tmp_path / "d" / "data.yaml"))
    assert res["errors"] == []
    assert res["boxes"]["train"][0] == 1 and res["boxes"]["val"][1] == 1


def test_check_dataset_catches_problems(tmp_path):
    make_yolo(tmp_path / "d", ["a", "b"], {
        "train": {"t1.jpg": ["5 .5 .5 .2 .2",          # bad class
                             "0 .95 .5 .2 .2",         # outside image
                             "0 .5 .5 .2",             # malformed
                             "0 .5 .5 .002 .002",      # tiny
                             "1 .4 .4 .1 .1", "1 .4 .4 .1 .1"]},   # duplicate
        "val": {"v1.jpg": ["0 .5 .5 .3 .3"]}})
    # leakage: identical image in train and val
    import shutil
    shutil.copy(tmp_path / "d" / "images" / "train" / "t1.jpg", tmp_path / "d" / "images" / "val" / "copy.jpg")
    res = check_dataset.check(str(tmp_path / "d" / "data.yaml"), imgsz=640)
    e, w = " ".join(res["errors"]), " ".join(res["warnings"])
    assert "class 5" in e and "outside the image" in e and "expected 5 values" in e
    assert "too small" in w and "duplicate box" in w and "leakage" in w
    assert "'b': no validation boxes" in w
    assert check_dataset.main([str(tmp_path / "d" / "data.yaml"), "--preview", "2"]) == 1
    assert len(os.listdir(tmp_path / "d" / "preview")) >= 1


# --------------------------------------------------------------- prelabel --

def test_prelabel_selection_and_output_feed_merge(tmp_path, classes):
    imgs = tmp_path / "frames"
    for i in range(4):
        write_img(imgs / f"f{i}.jpg")
    preds = {str(imgs / "f0.jpg"): [(0, 0.95, .5, .5, .1, .1)],
             str(imgs / "f1.jpg"): [(0, 0.40, .5, .5, .1, .1), (1, 0.35, .2, .2, .1, .1)],
             str(imgs / "f2.jpg"): [],
             str(imgs / "f3.jpg"): [(1, 0.55, .3, .3, .1, .1)]}
    chosen = prelabel.select_images(preds, "uncertain", 2, sure=0.6)
    assert chosen[0].endswith("f1.jpg") and chosen[1].endswith("f3.jpg")
    out = tmp_path / "to_label"
    cls = prelabel.write_prelabels(preds, ["tractor", "combine"], chosen, str(imgs), str(out),
                                   add_classes=["deer"])
    assert cls == ["tractor", "combine", "deer"]
    assert (out / "classes.txt").read_text().split() == cls
    assert len(open(out / "f1.txt").readlines()) == 2
    # the user adds a deer box in the labelling tool (index 2 in classes.txt)
    with open(out / "f3.txt", "a") as fh:
        fh.write("2 0.7 0.7 0.1 0.1\n")
    m = tmp_path / "m"
    assert merge_datasets.main([str(out), "--out", str(m), "--classes", str(classes),
                                "--val-frac", "0"]) == 0
    assert [r[0] for r in read_rows(m, "train")["to_label__f3.txt"]] == ["1", "3"]


# --------------------------------------------------- export + thresholds --

def test_export_labels_come_from_model_names(tmp_path):
    import export
    p = tmp_path / "l.txt"
    export.write_labels(str(p), {0: "tractor", 1: "deer"})
    assert p.read_text().split() == ["tractor", "deer"]


def test_per_class_thresholds():
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from silm_recon.detector import ObjectDetector

    class FakeInterp:
        def __init__(self, out):
            self.out = out

        def get_tensor(self, i):
            return self.out

    t = np.zeros((1, 4 + 2, 10), np.float32)
    for i, (cx, c, s) in enumerate([(0.2, 0, 0.35), (0.6, 1, 0.35), (0.8, 1, 0.55)]):
        t[0, :4, i] = (cx, 0.5, 0.05, 0.05)
        t[0, 4 + c, i] = s
    d = object.__new__(ObjectDetector)
    d.interpreter, d._out = FakeInterp(t), [{"index": 0, "quantization": (0.0, 0)}]
    d.labels, d.fmt = {0: "fire", 1: "person"}, "yolo"
    d.score_threshold, d.iou_threshold = 0.45, 0.45
    d.class_thresholds = {"fire": 0.30, "person": 0.60}
    got = sorted((x.label, round(x.score, 2)) for x in d._detect_yolo())
    assert got == [("fire", 0.35)]   # fire kept at 0.35 (<0.45), person 0.55 dropped (<0.60)
    d.class_thresholds = {}
    got = sorted((x.label, round(x.score, 2)) for x in d._detect_yolo())
    assert got == [("person", 0.55)]


# -------------------------------------------------------- pixels on target --

def test_pixels_on_target_numbers_and_verdicts():
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
    import pixels_on_target as pot
    fx = pot.focal_px(1280, 86.0)
    assert fx == pytest.approx(640 / np.tan(np.radians(43)))
    pf, pm = pot.pixels(1.0, 100.0, fx, 1280, 320)
    assert pf == pytest.approx(fx / 100) and pm == pytest.approx(pf / 4)
    _, pm_t = pot.pixels(1.0, 100.0, fx, 1280, 320, tiles=2)
    assert pm_t == pytest.approx(2 * pm)                     # 2x2 tiles double it
    alt = pot.max_altitude(1.8, fx, 1280, 320, tiles=2, min_px=10)
    assert pot.pixels(1.8, alt, fx, 1280, 320, 2)[1] == pytest.approx(10.0)
    cal = {"width": 1920, "camera_matrix": [[1000.0, 0, 960], [0, 1000.0, 540], [0, 0, 1]]}
    assert pot.focal_px(1280, calibration=cal) == pytest.approx(1000 * 1280 / 1920)
    assert pot.main(["--object-m", "3.5", "--alt-m", "60", "--tiles", "2"]) == 0   # combine: ok
    assert pot.main(["--object-m", "0.5", "--alt-m", "100"]) == 1                  # deer: no


def test_merge_per_flight_class_lists_and_test_folder(tmp_path, classes):
    """Each flight exported separately with its own classes.txt in a different
    order; one fully labelled flight held out under test/."""
    src = tmp_path / "labelled"
    flat_folder(src / "flight_a", ["deer", "tractor"], {"a.jpg": ["0 .5 .5 .1 .1", "1 .2 .2 .1 .1"]})
    flat_folder(src / "flight_b", ["tractor", "deer"], {"b.jpg": ["0 .5 .5 .1 .1", "1 .2 .2 .1 .1"]})
    flat_folder(src / "test" / "flight_c", ["deer"], {"c.jpg": ["0 .5 .5 .1 .1"]})
    out = tmp_path / "m"
    assert merge_datasets.main([str(src), "--out", str(out), "--classes", str(classes),
                                "--val-frac", "0"]) == 0
    tr = read_rows(out, "train")
    assert [r[0] for r in tr["labelled__flight_a__a.txt"]] == ["3", "0"]   # deer, tractor
    assert [r[0] for r in tr["labelled__flight_b__b.txt"]] == ["0", "3"]   # tractor, deer
    assert [r[0] for r in read_rows(out, "test")["labelled__test__flight_c__c.txt"]] == ["3"]


def test_merge_errors_when_labels_have_no_class_list(tmp_path, classes):
    d = tmp_path / "nolist"
    write_img(d / "x.jpg")
    (d / "x.txt").write_text("0 .5 .5 .1 .1\n")
    with pytest.raises(SystemExit):
        merge_datasets.main([str(d), "--out", str(tmp_path / "m"), "--classes", str(classes)])


def test_time_in_view():
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
    import pixels_on_target as pot
    fx = pot.focal_px(1280, 86.0)
    foot, secs = pot.time_in_view(100.0, fx, 720, 19.0)
    assert foot == pytest.approx(2 * 100 * 360 / fx) and secs == pytest.approx(foot / 19.0)


# --------------------------------------------------------- threshold sweep --

def test_threshold_sweep_precision_recall():
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
    from threshold_sweep import sweep
    gts = {"a": [("deer", None, (0.1, 0.1, 0.2, 0.2)), ("deer", None, (0.5, 0.5, 0.6, 0.6))],
           "b": []}
    dets = {"a": [("deer", 0.9, (0.1, 0.1, 0.2, 0.2)),      # correct
                  ("deer", 0.4, (0.5, 0.5, 0.6, 0.6)),      # correct, low score
                  ("deer", 0.8, (0.11, 0.1, 0.21, 0.2))],   # duplicate of the first
            "b": [("deer", 0.6, (0.3, 0.3, 0.4, 0.4))]}     # false alarm
    r = sweep(dets, gts, [0.3, 0.5, 0.95])["deer"]
    assert (r[0.3]["tp"], r[0.3]["fp"], r[0.3]["fn"]) == (2, 2, 0)
    assert r[0.3]["precision"] == 0.5 and r[0.3]["recall"] == 1.0
    assert (r[0.5]["tp"], r[0.5]["fp"], r[0.5]["fn"]) == (1, 2, 1)
    assert r[0.95]["precision"] is None and r[0.95]["recall"] == 0.0
    assert r[0.3]["fp_per_img"] == 1.0


def test_export_finds_training_data_from_run_args(tmp_path):
    import export
    run = tmp_path / "runs" / "detect" / "farm_v2"
    (run / "weights").mkdir(parents=True)
    data = tmp_path / "data.yaml"
    data.write_text("names: {0: a}\n")
    (run / "args.yaml").write_text(yaml.safe_dump({"data": str(data), "imgsz": 320}))
    w = run / "weights" / "best.pt"
    w.write_text("")
    assert export.find_training_data(str(w), None) == str(data)
    (run / "args.yaml").write_text(yaml.safe_dump({"data": str(tmp_path / "gone.yaml")}))
    assert export.find_training_data(str(w), None) is None


# ---- Open Images dataset builder: which photos and boxes it takes ---------

def test_openimages_selection_rules():
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "training"))
    import openimages_dataset as oi
    T, H, LV, V = oi.TRACTOR_IMG, oi.HARVESTER_IMG, oi.LAND_VEHICLE, oi.VEHICLE
    verified = {"trac1": {T}, "trac_with_car": {T}, "harv": {H}, "trac_harv": {T, H},
                "trac_nobox": {T}}
    boxes = {
        "trac1": [(LV, .1, .1, .5, .5), (oi.CATTLE, .6, .6, .8, .8)],
        "trac_with_car": [(V, .1, .1, .5, .5), (oi.CAR, .6, .6, .9, .9)],   # ambiguous
        "harv": [(LV, .2, .2, .6, .6)],
        "trac_harv": [(LV, .2, .2, .6, .6)],                               # tractor or combine?
        "car1": [(oi.CAR, .1, .1, .4, .4), (oi.TRUCK, .5, .5, .9, .9)],
        "car_unnamed": [(oi.CAR, .1, .1, .4, .4), (V, .5, .5, .9, .9)],     # unnamed vehicle
        "speck": [(oi.CAR, .1, .1, .12, .12)],                              # too small
        "sheep": [(oi.SHEEP, .1, .1, .5, .5)],
    }
    p = oi.choose(verified, boxes, per_class=10)
    assert p["trac1"] == [("tractor", .1, .1, .5, .5), ("livestock", .6, .6, .8, .8)]
    assert "trac_with_car" not in p and "harv" not in p and "trac_harv" not in p
    assert "trac_nobox" not in p and "car_unnamed" not in p and "speck" not in p
    assert sorted(c for c, *_ in p["car1"]) == ["car", "truck"]
    assert p["sheep"] == [("livestock", .1, .1, .5, .5)]
    with_combine = oi.choose(verified, boxes, per_class=10, combine=True)
    assert with_combine["harv"] == [("combine", .2, .2, .6, .6)]
    names = oi.silm_classes()
    assert {"tractor", "car", "truck", "livestock", "combine"} <= set(names)


def test_export_keeps_only_the_detection_head_in_float():
    """Fully-INT8 YOLO exports pin every class score at ~0.50. export.py keeps
    the head in float; the regex must match the head's tensors (names as
    litert-torch writes them) and NOT the backbone, or nothing gets quantised."""
    import re
    import export
    pre = "ultralytics.utils.export.engine._NormalizeCoords/ultralytics.nn.tasks.DetectionModel_model/"
    head = [
        pre + "ultralytics.nn.modules.head.Detect_23/torch.nn.modules.container.Sequential_0/torch.nn.modules.conv.Conv2d_2;6",
        pre + "ultralytics.nn.modules.head.Detect_23/ultralytics.nn.modules.block.DFL_dfl;3",
        pre + "ultralytics.nn.modules.head.Detect_23;28",
        "ultralytics.utils.export.engine._NormalizeCoords;1",
        "serving_default_output_0_output",
    ]
    body = [
        pre + "ultralytics.nn.modules.conv.Conv_5/torch.nn.modules.activation.SiLU_act;",
        pre + "ultralytics.nn.modules.block.C3k2_2/torch.nn.modules.conv.Conv2d_conv;2",
    ]
    rx = re.compile(export.HEAD_REGEX)
    assert all(rx.fullmatch(n) for n in head)
    assert not any(rx.fullmatch(n) for n in body)


def test_export_calibration_images_are_letterboxed_nchw(tmp_path):
    import cv2
    import numpy as np
    import export
    p = tmp_path / "wide.jpg"
    cv2.imwrite(str(p), np.full((100, 400, 3), 255, np.uint8))
    x = export._letterbox_nchw(str(p), 320)
    assert x.shape == (1, 3, 320, 320) and x.dtype == np.float32
    assert abs(float(x[0, 0, 160, 160]) - 1.0) < 1e-3          # picture
    assert abs(float(x[0, 0, 5, 160]) - 114 / 255) < 1e-3      # grey padding
    assert export._letterbox_nchw(str(tmp_path / "missing.jpg"), 320) is None
