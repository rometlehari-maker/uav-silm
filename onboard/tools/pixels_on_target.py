#!/usr/bin/env python3
"""How many pixels will an object be? Check BEFORE adding a new class.

A detector can't learn what it can't see. As a rule of thumb, an object needs
roughly 10+ pixels across its SMALLER side at the MODEL'S input (not the
camera's) to be detected reliably; 5-10 is marginal and under ~5 is unlikely
to work. These are rules of thumb: the real test is your validation results.

The camera frame (or the detection region, detector.roi) is shrunk to the
model input, so small objects shrink with it. Defaults describe this
airframe's camera, the SIYI A2 mini: 1920x1080, 160 deg fisheye.

    python3 tools/pixels_on_target.py --object-m 0.5 --length-m 1.5 --alt-m 100
    python3 tools/pixels_on_target.py --object-m 1.8 --alt-m 60 --roi 0.5 --tiles 2
    python3 tools/pixels_on_target.py --object-m 1.8 --alt-m 60 --config config.yaml

--config reads the lens (georef.calibration / lens / hfov_deg), the stream size
(camera.width/height) and the detection region (detector.roi, tile_grid if
tiled). Numbers are for the image centre of a straight-down camera, where a
fisheye's resolution is highest.
"""

from __future__ import annotations

import argparse
import math
import sys

import yaml


def focal_px(frame_w: int, hfov_deg: float | None = None, calibration: dict | None = None,
             lens: str = "pinhole") -> float:
    """Pixels per radian at the image centre."""
    if calibration:
        return float(calibration["camera_matrix"][0][0]) * frame_w / float(calibration["width"])
    if lens == "fisheye":   # equidistant: radius = f * angle
        return (frame_w / 2.0) / math.radians(hfov_deg / 2.0)
    return (frame_w / 2.0) / math.tan(math.radians(hfov_deg) / 2.0)


def pixels(object_m, alt_m, fx, frame_w, imgsz, tiles=1, roi_w=1.0):
    """(pixels in the camera frame, pixels at the model input). The model-input
    figure never exceeds the camera's: enlarging a small tile to a larger model
    input adds no detail."""
    px_frame = object_m * fx / alt_m
    px_model = min(px_frame, px_frame * imgsz / (frame_w * roi_w / tiles))
    return px_frame, px_model


def time_in_view(alt_m, fy, frame_h, speed_mps, lens: str = "pinhole", roi_h: float = 1.0):
    """(along-track ground footprint of the detection region in m, seconds a
    point stays in it) for a straight-down camera, image top towards the nose."""
    half = (frame_h * roi_h / 2.0) / fy
    if lens == "fisheye":
        half = min(half, math.radians(85))
        footprint = 2.0 * alt_m * math.tan(half)
    else:
        footprint = 2.0 * alt_m * half
    return footprint, footprint / speed_mps


def max_altitude(object_m, fx, frame_w, imgsz, tiles=1, min_px=10.0, roi_w=1.0):
    scale = min(1.0, imgsz / (frame_w * roi_w / tiles))   # no gain from enlarging
    return object_m * fx * scale / min_px


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--object-m", type=float, required=True,
                    help="the object's SMALLER side seen from above, metres (decides the verdict)")
    ap.add_argument("--length-m", type=float, default=None, help="its longer side, metres (shown only)")
    ap.add_argument("--alt-m", type=float, required=True, help="height above the ground, metres")
    ap.add_argument("--config", help="config.yaml to read the camera, lens and detection region from")
    ap.add_argument("--lens", choices=["fisheye", "pinhole"], default="fisheye")
    ap.add_argument("--hfov-deg", type=float, default=160.0)
    ap.add_argument("--frame-width", type=int, default=1920)
    ap.add_argument("--frame-height", type=int, default=1080)
    ap.add_argument("--imgsz", type=int, default=320, help="model input size")
    ap.add_argument("--tiles", type=int, default=1, help="tile grid side (detector.tile_grid), 1 = off")
    ap.add_argument("--roi", type=float, default=1.0,
                    help="detection region width and height as a fraction of the frame (detector.roi)")
    ap.add_argument("--min-px", type=float, default=10.0)
    ap.add_argument("--speed-mps", type=float, default=19.0, help="ground speed, m/s")
    ap.add_argument("--fps", type=float, default=None,
                    help="detection frames per second you expect (prints frames per object)")
    a = ap.parse_args(argv)

    calib, lens, hfov = None, a.lens, a.hfov_deg
    fw, fh, roi_w, roi_h, tiles = a.frame_width, a.frame_height, a.roi, a.roi, a.tiles
    if a.config:
        cfg = yaml.safe_load(open(a.config)) or {}
        g, cam, det = cfg.get("georef", {}), cfg.get("camera", {}), cfg.get("detector", {})
        calib = g.get("calibration")
        lens = (calib or {}).get("model") or g.get("lens", lens)
        hfov = float(g.get("hfov_deg", hfov))
        fw, fh = int(cam.get("width", fw)), int(cam.get("height", fh))
        if det.get("roi"):
            x0, y0, x1, y1 = det["roi"]
            roi_w, roi_h = x1 - x0, y1 - y0
        if det.get("tiled"):
            tiles = int(det.get("tile_grid", [2, 2])[0])
    fx = focal_px(fw, hfov, calib, lens)
    pf, pm = pixels(a.object_m, a.alt_m, fx, fw, a.imgsz, tiles, roi_w)
    gsd = a.alt_m / fx * 100
    verdict = ("GOOD" if pm >= a.min_px else "MARGINAL - expect misses" if pm >= a.min_px / 2
               else "TOO SMALL - unlikely to work")
    region = f"{fw * roi_w:.0f}x{fh * roi_h:.0f}-px region" if roi_w < 1 or roi_h < 1 else f"{fw}-px frame"
    tiles_txt = f" ({tiles}x{tiles} tiles)" if tiles > 1 else ""
    print(f"camera: {fw}x{fh}, {lens} {hfov:g} deg; ground resolution at the centre: "
          f"{gsd:.1f} cm per pixel at {a.alt_m:.0f} m")
    if a.length_m:
        lf, lm = pixels(a.length_m, a.alt_m, fx, fw, a.imgsz, tiles, roi_w)
        print(f"{a.length_m} x {a.object_m} m object: {lf:.1f} x {pf:.1f} px in the camera image, "
              f"{lm:.1f} x {pm:.1f} px at the {a.imgsz}-px model input (from the {region}{tiles_txt})")
    else:
        print(f"{a.object_m} m object: {pf:.1f} px in the camera image, "
              f"{pm:.1f} px at the {a.imgsz}-px model input (from the {region}{tiles_txt})")
    print(f"verdict: {verdict}")
    if pm >= pf - 1e-9 and a.imgsz > fw * roi_w / tiles:
        print(f"note: each {fw * roi_w / tiles:.0f}-px tile is enlarged to {a.imgsz} px, which adds no "
              f"detail - the camera's own {pf:.1f} px is the limit")
    foot, secs = time_in_view(a.alt_m, fx, fh, a.speed_mps, lens, roi_h)
    line = (f"in the detection region for ~{secs:.1f} s at {a.speed_mps:.0f} m/s "
            f"({foot:.0f} m of ground along track)")
    if a.fps:
        line += f": ~{secs * a.fps:.1f} detection frames at {a.fps:g} fps"
        if secs * a.fps < 2:
            line += " - fewer than 2, so multi-frame confirmation (confirm.frames: 2) will miss it"
    print(line)
    if pm < a.min_px:
        print("\nto reach ~%.0f px at the model input:" % a.min_px)
        opts = []
        for o in [(a.imgsz, tiles, roi_w), (a.imgsz, 2, roi_w), (a.imgsz, 2, 0.5), (640, 2, 0.5)]:
            if o not in opts:
                opts.append(o)
        for imgsz, t, r in opts:
            alt = max_altitude(a.object_m, fx, fw, imgsz, t, a.min_px, r)
            print(f"  imgsz {imgsz:4d}, {t}x{t} tiles, region {r:.0%} of the width: "
                  f"fly at or below {alt:5.0f} m")
        print("  (more tiles / larger imgsz multiply the computing: check the speed with tools/preflight.py on the Pi 4)")
    return 0 if pm >= a.min_px / 2 else 1


if __name__ == "__main__":
    sys.exit(main())
