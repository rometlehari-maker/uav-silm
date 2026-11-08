"""Configuration: config.yaml (defaults, in the repo) + settings.yaml (your
answers from the laptop Ground Station, sent over Wi-Fi or a USB stick).

    cfg = load_config("config.yaml")            # + the settings file, if present

settings.yaml only contains what you changed in the Ground Station; it is
merged on top of config.yaml key by key (nested sections merge, lists and
values replace). Updating the software (git pull) never overwrites it.

validate_config() checks a merged config before it is used or accepted from
the laptop, so a typo can't stop the flight program at the field.
"""

from __future__ import annotations

import copy
import os

import yaml

DEFAULT_SETTINGS = "~/silm-settings/settings.yaml"

CAMERA_SOURCES = ("rtsp", "usb", "picamera", "file")
DETECTOR_FORMATS = ("auto", "ssd", "yolo")


def deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def settings_path(cfg: dict, override: str | None = None) -> str:
    p = override or os.environ.get("SILM_SETTINGS") or cfg.get("settings_file") or DEFAULT_SETTINGS
    return os.path.expanduser(p)


def load_config(path: str, settings: str | None = None) -> dict:
    """config.yaml merged with the settings file (if it exists). The merged
    config gets `_settings_file` (path used) and `_settings_loaded` (bool)."""
    with open(path, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    sp = settings_path(cfg, settings)
    loaded = False
    if os.path.isfile(sp):
        with open(sp, encoding="utf-8") as fh:
            over = yaml.safe_load(fh) or {}
        if not isinstance(over, dict):
            raise ValueError(f"{sp}: expected a mapping of settings")
        cfg = deep_merge(cfg, over)
        loaded = True
    cfg["_settings_file"], cfg["_settings_loaded"] = sp, loaded
    return cfg


def _num(errors, cfg, section, key, lo=None, hi=None, required=False):
    sec = cfg.get(section) or {}
    if key not in sec:
        if required:
            errors.append(f"{section}.{key} is missing")
        return
    v = sec[key]
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        errors.append(f"{section}.{key} must be a number (got {v!r})")
        return
    if lo is not None and v < lo:
        errors.append(f"{section}.{key} = {v} is below {lo}")
    if hi is not None and v > hi:
        errors.append(f"{section}.{key} = {v} is above {hi}")


def validate_config(cfg: dict) -> list[str]:
    """Problems that would stop or break the flight program (empty = fine)."""
    e: list[str] = []
    if not isinstance(cfg, dict):
        return ["the settings are not a mapping"]
    for sec in ("telemetry", "camera", "detector", "storage", "activation"):
        if not isinstance(cfg.get(sec), dict):
            e.append(f"section '{sec}' is missing")
    if e:
        return e
    if not str(cfg["telemetry"].get("connection") or "").strip():
        e.append("telemetry.connection (how the Pi reaches the flight controller) is empty")
    _num(e, cfg, "telemetry", "baud", 9600, 3000000)
    src = cfg["camera"].get("source")
    if src not in CAMERA_SOURCES:
        e.append(f"camera.source must be one of {', '.join(CAMERA_SOURCES)} (got {src!r})")
    if src == "rtsp" and not str(cfg["camera"].get("url") or "").startswith("rtsp://"):
        e.append("camera.url must start with rtsp://")
    _num(e, cfg, "camera", "width", 160, 7680)
    _num(e, cfg, "camera", "height", 120, 4320)
    det = cfg["detector"]
    if det.get("enabled", True):
        for k in ("model", "labels"):
            if not str(det.get(k) or "").strip():
                e.append(f"detector.{k} is empty")
        if det.get("format", "auto") not in DETECTOR_FORMATS:
            e.append(f"detector.format must be one of {', '.join(DETECTOR_FORMATS)}")
        _num(e, cfg, "detector", "score_threshold", 0.05, 0.99)
        roi = det.get("roi")
        if roi is not None:
            ok = (isinstance(roi, (list, tuple)) and len(roi) == 4
                  and all(isinstance(v, (int, float)) for v in roi)
                  and 0 <= roi[0] < roi[2] <= 1 and 0 <= roi[1] < roi[3] <= 1)
            if not ok:
                e.append("detector.roi must be [x0, y0, x1, y1] between 0 and 1")
        g = det.get("tile_grid", [2, 2])
        if not (isinstance(g, (list, tuple)) and len(g) == 2
                and all(isinstance(v, int) and 1 <= v <= 4 for v in g)):
            e.append("detector.tile_grid must be two whole numbers from 1 to 4")
    t = cfg.get("targets", [])
    if not isinstance(t, list) or not all(isinstance(x, str) for x in t):
        e.append("targets must be a list of class names")
    if not str(cfg["storage"].get("output_dir") or "").strip():
        e.append("storage.output_dir is empty")
    _num(e, cfg, "storage", "jpeg_quality", 30, 100)
    _num(e, cfg, "storage", "frame_interval_s", 0.2, 600)
    _num(e, cfg, "activation", "min_alt_m", 0, 400)
    _num(e, cfg, "activation", "detect_every_n_frames", 1, 30)
    am = cfg["activation"].get("active_modes", ["AUTO"])
    if not isinstance(am, list) or not am:
        e.append("activation.active_modes must be a non-empty list of flight modes")
    if (cfg.get("gimbal") or {}).get("enabled") and src == "rtsp":
        _num(e, cfg, "gimbal", "pitch_deg", -90, 25)
    _num(e, cfg, "georef", "hfov_deg", 20, 200)
    _num(e, cfg, "confirm", "frames", 1, 10)
    return e
