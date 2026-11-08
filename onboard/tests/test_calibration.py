"""The calibration tool must recover a known lens from synthetic photos.

Checkerboard views are rendered through a known camera matrix + barrel
distortion, then tools/calibrate_camera.calibrate() must get them back, and the
result must plug into GeoReferencer.
"""

import math
import os
import sys

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
from calibrate_camera import calibrate, find_corners  # noqa: E402

from silm_recon.georef import GeoReferencer, undistort_points  # noqa: E402

W, H = 640, 480
K_TRUE = np.array([[420.0, 0, 322.0], [0, 421.0, 238.0], [0, 0, 1]])
D_TRUE = np.array([-0.28, 0.09, 0.0005, -0.0004, -0.012])
COLS, ROWS, SQ_PX, SQ_M = 9, 6, 60, 0.025


def board_texture():
    margin = SQ_PX
    tw, th = (COLS + 1) * SQ_PX + 2 * margin, (ROWS + 1) * SQ_PX + 2 * margin
    tex = np.full((th, tw), 255, np.uint8)
    for r in range(ROWS + 1):
        for c in range(COLS + 1):
            if (r + c) % 2 == 0:
                y0, x0 = margin + r * SQ_PX, margin + c * SQ_PX
                tex[y0:y0 + SQ_PX, x0:x0 + SQ_PX] = 0
    # texture px -> board metres, inner corner (0,0) at (margin+SQ, margin+SQ)
    off = margin + SQ_PX
    A = np.array([[SQ_M / SQ_PX, 0, -off * SQ_M / SQ_PX],
                  [0, SQ_M / SQ_PX, -off * SQ_M / SQ_PX],
                  [0, 0, 1]])
    return tex, A


PAD = 400  # barrel distortion: frame corners come from outside the ideal frame


def distort_map():
    """For each distorted output pixel, where it comes from in the ideal image
    (which is rendered on a canvas padded by PAD on every side)."""
    xs, ys = np.meshgrid(np.arange(W, dtype=np.float64), np.arange(H, dtype=np.float64))
    pts = np.stack([xs.ravel(), ys.ravel()], axis=1).reshape(-1, 1, 2)
    und = undistort_points(pts, K_TRUE, D_TRUE, P=K_TRUE).reshape(H, W, 2)
    return (und[..., 0] + PAD).astype(np.float32), (und[..., 1] + PAD).astype(np.float32)


def render_views(n=24, seed=0):
    """Render n views in which the whole board is visible, spread over the
    frame including its corners and edges (as the tool tells users to do)."""
    rng = np.random.default_rng(seed)
    tex, A = board_texture()
    mx, my = distort_map()
    cx_board, cy_board = (COLS - 1) * SQ_M / 2, (ROWS - 1) * SQ_M / 2
    # aim points across a 5x4 grid of the frame, then random extras
    aims = [(x, y) for x in np.linspace(0.12, 0.88, 5) for y in np.linspace(0.15, 0.85, 4)]
    views = []
    tries = 0
    while len(views) < n and tries < 2000:
        tries += 1
        ax, ay = aims[len(views)] if len(views) < len(aims) else rng.uniform(0.1, 0.9, 2)
        rvec = rng.uniform(-0.45, 0.45, 3) * np.array([1, 1, 0.4])
        R, _ = cv2.Rodrigues(rvec)
        z = rng.uniform(0.35, 0.55)
        ray = np.linalg.inv(K_TRUE) @ np.array([ax * W, ay * H, 1.0])
        target = ray / ray[2] * z
        t = target - R @ np.array([cx_board, cy_board, 0])
        shift = np.array([[1, 0, PAD], [0, 1, PAD], [0, 0, 1.0]])
        Hm = shift @ K_TRUE @ np.column_stack([R[:, 0], R[:, 1], t]) @ A
        ideal = cv2.warpPerspective(tex, Hm, (W + 2 * PAD, H + 2 * PAD), borderValue=127)
        img = cv2.remap(ideal, mx, my, cv2.INTER_LINEAR, borderValue=127)
        if find_corners(img, COLS, ROWS) is None:
            continue  # board partly out of frame: a real user would retake it
        views.append(cv2.cvtColor(img, cv2.COLOR_GRAY2BGR))
    return views


@pytest.fixture(scope="module")
def cal():
    return calibrate(render_views(), COLS, ROWS, SQ_M)


def test_recovers_focal_length_and_centre(cal):
    K = np.array(cal["camera_matrix"])
    assert K[0, 0] == pytest.approx(K_TRUE[0, 0], rel=0.02)
    assert K[1, 1] == pytest.approx(K_TRUE[1, 1], rel=0.02)
    assert K[0, 2] == pytest.approx(K_TRUE[0, 2], abs=6)
    assert K[1, 2] == pytest.approx(K_TRUE[1, 2], abs=6)
    assert cal["rms_px"] < 0.5
    assert (cal["width"], cal["height"]) == (W, H)


def test_recovered_lens_undistorts_like_the_true_lens(cal):
    """Compare what matters: the ray each pixel maps to, across the frame."""
    true_g = GeoReferencer({"calibration": {"width": W, "height": H,
                                            "camera_matrix": K_TRUE.tolist(),
                                            "dist_coeffs": D_TRUE.tolist()}}, (W, H))
    got_g = GeoReferencer({"calibration": {k: cal[k] for k in
                                           ("width", "height", "camera_matrix", "dist_coeffs")}},
                          (W, H))
    worst = 0.0
    for u in np.linspace(0.05, 0.95, 7):
        for v in np.linspace(0.05, 0.95, 7):
            a, b = true_g.pixel_ray_cam(u, v), got_g.pixel_ray_cam(u, v)
            worst = max(worst, math.degrees(math.acos(min(1.0, float(a @ b)))))
    assert worst < 0.3, f"worst ray error {worst:.3f} deg"


def test_hfov_reported(cal):
    expected = math.degrees(2 * math.atan(W / (2 * K_TRUE[0, 0])))
    assert cal["hfov_deg"] == pytest.approx(expected, abs=1.0)


def test_too_few_views_is_an_error():
    with pytest.raises(ValueError):
        calibrate(render_views(n=3, seed=5), COLS, ROWS, SQ_M)


def test_views_cover_the_frame():
    pts = np.concatenate([find_corners(cv2.cvtColor(v, cv2.COLOR_BGR2GRAY), COLS, ROWS).reshape(-1, 2)
                          for v in render_views()])
    assert pts[:, 0].min() < 0.08 * W and pts[:, 0].max() > 0.92 * W
    assert pts[:, 1].min() < 0.08 * H and pts[:, 1].max() > 0.92 * H
