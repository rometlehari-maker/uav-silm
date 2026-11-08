"""Geometry tests: geo-referencing and the optical-flow velocity aid.

Everything is checked against an independent forward model: a flat-ground
point is projected into the image with GeoReferencer.ground_to_pixel (plain
rotation matrices + pinhole), and the code under test must invert it.
"""

import math

import numpy as np
import pytest

from silm_recon.failsafe import OpticalFlowAid, flow_to_velocity
from silm_recon.georef import GeoReferencer

LAT, LON = 58.3780, 24.5000
M_PER_DEG_LAT = 111320.0


def ne_offset(gp):
    n = (gp.lat - LAT) * M_PER_DEG_LAT
    e = (gp.lon - LON) * M_PER_DEG_LAT * math.cos(math.radians(LAT))
    return n, e


# ----------------------------------------------------------------- georef --

def test_centre_pixel_is_directly_below_when_level():
    g = GeoReferencer({"hfov_deg": 86}, (1280, 720))
    n, e = ne_offset(g.project(0.5, 0.5, LAT, LON, 100, 0, 0, 0))
    assert abs(n) < 0.01 and abs(e) < 0.01


def test_edges_match_fov_on_16x9_frame():
    g = GeoReferencer({"hfov_deg": 86}, (1280, 720))
    # right edge -> east by h*tan(hfov/2)
    _, e = ne_offset(g.project(1.0, 0.5, LAT, LON, 100, 0, 0, 0))
    assert e == pytest.approx(100 * math.tan(math.radians(43)), rel=1e-3)
    # top edge -> north by h*tan(vfov/2), where tan(vfov/2) = (H/2)/fx.
    # (The old code treated the frame as square and got 93 m here.)
    n, _ = ne_offset(g.project(0.5, 0.0, LAT, LON, 100, 0, 0, 0))
    expected = 100 * (360 / g.fy)
    assert n == pytest.approx(expected, rel=1e-3)
    assert n < 60  # 16:9 -> much less than the horizontal half-width


def test_heading_rotates_footprint():
    g = GeoReferencer({"hfov_deg": 86}, (1280, 720))
    n, e = ne_offset(g.project(0.5, 0.0, LAT, LON, 100, 0, 0, math.radians(90)))
    assert abs(n) < 0.05 and e > 50  # image top points east when heading east


@pytest.mark.parametrize("tilt", [90, 70, 45])
@pytest.mark.parametrize("att", [(0, 0, 0), (0.2, -0.1, 1.0), (-0.3, 0.15, -2.5)])
def test_round_trip_pixel_ground_pixel(tilt, att):
    g = GeoReferencer({"hfov_deg": 86, "forward_tilt_deg": tilt}, (1280, 720))
    roll, pitch, yaw = att
    for u, v in [(0.5, 0.5), (0.2, 0.7), (0.8, 0.9), (0.6, 0.6)]:
        gp = g.project(u, v, LAT, LON, 100, roll, pitch, yaw)
        if not gp.valid:
            continue
        n, e = ne_offset(gp)
        u2, v2 = g.ground_to_pixel(n, e, 100, roll, pitch, yaw)
        assert u2 == pytest.approx(u, abs=2e-4) and v2 == pytest.approx(v, abs=2e-4)


def test_ray_above_horizon_is_invalid():
    g = GeoReferencer({"hfov_deg": 86, "forward_tilt_deg": 10}, (1280, 720))
    assert not g.project(0.5, 0.0, LAT, LON, 100, 0, math.radians(15), 0).valid


def test_lens_distortion_is_removed():
    cv2 = pytest.importorskip("cv2")
    W, H = 1280, 720
    K = [[640.0, 0, 650.0], [0, 645.0, 355.0], [0, 0, 1]]
    D = [-0.32, 0.12, 0.001, -0.0005, -0.02]  # strong wide-angle barrel
    g = GeoReferencer({"calibration": {"width": W, "height": H,
                                       "camera_matrix": K, "dist_coeffs": D}}, (W, H))
    # A known ray, pushed through the real distortion model to get a pixel...
    ray = np.array([[0.55, -0.31, 1.0]])
    px, _ = cv2.projectPoints(ray, np.zeros(3), np.zeros(3), np.array(K), np.array(D))
    u, v = px[0, 0, 0] / W, px[0, 0, 1] / H
    # ...must come back as the same ray.
    got = g.pixel_ray_cam(u, v)
    want = ray[0] / np.linalg.norm(ray[0])
    assert np.allclose(got, want, atol=1e-4)


def test_calibration_scales_with_resolution():
    K = [[1000.0, 0, 960.0], [0, 1000.0, 540.0], [0, 0, 1]]
    g = GeoReferencer({"calibration": {"width": 1920, "height": 1080,
                                       "camera_matrix": K, "dist_coeffs": [0, 0, 0, 0, 0]}},
                      (1280, 720))
    assert g.fx == pytest.approx(1000 * 1280 / 1920)
    assert g.cx == pytest.approx(640)


# ------------------------------------------------------------ flow maths --

W, H, PROC_W = 1280, 720, 320
PROC_H = PROC_W * H / W


def synthetic_median_flow(g, alt, p1, att1, p2, att2):
    """Median image shift (proc-res pixels) of ground points seen near the
    image centre, moving from pose 1 to pose 2."""
    dxs, dys = [], []
    for n in np.linspace(-15, 15, 9):
        for e in np.linspace(-15, 15, 9):
            a = g.ground_to_pixel(n - p1[0], e - p1[1], alt, *att1)
            b = g.ground_to_pixel(n - p2[0], e - p2[1], alt, *att2)
            if a is None or b is None:
                continue
            dxs.append((b[0] - a[0]) * PROC_W)
            dys.append((b[1] - a[1]) * PROC_H)
    return float(np.median(dxs)), float(np.median(dys))


@pytest.mark.parametrize("vel", [(19, 0), (0, 19), (-12, 8), (13.4, -13.4)])
@pytest.mark.parametrize("yaw", [0.0, 1.2, -2.4])
@pytest.mark.parametrize("drot", [(0, 0), (0.035, 0), (0, -0.035), (-0.04, 0.03)])
def test_flow_to_velocity_recovers_motion_and_removes_rotation(vel, yaw, drot):
    g = GeoReferencer({"hfov_deg": 86}, (W, H))
    alt, dt = 60.0, 0.2
    vn, ve = vel
    droll, dpitch = drot
    att1 = (0.0, 0.0, yaw)
    att2 = (droll, dpitch, yaw)
    p2 = (vn * dt, ve * dt)
    dx, dy = synthetic_median_flow(g, alt, (0, 0), att1, p2, att2)
    f_px = g.fx / W * PROC_W
    got_n, got_e = flow_to_velocity(dx, dy, f_px, alt, dt, droll, dpitch, yaw)
    assert got_n == pytest.approx(vn, abs=1.0)
    assert got_e == pytest.approx(ve, abs=1.0)


def test_rotation_compensation_matters():
    """A 2 degree pitch change in 0.2 s with no movement at all: without the
    compensation it would read as ~10 m/s; with it, ~0."""
    g = GeoReferencer({"hfov_deg": 86}, (W, H))
    alt, dt, dp = 60.0, 0.2, math.radians(2)
    dx, dy = synthetic_median_flow(g, alt, (0, 0), (0, 0, 0), (0, 0), (0, dp, 0))
    f_px = g.fx / W * PROC_W
    raw = flow_to_velocity(dx, dy, f_px, alt, dt)
    comp = flow_to_velocity(dx, dy, f_px, alt, dt, dpitch=dp)
    assert math.hypot(*raw) > 5.0
    assert math.hypot(*comp) < 0.5


# ------------------------------------------- end-to-end through OpenCV LK --

def render_ground(g, texture, m_per_px, alt, pos, att):
    """Render what the camera sees of a flat textured ground (homography)."""
    cv2 = pytest.importorskip("cv2")
    th, tw = texture.shape[:2]
    r0, c0 = th / 2, tw / 2
    src, dst = [], []
    for n, e in [(-20, -30), (-20, 30), (20, 30), (20, -30)]:
        uv = g.ground_to_pixel(n - pos[0], e - pos[1], alt, *att)
        src.append([c0 + e / m_per_px, r0 - n / m_per_px])
        dst.append([uv[0] * g.W, uv[1] * g.H])
    Hm = cv2.getPerspectiveTransform(np.float32(src), np.float32(dst))
    return cv2.warpPerspective(texture, Hm, (g.W, g.H))


@pytest.mark.parametrize("case", [
    dict(v=(19, 0), yaw=0.0, drot=(0, 0)),
    dict(v=(0, -15), yaw=0.0, drot=(0, 0)),
    dict(v=(14, 10), yaw=0.9, drot=(0.03, -0.02)),
])
def test_optical_flow_aid_end_to_end(case):
    cv2 = pytest.importorskip("cv2")
    rng = np.random.default_rng(3)
    noise = rng.random((1600, 1600)).astype(np.float32)
    tex = cv2.GaussianBlur(noise, (0, 0), 2.0)
    tex = cv2.normalize(tex, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    tex = cv2.cvtColor(tex, cv2.COLOR_GRAY2BGR)
    m_per_px = 0.1  # 160 m x 160 m of ground

    g = GeoReferencer({"hfov_deg": 86}, (W, H))
    alt, dt = 40.0, 0.2
    yaw = case["yaw"]
    droll, dpitch = case["drot"]
    vn, ve = case["v"]
    att1, att2 = (0.0, 0.0, yaw), (droll, dpitch, yaw)
    f1 = render_ground(g, tex, m_per_px, alt, (0, 0), att1)
    f2 = render_ground(g, tex, m_per_px, alt, (vn * dt, ve * dt), att2)

    aid = OpticalFlowAid({"enabled": True, "max_rate_hz": 100, "proc_width": PROC_W},
                         fx_over_width=g.fx / W)
    assert not aid.update(f1, alt, *att1, now=100.0).valid   # first frame primes
    est = aid.update(f2, alt, *att2, now=100.0 + dt)
    assert est.valid
    assert est.vx_ned == pytest.approx(vn, abs=1.5)
    assert est.vy_ned == pytest.approx(ve, abs=1.5)
