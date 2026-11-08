#!/usr/bin/env python3
"""Entry point for the SILM onboard program (runs on the aircraft's Raspberry Pi 4).

Usage:
    python3 run.py                         # config.yaml + ~/silm-settings/settings.yaml
    python3 run.py --config config.yaml --settings my_settings.yaml
    python3 run.py --selftest              # camera + detector check, no flight controller

At power-on the systemd service starts it (systemd/silm-recon.service). The
settings file holds your answers from the laptop Ground Station; it is sent
over the Pi's Wi-Fi hotspot or a USB stick (see GROUND_STATION.md).

program.mode in the settings:
    flight  - normal: records only while armed, in AUTO, above activation.min_alt_m
    bench   - records whenever it sees something (camera test on the ground)
"""

from __future__ import annotations

import argparse
import math
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from silm_recon.config import load_config, validate_config  # noqa: E402


def resolve(cfg: dict, base_dir: str) -> dict:
    """Make relative model/label paths relative to the onboard folder, so the
    program works whatever folder systemd starts it in."""
    det = cfg.get("detector") or {}
    for k in ("model", "labels"):
        p = det.get(k)
        if p:
            p = det[k] = os.path.expanduser(p)
        if p and not os.path.isabs(p):
            det[k] = os.path.join(base_dir, p)
    return cfg


def load_checked(config_path: str, settings: str | None = None) -> dict:
    """config.yaml + settings.yaml. If the merged result is invalid, use
    config.yaml alone and say why (a bad settings file must not ground the
    aircraft's camera)."""
    cfg = load_config(config_path, settings)
    errors = validate_config(cfg)
    if errors and cfg.get("_settings_loaded"):
        print(f"[run] WARNING: the settings file {cfg['_settings_file']} was IGNORED:")
        for e in errors:
            print(f"[run]   - {e}")
        cfg = load_config(config_path, settings="/nonexistent/silm-settings.yaml")
        cfg["_settings_rejected"] = errors
        errors = validate_config(cfg)
    if errors:
        raise SystemExit("config.yaml is invalid:\n  - " + "\n  - ".join(errors))
    mode = ((cfg.get("program") or {}).get("mode") or "flight").lower()
    if mode == "bench":
        cfg.setdefault("activation", {})["always_on"] = True
        print("[run] BENCH mode: recording whenever something is seen (not for flight)")
    return resolve(cfg, os.path.dirname(os.path.abspath(config_path)))


def selftest(cfg: dict) -> int:
    """Exercise camera + detector + fire + georef without a flight controller.

    Feeds frames from the configured camera (set camera.source: file to use a
    folder of test images) and prints detections. Good for verifying the model
    and the camera's Ethernet video path on the bench before a flight.
    """
    from silm_recon.camera import Camera
    from silm_recon.detector import ObjectDetector
    from silm_recon.fire import FireDetector
    from silm_recon.georef import GeoReferencer

    cam = Camera(cfg["camera"])
    det = None
    if cfg["detector"].get("enabled", True):
        det = ObjectDetector(
            cfg["detector"]["model"], cfg["detector"]["labels"],
            float(cfg["detector"].get("score_threshold", 0.5)),
            fmt=cfg["detector"].get("format", "auto"),
            iou_threshold=float(cfg["detector"].get("iou_threshold", 0.45)),
            class_thresholds=cfg["detector"].get("class_thresholds"),
        )
    fire = FireDetector(cfg.get("fire", {}))
    geo = GeoReferencer(cfg.get("georef", {}), image_size=cam.frame_size)
    targets = set(cfg.get("targets", []))

    print("[selftest] reading 30 frames...")
    for i in range(30):
        # wait for a NEW frame (a live stream delivers them at its own rate)
        frame, t_wait = None, time.time()
        while frame is None and time.time() - t_wait < 5:
            frame = cam.read()
            if frame is None:
                time.sleep(0.01)
        if frame is None:
            print(f"  frame {i}: <no frame for 5 s>")
            continue
        hits = []
        if det is not None:
            dcfg = cfg["detector"]
            roi = tuple(dcfg["roi"]) if dcfg.get("roi") else None
            grid = tuple(dcfg.get("tile_grid", [2, 2])) if dcfg.get("tiled") else (1, 1)
            hits += [(d.label, d.score, d.box) for d in det.detect_roi(frame, roi, grid)
                     if not targets or d.label in targets]
        hits += [(f.label, f.score, f.box) for f in fire.detect(frame)]
        if hits:
            # Demonstrate geo-ref with a dummy nadir pose at 100 m AGL.
            bx = hits[0][2]
            gp = geo.project(0.5 * (bx[0] + bx[2]), 0.5 * (bx[1] + bx[3]),
                             58.3780, 24.5000, 100.0, 0.0, 0.0, math.radians(90))
            extra = f" georef~({gp.lat:.5f},{gp.lon:.5f})" if gp.valid else ""
            print(f"  frame {i}: " + ", ".join(f"{l}:{s:.2f}" for l, s, _ in hits) + extra)
        else:
            print(f"  frame {i}: (nothing)")
    cam.release()
    print("[selftest] done")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="SILM onboard program (Raspberry Pi 4)")
    ap.add_argument("--config", default=os.path.join(HERE, "config.yaml"))
    ap.add_argument("--settings", default=None,
                    help="settings file from the Ground Station (default ~/silm-settings/settings.yaml)")
    ap.add_argument("--selftest", action="store_true",
                    help="run camera+detector without a flight controller")
    ap.add_argument("--check", action="store_true",
                    help="only check the configuration and print the result")
    args = ap.parse_args()

    cfg = load_checked(args.config, args.settings)
    print(f"[run] settings file: {cfg['_settings_file']} "
          f"({'loaded' if cfg['_settings_loaded'] else 'not present - using config.yaml defaults'})")
    if args.check:
        print("[run] configuration OK")
        return 0 if not cfg.get("_settings_rejected") else 1
    if args.selftest:
        return selftest(cfg)

    from silm_recon.recon import Recon
    Recon(cfg).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
