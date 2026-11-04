#!/usr/bin/env python3
"""Entry point for the SILM onboard reconnaissance system.

Usage:
    python3 run.py --config config.yaml
    python3 run.py --config config.yaml --selftest   # no flight controller needed
"""

from __future__ import annotations

import argparse
import sys
import time

import yaml

from silm_recon.recon import Recon


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def selftest(cfg: dict) -> int:
    """Exercise camera + detector + fire + georef without a flight controller.

    Feeds frames from the configured camera (set camera.source: file to use a
    folder of test images) and prints detections. Good for verifying the model
    and the camera's Ethernet video path on the bench before a flight.
    """
    import math

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
    ap = argparse.ArgumentParser(description="SILM onboard reconnaissance")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--selftest", action="store_true",
                    help="run camera+detector without a flight controller")
    args = ap.parse_args()

    cfg = load_config(args.config)
    if args.selftest:
        return selftest(cfg)

    Recon(cfg).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
