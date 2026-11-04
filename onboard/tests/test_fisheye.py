"""Fisheye lens model (the SIYI A2 mini is 160 deg) and gimbal tilt."""

import math
import os
import sys

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
from calibrate_camera import calibrate_points  # noqa: E402

from silm_recon.georef import GeoReferencer  # noqa: E402

LAT, LON = 58.378, 24.5
W, H = 1920, 1080


def ne(gp):
    return ((gp.lat - LAT) * 111320.0,
            (gp.lon - LON) * 111320.0 * math.cos(math.radians(LAT)))


def eq_geo(**kw):
    return GeoReferencer(dict({"lens": "fisheye", "hfov_deg": 160}, **kw), (W, H))


def test_equidistant_edge_and_centre_density():
    g = eq_geo()
    assert g.fx == pytest.approx(960 / math.radians(80))
    n, e = ne(g.project(1.0, 0.5, LAT, LON, 100, 0, 0, 0))
    assert e == pytest.approx(100 * math.tan(math.radians(80)), rel=1e-3)   # 567 m
    n, e = ne(g.project(0.5 + 10 / W, 0.5, LAT, LON, 100, 0, 0, 0))          # 10 px right of centre
    assert e == pytest.approx(10 / g.fx * 100, rel=1e-3)


@pytest.mark.parametrize("tilt", [90, 70])
@pytest.mark.parametrize("att", [(0, 0, 0), (0.25, -0.1, 1.0), (-0.3, 0.15, -2.4)])
def test_equidistant_round_trip(tilt, att):
    g = eq_geo(forward_tilt_deg=tilt)
    for u, v in [(0.5, 0.5), (0.15, 0.8), (0.85, 0.3), (0.6, 0.95)]:
        gp = g.project(u, v, LAT, LON, 100, *att)
        if not gp.valid:
            continue
        u2, v2 = g.ground_to_pixel(*ne(gp), 100, *att)
        assert (u2, v2) == pytest.approx((u, v), abs=2e-4)


K_TRUE = np.array([[690.0, 0, 965.0], [0, 690.0, 535.0], [0, 0, 1]])
D_TRUE = np.array([0.03, -0.01, 0.002, -0.0003])


def synthetic_views(n=24, seed=1):
    rng = np.random.default_rng(seed)
    cols, rows, sq = 9, 6, 0.08
    objp = np.zeros((cols * rows, 3))
    objp[:, :2] = np.mgrid[0:cols, 0:rows].T.reshape(-1, 2) * sq
    centre = np.array([(cols - 1) * sq / 2, (rows - 1) * sq / 2, 0])
    objs, imgs = [], []
    aims = [(x, y) for x in np.linspace(0.08, 0.92, 6) for y in np.linspace(0.12, 0.88, 4)]
    for ax, ay in aims[:n]:
        for _ in range(50):
            rvec = rng.uniform(-0.5, 0.5, 3) * np.array([1, 1, 0.5])
            R, _ = cv2.Rodrigues(rvec)
            # direction to the aimed pixel, through the true fisheye
            ray = cv2.fisheye.undistortPoints(np.array([[[ax * W, ay * H]]]), K_TRUE,
                                              D_TRUE.reshape(4, 1))[0, 0]
            d = np.array([ray[0], ray[1], 1.0])
            d /= np.linalg.norm(d)
            t = d * rng.uniform(0.6, 1.0) - R @ centre
            pts, _ = cv2.fisheye.projectPoints(objp.reshape(-1, 1, 3), rvec, t,
                                               K_TRUE, D_TRUE.reshape(4, 1))
            pts = pts.reshape(-1, 2)
            cam = (R @ objp.T).T + t
            if (cam[:, 2] > 0.1).all() and (pts[:, 0] > 5).all() and (pts[:, 0] < W - 5).all() \
                    and (pts[:, 1] > 5).all() and (pts[:, 1] < H - 5).all():
                imgs.append((pts + rng.normal(0, 0.2, pts.shape)).reshape(-1, 1, 2))
                objs.append(objp)
                break
    return objs, imgs


def test_fisheye_calibration_recovers_lens():
    objs, imgs = synthetic_views()
    assert len(objs) >= 15
    cal = calibrate_points(objs, imgs, (W, H), fisheye=True)
    assert cal["model"] == "fisheye" and cal["rms_px"] < 0.5
    true_g = GeoReferencer({"calibration": {"model": "fisheye", "width": W, "height": H,
                                            "camera_matrix": K_TRUE.tolist(),
                                            "dist_coeffs": D_TRUE.tolist()}}, (W, H))
    got_g = GeoReferencer({"calibration": {k: cal[k] for k in
                                           ("model", "width", "height", "camera_matrix",
                                            "dist_coeffs")}}, (W, H))
    worst = 0.0
    for u in np.linspace(0.05, 0.95, 7):
        for v in np.linspace(0.08, 0.92, 7):
            a, b = true_g.pixel_ray_cam(u, v), got_g.pixel_ray_cam(u, v)
            worst = max(worst, math.degrees(math.acos(min(1.0, float(a @ b)))))
    assert worst < 0.3, f"worst ray error {worst:.3f} deg"
    assert cal["hfov_deg"] > 140


def test_calibrated_fisheye_round_trip():
    g = GeoReferencer({"calibration": {"model": "fisheye", "width": W, "height": H,
                                       "camera_matrix": K_TRUE.tolist(),
                                       "dist_coeffs": D_TRUE.tolist()}}, (W, H))
    for u, v in [(0.5, 0.5), (0.2, 0.3), (0.8, 0.85)]:
        gp = g.project(u, v, LAT, LON, 80, 0.1, -0.05, 0.7)
        u2, v2 = g.ground_to_pixel(*ne(gp), 80, 0.1, -0.05, 0.7)
        assert (u2, v2) == pytest.approx((u, v), abs=5e-4)


def test_gimbal_tilt_override_and_stabilised_pitch():
    g = eq_geo(forward_tilt_deg=45)     # config value ignored when the gimbal reports
    # gimbal says straight down (tilt 90); aircraft pitched 10 deg nose up but
    # the gimbal cancels it -> pass pitch=0: centre is directly below
    n, e = ne(g.project(0.5, 0.5, LAT, LON, 100, 0, 0.0, 0, cam_tilt_deg=90))
    assert abs(n) < 0.01 and abs(e) < 0.01
    # without the gimbal correction the fixed 45 deg mount looks 100 m ahead
    n, _ = ne(g.project(0.5, 0.5, LAT, LON, 100, 0, 0, 0))
    assert n == pytest.approx(100.0, rel=1e-3)
