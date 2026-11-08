#!/usr/bin/env python3
"""Calibrate the camera lens (focal length, centre, distortion).

The SIYI A2 Mini is wide-angle, so without calibration geo-referenced positions
drift near the frame edges. This measures the lens from photos of a printed
checkerboard and writes a block you paste into config.yaml under `georef:`.

1. Print a checkerboard (e.g. 9x6 inner corners, 25 mm squares) and tape it to
   something flat and rigid.
2. Take 15-30 photos with the AIRCRAFT CAMERA, from its live stream at the
   same resolution you fly with. Cover the whole frame, especially
   the corners and edges, and tilt the board at different angles.
3. Run one of:

    # from a folder of photos
    python3 tools/calibrate_camera.py --images calib/ --cols 9 --rows 6 --square-mm 25

    # live, on the Pi: grabs a frame every second the board is visible
    python3 tools/calibrate_camera.py --device 0 --capture 25 --cols 9 --rows 6 --square-mm 25

4. Check the reported RMS error (under ~0.5 px is good, over 1 px: retake
   photos), then paste the printed `calibration:` block under `georef:`.
"""

from __future__ import annotations

import argparse
import glob
import math
import os
import sys
import time

import cv2
import numpy as np
import yaml


def find_corners(gray, cols, rows):
    flags = cv2.CALIB_CB_ADAPTIVE_THRESH | cv2.CALIB_CB_NORMALIZE_IMAGE
    ok, corners = cv2.findChessboardCorners(gray, (cols, rows), flags)
    if not ok:
        return None
    # The sub-pixel search window must stay inside one square, or refinement
    # jumps to a neighbouring corner. Squares shrink with distance and are
    # squeezed by barrel distortion near the frame edges, so size the window
    # from the smallest spacing between neighbouring corners on this board.
    grid = corners.reshape(rows, cols, 2)
    spacing = min(np.linalg.norm(np.diff(grid, axis=1), axis=2).min(),
                  np.linalg.norm(np.diff(grid, axis=0), axis=2).min())
    half = int(max(2, min(11, spacing * 0.3)))
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 40, 1e-3)
    return cv2.cornerSubPix(gray, corners, (half, half), (-1, -1), crit)


def calibrate_points(obj_pts, img_pts, size, fisheye: bool = False) -> dict:
    """Calibrate from matched board corners. obj_pts: list of (N,3) arrays in
    metres; img_pts: list of (N,1,2) pixel arrays; size: (w, h)."""
    w, h = size
    if fisheye:
        # OpenCV's fisheye model (theta-polynomial), right for lenses beyond
        # ~120 deg such as the SIYI A2 mini's 160 deg.
        # (1, N, 3) / (1, N, 2): the shape both OpenCV 4 and 5 accept here
        objs = [np.asarray(o, np.float64).reshape(1, -1, 3) for o in obj_pts]
        imgs = [np.asarray(i, np.float64).reshape(1, -1, 2) for i in img_pts]
        K = np.eye(3)
        K[0, 0] = K[1, 1] = (w / 2) / math.radians(80)    # start: ~160 deg equidistant
        K[0, 2], K[1, 2] = w / 2, h / 2
        D = np.zeros((4, 1))
        def flag(name):  # OpenCV 4: cv2.fisheye.X, OpenCV 5: cv2.X
            return getattr(cv2.fisheye, name, None) or getattr(cv2, name)
        flags = (flag("CALIB_RECOMPUTE_EXTRINSIC") | flag("CALIB_FIX_SKEW")
                 | flag("CALIB_USE_INTRINSIC_GUESS"))
        crit = (cv2.TERM_CRITERIA_COUNT + cv2.TERM_CRITERIA_EPS, 200, 1e-9)
        rms, K, D, _, _ = cv2.fisheye.calibrate(objs, imgs, (w, h), K, D, flags=flags, criteria=crit)
        # horizontal FOV: angle of the left/right image edge at the centre row
        edge = np.array([[[0.0, K[1, 2]], [w - 1.0, K[1, 2]]]], np.float64)
        und = cv2.fisheye.undistortPoints(edge, K, D)[0]
        hfov = math.degrees(math.atan(abs(und[0, 0])) + math.atan(abs(und[1, 0])))
        model = "fisheye"
    else:
        objs = [np.asarray(o, np.float32) for o in obj_pts]
        imgs = [np.asarray(i, np.float32) for i in img_pts]
        rms, K, D, _, _ = cv2.calibrateCamera(objs, imgs, (w, h), None, None)
        hfov = math.degrees(2 * math.atan(w / (2 * K[0, 0])))
        model = "pinhole"
    return {
        "model": model,
        "width": int(w),
        "height": int(h),
        "camera_matrix": [[round(float(v), 4) for v in row] for row in K],
        "dist_coeffs": [round(float(v), 6) for v in np.asarray(D).reshape(-1)],
        "rms_px": round(float(rms), 4),
        "views_used": len(obj_pts),
        "hfov_deg": round(hfov, 2),
    }


def calibrate(images, cols: int, rows: int, square_m: float, fisheye: bool = False) -> dict:
    """Calibrate from a list of BGR images. Returns a config-ready dict."""
    objp = np.zeros((cols * rows, 3), np.float32)
    objp[:, :2] = np.mgrid[0:cols, 0:rows].T.reshape(-1, 2) * square_m
    obj_pts, img_pts, size = [], [], None
    for img in images:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
        if size is None:
            size = gray.shape[::-1]
        elif gray.shape[::-1] != size:
            raise ValueError("all calibration images must have the same resolution")
        c = find_corners(gray, cols, rows)
        if c is not None:
            obj_pts.append(objp)
            img_pts.append(c)
    if len(obj_pts) < 8:
        raise ValueError(f"board found in only {len(obj_pts)} images; need at least 8")
    return calibrate_points(obj_pts, img_pts, size, fisheye)


def load_folder(folder):
    files = sorted(glob.glob(os.path.join(folder, "*.jpg")) + glob.glob(os.path.join(folder, "*.png")))
    return [img for img in (cv2.imread(f) for f in files) if img is not None]


def capture_live(device, n, cols, rows):
    cap = cv2.VideoCapture(device)
    if not cap.isOpened():
        raise SystemExit(f"cannot open camera {device}")
    shots, last = [], 0.0
    print(f"show the board to the camera; collecting {n} views (Ctrl-C to stop)")
    try:
        while len(shots) < n:
            ok, frame = cap.read()
            if not ok:
                continue
            if time.time() - last < 1.0:
                continue
            if find_corners(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), cols, rows) is not None:
                shots.append(frame)
                last = time.time()
                print(f"  view {len(shots)}/{n}")
    except KeyboardInterrupt:
        pass
    cap.release()
    return shots


def main() -> int:
    ap = argparse.ArgumentParser()
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--images", help="folder of checkerboard photos")
    src.add_argument("--device", help="camera index or path for live capture")
    ap.add_argument("--capture", type=int, default=25, help="views to grab live")
    ap.add_argument("--cols", type=int, default=9, help="inner corners per row")
    ap.add_argument("--rows", type=int, default=6, help="inner corners per column")
    ap.add_argument("--square-mm", type=float, default=25.0)
    ap.add_argument("--fisheye", action="store_true",
                    help="use the fisheye lens model (for very wide lenses, e.g. the A2 mini's 160 deg)")
    ap.add_argument("--out", default="calibration.yaml")
    args = ap.parse_args()

    if args.images:
        imgs = load_folder(args.images)
    else:
        dev = int(args.device) if str(args.device).isdigit() else args.device
        imgs = capture_live(dev, args.capture, args.cols, args.rows)
    print(f"{len(imgs)} images")
    try:
        cal = calibrate(imgs, args.cols, args.rows, args.square_mm / 1000.0, fisheye=args.fisheye)
    except ValueError as exc:
        print(f"calibration failed: {exc}")
        return 1

    quality = "good" if cal["rms_px"] < 0.5 else ("ok" if cal["rms_px"] < 1.0 else "POOR - retake photos")
    print(f"\nRMS reprojection error: {cal['rms_px']} px ({quality}), {cal['views_used']} views")
    print(f"Measured horizontal FOV: {cal['hfov_deg']} deg")
    block = {"calibration": {k: cal[k] for k in ("model", "width", "height", "camera_matrix", "dist_coeffs")}}
    with open(args.out, "w") as fh:
        yaml.safe_dump(block, fh, sort_keys=False, default_flow_style=None)
    print(f"\nPaste this under `georef:` in config.yaml (also saved to {args.out}):\n")
    print("\n".join("  " + ln for ln in yaml.safe_dump(block, sort_keys=False,
                                                       default_flow_style=None).splitlines()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
