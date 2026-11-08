"""Teach the aircraft new objects: pictures, boxes, training, a model for the Pi.

Your pictures and boxes live on the laptop in ~/SILM/training/<dataset>/:
    images/<name>.jpg          the pictures you added
    labels/<name>.txt          one line per box: "class cx cy w h" (YOLO
                               format, 0..1 of the picture). An EMPTY file means
                               "nothing to find in this picture" (that teaches
                               the model too). No file = not looked at yet.
    classes.txt                the object types, one per line (line = class number)

export() writes a train/val split + data.yaml for training/train.py
(Ultralytics YOLO); train() runs training and the export to a .tflite the Pi
runs; the model then goes to the aircraft with the settings.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time

import yaml

from groundstation import runtime  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ONBOARD = os.path.dirname(HERE)
TRAIN_DIR = os.path.join(ONBOARD, "training")
NAME_RE = re.compile(r"[^A-Za-z0-9_\-]+")
MAX_IMAGE_BYTES = 25_000_000
MIN_PER_CLASS = 30            # below this a model learns almost nothing
GOOD_PER_CLASS = 150


def default_classes() -> list[str]:
    try:
        names = (yaml.safe_load(open(os.path.join(TRAIN_DIR, "classes.yaml"))) or {}).get("names", {})
        return [names[i] for i in sorted(names)] if isinstance(names, dict) else list(names)
    except OSError:
        return ["tractor", "combine", "car", "truck"]


def _clean(name: str) -> str:
    return NAME_RE.sub("_", (name or "").strip()).strip("_")[:60]


def image_kind(data: bytes) -> str | None:
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    return None


class Dataset:
    def __init__(self, root: str, name: str = "my_objects"):
        self.name = _clean(name) or "my_objects"
        self.dir = os.path.join(root, self.name)
        self.images = os.path.join(self.dir, "images")
        self.labels = os.path.join(self.dir, "labels")
        os.makedirs(self.images, exist_ok=True)
        os.makedirs(self.labels, exist_ok=True)
        if not os.path.isfile(self._classes_file()):
            self._write_classes(default_classes())

    # ------------------------------------------------------------ classes
    def _classes_file(self) -> str:
        return os.path.join(self.dir, "classes.txt")

    def classes(self) -> list[str]:
        with open(self._classes_file(), encoding="utf-8") as fh:
            return [ln.strip() for ln in fh if ln.strip()]

    def _write_classes(self, names) -> None:
        with open(self._classes_file(), "w", encoding="utf-8") as fh:
            fh.write("\n".join(names) + "\n")

    def add_class(self, name: str) -> list[str]:
        n = _clean(name).lower()
        if not n:
            raise ValueError("type a name for the new object type, e.g. 'sprayer'")
        names = self.classes()
        if n not in names:
            names.append(n)               # appended: existing class numbers never change
            self._write_classes(names)
        return names

    # ------------------------------------------------------------ images
    def add_image(self, filename: str, data: bytes) -> str:
        if len(data) > MAX_IMAGE_BYTES:
            raise ValueError(f"{filename}: larger than {MAX_IMAGE_BYTES // 1_000_000} MB")
        kind = image_kind(data)
        if kind is None:
            raise ValueError(f"{filename}: not a JPEG or PNG picture")
        digest = hashlib.sha1(data).hexdigest()[:8]
        for f in os.listdir(self.images):           # the same picture again = the same file
            if os.path.splitext(f)[0].endswith("_" + digest):
                return f
        stem = _clean(os.path.splitext(os.path.basename(filename))[0]) or "picture"
        path = os.path.join(self.images, f"{stem}_{digest}.{kind}")
        if not os.path.exists(path):
            with open(path, "wb") as fh:
                fh.write(data)
        return os.path.basename(path)

    def add_image_b64(self, filename: str, b64: str) -> str:
        if "," in b64[:100]:
            b64 = b64.split(",", 1)[1]          # data:image/jpeg;base64,....
        return self.add_image(filename, base64.b64decode(b64))

    def _img(self, name: str) -> str:
        p = os.path.realpath(os.path.join(self.images, os.path.basename(name or "")))
        if not p.startswith(os.path.realpath(self.images) + os.sep) or not os.path.isfile(p):
            raise FileNotFoundError(name)
        return p

    def image_path(self, name: str) -> str:
        return self._img(name)

    def _label_path(self, name: str) -> str:
        return os.path.join(self.labels, os.path.splitext(os.path.basename(name))[0] + ".txt")

    def delete_image(self, name: str) -> None:
        os.remove(self._img(name))
        lp = self._label_path(name)
        if os.path.exists(lp):
            os.remove(lp)

    def list_images(self) -> list[dict]:
        names = self.classes()
        out = []
        for f in sorted(os.listdir(self.images)):
            if not f.lower().endswith((".jpg", ".png")):
                continue
            boxes = self.get_boxes(f)
            out.append({"name": f, "labelled": boxes is not None,
                        "boxes": 0 if boxes is None else len(boxes),
                        "classes": sorted({names[b["cls"]] for b in boxes or []
                                           if b["cls"] < len(names)})})
        return out

    # ------------------------------------------------------------ boxes
    def get_boxes(self, name: str) -> list[dict] | None:
        """[{cls, x0, y0, x1, y1}] (0..1), [] = nothing in it, None = not labelled."""
        lp = self._label_path(name)
        if not os.path.isfile(lp):
            return None
        out = []
        with open(lp, encoding="utf-8") as fh:
            for ln in fh:
                f = ln.split()
                if len(f) != 5:
                    continue
                c, cx, cy, w, h = int(f[0]), *map(float, f[1:])
                out.append({"cls": c, "x0": cx - w / 2, "y0": cy - h / 2,
                            "x1": cx + w / 2, "y1": cy + h / 2})
        return out

    def set_boxes(self, name: str, boxes: list[dict]) -> int:
        self._img(name)
        n = len(self.classes())
        lines = []
        for b in boxes or []:
            c = int(b["cls"])
            if not 0 <= c < n:
                raise ValueError(f"unknown object type number {c}")
            x0, x1 = sorted((min(max(float(b["x0"]), 0.0), 1.0), min(max(float(b["x1"]), 0.0), 1.0)))
            y0, y1 = sorted((min(max(float(b["y0"]), 0.0), 1.0), min(max(float(b["y1"]), 0.0), 1.0)))
            if x1 - x0 < 0.002 or y1 - y0 < 0.002:
                continue                   # a click, not a box
            lines.append(f"{c} {(x0 + x1) / 2:.6f} {(y0 + y1) / 2:.6f} {x1 - x0:.6f} {y1 - y0:.6f}")
        with open(self._label_path(name), "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + ("\n" if lines else ""))
        return len(lines)

    # ------------------------------------------------------------ readiness
    def stats(self) -> dict:
        names = self.classes()
        per = {n: {"boxes": 0, "pictures": 0} for n in names}
        imgs = self.list_images()
        for im in imgs:
            for c in im["classes"]:
                per[c]["pictures"] += 1
        for im in imgs:
            for b in self.get_boxes(im["name"]) or []:
                if b["cls"] < len(names):
                    per[names[b["cls"]]]["boxes"] += 1
        used = {n: v for n, v in per.items() if v["boxes"]}
        advice = []
        for n, v in used.items():
            if v["pictures"] < MIN_PER_CLASS:
                advice.append(f"{n}: {v['pictures']} pictures - add at least {MIN_PER_CLASS} "
                              f"(good: {GOOD_PER_CLASS}+, from above, at your flying height)")
        labelled = sum(im["labelled"] for im in imgs)
        if labelled < len(imgs):
            advice.append(f"{len(imgs) - labelled} picture(s) not marked yet")
        return {"classes": names, "per_class": per, "pictures": len(imgs), "labelled": labelled,
                "empty": sum(im["labelled"] and im["boxes"] == 0 for im in imgs),
                "ready": bool(used) and not any(v["pictures"] < MIN_PER_CLASS for v in used.values()),
                "advice": advice}

    # ------------------------------------------------------------ export
    def export(self, val_share: float = 0.2) -> str:
        """YOLO dataset (images/labels train/val + data.yaml). Returns data.yaml."""
        out = os.path.join(self.dir, "export")
        if os.path.isdir(out):
            shutil.rmtree(out)
        n_train = n_val = 0
        for im in self.list_images():
            if not im["labelled"]:
                continue
            h = int(hashlib.sha1(im["name"].encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
            split = "val" if h < val_share else "train"
            for sub in ("images", "labels"):
                os.makedirs(os.path.join(out, sub, split), exist_ok=True)
            shutil.copyfile(self._img(im["name"]), os.path.join(out, "images", split, im["name"]))
            shutil.copyfile(self._label_path(im["name"]),
                            os.path.join(out, "labels", split,
                                         os.path.splitext(im["name"])[0] + ".txt"))
            n_train += split == "train"
            n_val += split == "val"
        if n_train == 0:
            raise ValueError("no marked pictures to train on yet")
        if n_val == 0:              # tiny sets: validate on the training pictures
            shutil.copytree(os.path.join(out, "images", "train"), os.path.join(out, "images", "val"))
            shutil.copytree(os.path.join(out, "labels", "train"), os.path.join(out, "labels", "val"))
        names = self.classes()
        data = {"path": out, "train": "images/train", "val": "images/val",
                "names": {i: n for i, n in enumerate(names)}}
        p = os.path.join(out, "data.yaml")
        with open(p, "w", encoding="utf-8") as fh:
            yaml.safe_dump(data, fh, sort_keys=False)
        return p


# ---------------------------------------------------------------- training

def tools_available() -> dict:
    """Can this laptop train? With what to do if not (the tab offers to install them)."""
    py = runtime.training_python()
    if py is None:
        return {"available": False, "can_install": False,
                "why": "training needs Python 3 on this laptop first - see the guide 'Installing "
                       "Python' (python.org; on Windows tick 'Add python.exe to PATH'). Then press "
                       "'Install the training tools' here."}
    if runtime.has_module("ultralytics", py):
        return {"available": True, "can_install": True, "why": None}
    return {"available": False, "can_install": True,
            "why": "the training tools are not installed on this laptop yet. Press 'Install the "
                   "training tools' (2-6 GB, once; internet and about 8 GB free disk needed). A computer with an NVIDIA "
                   "graphics card trains 10-50x faster."}


PRESETS = {
    "quick": {"epochs": 15, "imgsz": 320, "label": "Quick test (minutes; checks that it works)"},
    "good": {"epochs": 80, "imgsz": 320, "label": "Good (an hour or more on a laptop)"},
    "best": {"epochs": 150, "imgsz": 640, "label": "Best (graphics card recommended; many hours)"},
}


class Trainer:
    """One training run at a time, in the background, with a progress log."""

    def __init__(self, models_dir: str, bundled_dirs: tuple = ()):
        self.models_dir = models_dir
        # Ready-made models shipped with the software (onboard/models): only
        # those with a <name>.json description and labels are offered.
        self.bundled_dirs = tuple(d for d in bundled_dirs if d and os.path.isdir(d))
        os.makedirs(models_dir, exist_ok=True)
        self.job: dict | None = None

    @staticmethod
    def _scan(folder: str, need_info: bool) -> list[dict]:
        out = []
        for f in sorted(os.listdir(folder)):
            if not f.endswith(".tflite") or f.endswith("_edgetpu.tflite"):
                continue
            base = f[:-7]
            ip = os.path.join(folder, base + ".json")
            if need_info and not (os.path.isfile(ip) and
                                  os.path.isfile(os.path.join(folder, base + "_labels.txt"))):
                continue
            info = {}
            if os.path.isfile(ip):
                try:
                    with open(ip, encoding="utf-8") as fh:
                        info = json.load(fh)
                except ValueError:
                    pass
            out.append({"name": base, "file": f, "dir": folder, "size_mb": round(
                os.path.getsize(os.path.join(folder, f)) / 1e6, 1), **info})
        return out

    def list_models(self) -> list[dict]:
        """Your trained models, then the bundled ones (yours win on a name clash)."""
        out = self._scan(self.models_dir, need_info=False)
        seen = {m["name"] for m in out}
        for d in self.bundled_dirs:
            for m in self._scan(d, need_info=True):
                if m["name"] not in seen:
                    seen.add(m["name"])
                    out.append({**m, "bundled": True})
        return out

    def model_dir(self, name: str) -> str:
        """The folder holding model <name> (yours first, then the bundled ones)."""
        for m in self.list_models():
            if m["name"] == name:
                return m["dir"]
        return self.models_dir

    def start(self, ds: Dataset, preset: str = "quick", device: str = "cpu",
              base_model: str = "yolo11n.pt") -> dict:
        if self.job and self.job["state"] == "running":
            return {"ok": False, "message": "a training run is already going"}
        av = tools_available()
        if not av["available"]:
            return {"ok": False, "message": av["why"]}
        st = ds.stats()
        if not any(v["boxes"] for v in st["per_class"].values()):
            return {"ok": False, "message": "mark some objects in your pictures first"}
        pr = PRESETS.get(preset, PRESETS["quick"])
        data_yaml = ds.export()
        name = f"{ds.name}_{time.strftime('%Y%m%d_%H%M')}"
        self.job = {"state": "running", "log": [], "name": name, "started": time.time(),
                    "step": "training", "result": None}
        args = (data_yaml, pr, device, base_model, name, ds.classes())
        threading.Thread(target=self._run, args=args, daemon=True).start()
        return {"ok": True, "message": f"training started ({pr['label']})", "name": name}

    def _log(self, line):
        self.job["log"].append(line.rstrip("\n"))
        del self.job["log"][:-2000]

    def _cmd(self, cmd, cwd):
        self._log("$ " + " ".join(os.path.basename(c) if i < 2 else c for i, c in enumerate(cmd)))
        p = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, bufsize=1)
        self.job["proc"] = p
        for line in p.stdout:
            self._log(line)
        return p.wait()

    def _run(self, data_yaml, pr, device, base_model, name, classes):
        j = self.job
        try:
            runs = os.path.join(os.path.dirname(data_yaml), "runs")
            code = self._cmd([*runtime.training_python(), "-u", os.path.join(TRAIN_DIR, "train.py"),
                              "--data", data_yaml, "--model", base_model, "--epochs",
                              str(pr["epochs"]), "--imgsz", str(pr["imgsz"]), "--device", device,
                              "--name", name, "--batch", "8", "--project", runs], TRAIN_DIR)
            best = os.path.join(runs, name, "weights", "best.pt")
            if code != 0 or not os.path.isfile(best):
                raise RuntimeError("training failed - see the log")
            j["step"] = "converting for the Raspberry Pi"
            code = self._cmd([*runtime.training_python(), "-u", os.path.join(TRAIN_DIR, "export.py"),
                              "--weights", best, "--imgsz", str(pr["imgsz"]), "--out",
                              self.models_dir, "--model-name", name, "--data", data_yaml], TRAIN_DIR)
            tfl = os.path.join(self.models_dir, name + ".tflite")
            if code != 0 or not os.path.isfile(tfl):
                raise RuntimeError("conversion to .tflite failed - see the log")
            lab = os.path.join(self.models_dir, name + "_labels.txt")
            if not os.path.isfile(lab):
                with open(lab, "w") as fh:
                    fh.write("\n".join(classes) + "\n")
            metrics = _read_metrics(os.path.join(runs, name, "results.csv"))
            info = {"classes": classes, "imgsz": pr["imgsz"], "epochs": pr["epochs"],
                    "trained": time.strftime("%Y-%m-%d %H:%M"), **metrics}
            with open(os.path.join(self.models_dir, name + ".json"), "w") as fh:
                json.dump(info, fh, indent=2)
            j["result"] = info
            j["state"] = "done"
        except Exception as exc:
            self._log(f"ERROR: {exc}")
            j["state"] = "failed"
        finally:
            j["step"] = "finished"
            j.pop("proc", None)

    def stop(self) -> dict:
        p = (self.job or {}).get("proc")
        if p and p.poll() is None:
            p.terminate()
        if self.job and self.job["state"] == "running":
            self.job["state"] = "stopped"
        return {"ok": True}

    def status(self) -> dict:
        j = self.job
        if not j:
            return {"state": "idle", **tools_available()}
        return {"state": j["state"], "step": j["step"], "name": j["name"], "log": j["log"][-200:],
                "result": j["result"], "elapsed_s": round(time.time() - j["started"]),
                **tools_available()}


def _read_metrics(path: str) -> dict:
    """Last row of Ultralytics results.csv: precision, recall, mAP50."""
    try:
        import csv
        rows = list(csv.DictReader(open(path)))
        last = {k.strip(): v for k, v in rows[-1].items()}

        def g(*keys):
            for k in keys:
                if k in last:
                    return round(float(last[k]), 3)
            return None
        return {"precision": g("metrics/precision(B)"), "recall": g("metrics/recall(B)"),
                "map50": g("metrics/mAP50(B)")}
    except Exception:
        return {}


def model_payload(models_dir: str, name: str) -> dict:
    """The model + labels, base64, for sending to the Pi."""
    base = os.path.join(models_dir, _clean(name))
    with open(base + ".tflite", "rb") as fh:
        tfl = fh.read()
    with open(base + "_labels.txt", encoding="utf-8") as fh:
        labels = fh.read()
    return {"name": _clean(name), "tflite_b64": base64.b64encode(tfl).decode(), "labels": labels}
