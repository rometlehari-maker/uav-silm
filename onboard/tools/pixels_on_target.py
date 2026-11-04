#!/usr/bin/env python3
"""How many pixels will an object be? Check BEFORE adding a new class.

A detector can't learn what it can't see. As a rule of thumb, an object needs
roughly 10+ pixels across its SMALLER side at the MODEL'S input (not the
camera's) to be detected reliably; 5-10 is marginal and under ~5 is unlikely
to work. These are rules of thumb: the real test is your validation results. The camera frame is shrunk to the model input
(e.g. 1280 -> 320 wide), so small objects shrink with it.

    python3 tools/pixels_on_target.py --object-m 1.5 --alt-m 100
    python3 tools/pixels_on_target.py --object-m 1.5 --alt-m 60 --tiles 2 --imgsz 320
    python3 tools/pixels_on_target.py --object-m 4.5 --alt-m 100 --config config.yaml

Uses the lens from --config (georef.calibration, else hfov_deg) or --hfov-deg.
Numbers are for the image centre of a straight-down camera; the edges of a
wide lens see objects smaller still.
"""

from __future__ import annotations

import argparse
import math
import sys

import yaml


def focal_px(frame_w: int, hfov_deg: float | None = None, calibration: dict | None = None) -> float:
    if calibration:
        return float(calibration["camera_matrix"][0][0]) * frame_w / float(calibration["width"])
    return (frame_w / 2.0) / math.tan(math.radians(hfov_deg) / 2.0)


def pixels(object_m, alt_m, fx, frame_w, imgsz, tiles=1):
    """(pixels in the camera frame, pixels at the model input)."""
    px_frame = object_m * fx / alt_m
    px_model = px_frame * imgsz / (frame_w / tiles)
    return px_frame, px_model


def time_in_view(alt_m, fy, frame_h, speed_mps):
    """(along-track ground footprint in m, seconds a point stays in view) for a
    straight-down camera with the image top towards the nose."""
    footprint = 2.0 * alt_m * (frame_h / 2.0) / fy
    return footprint, footprint / speed_mps


def max_altitude(object_m, fx, frame_w, imgsz, tiles=1, min_px=10.0):
    return object_m * fx * imgsz / (frame_w / tiles) / min_px


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--object-m", type=float, required=True,
                    help="the object's SMALLER side seen from above, metres (decides the verdict)")
    ap.add_argument("--length-m", type=float, default=None, help="its longer side, metres (shown only)")
    ap.add_argument("--alt-m", type=float, required=True, help="height above the ground, metres")
    ap.add_argument("--config", help="config.yaml to read the lens from")
    ap.add_argument("--hfov-deg", type=float, default=86.0)
    ap.add_argument("--frame-width", type=int, default=1280)
    ap.add_argument("--imgsz", type=int, default=320, help="model input size")
    ap.add_argument("--tiles", type=int, default=1, help="tile grid side (detector.tile_grid), 1 = off")
    ap.add_argument("--min-px", type=float, default=10.0)
    ap.add_argument("--frame-height", type=int, default=720)
    ap.add_argument("--speed-mps", type=float, default=19.0, help="ground speed, m/s")
    ap.add_argument("--fps", type=float, default=None,
                    help="detection frames per second you expect (prints frames per object)")
    a = ap.parse_args(argv)

    calib, hfov = None, a.hfov_deg
    if a.config:
        g = (yaml.safe_load(open(a.config)) or {}).get("georef", {})
        calib, hfov = g.get("calibration"), float(g.get("hfov_deg", hfov))
    fx = focal_px(a.frame_width, hfov, calib)
    pf, pm = pixels(a.object_m, a.alt_m, fx, a.frame_width, a.imgsz, a.tiles)
    gsd = a.alt_m / fx * 100
    verdict = ("GOOD" if pm >= a.min_px else "MARGINAL - expect misses" if pm >= a.min_px / 2
               else "TOO SMALL - unlikely to work")
    print(f"ground resolution: {gsd:.1f} cm per camera pixel at {a.alt_m:.0f} m")
    tiles_txt = f" ({a.tiles}x{a.tiles} tiles)" if a.tiles > 1 else ""
    if a.length_m:
        lf, lm = pixels(a.length_m, a.alt_m, fx, a.frame_width, a.imgsz, a.tiles)
        print(f"{a.length_m} x {a.object_m} m object: {lf:.1f} x {pf:.1f} px in the "
              f"{a.frame_width}-px frame, {lm:.1f} x {pm:.1f} px at the {a.imgsz}-px model input{tiles_txt}")
    else:
        print(f"{a.object_m} m object: {pf:.1f} px in the {a.frame_width}-px frame, "
              f"{pm:.1f} px at the {a.imgsz}-px model input{tiles_txt}")
    print(f"verdict: {verdict}")
    fy = fx  # square pixels (calibrated fy is within a few % of fx)
    foot, secs = time_in_view(a.alt_m, fy, a.frame_height, a.speed_mps)
    line = (f"in view for ~{secs:.1f} s at {a.speed_mps:.0f} m/s "
            f"({foot:.0f} m of ground along track)")
    if a.fps:
        line += f": ~{secs * a.fps:.1f} detection frames at {a.fps:g} fps"
        if secs * a.fps < 2:
            line += " - fewer than 2, so multi-frame confirmation (confirm.frames: 2) will miss it"
    print(line)
    if pm < a.min_px:
        print("\nto reach ~%.0f px at the model input:" % a.min_px)
        opts = []
        for o in [(a.imgsz, a.tiles), (a.imgsz, 2), (640, 1), (640, 2)]:
            if o not in opts:
                opts.append(o)
        for imgsz, tiles in opts:
            alt = max_altitude(a.object_m, fx, a.frame_width, imgsz, tiles, a.min_px)
            print(f"  imgsz {imgsz:4d}, {tiles}x{tiles} tiles: fly at or below {alt:5.0f} m")
        print("  (larger imgsz / tiling need more compute: on a Pi 3 that means a Coral)")
    return 0 if pm >= a.min_px / 2 else 1


if __name__ == "__main__":
    sys.exit(main())
