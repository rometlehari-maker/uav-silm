"""Geo-referencing.

Project an image pixel to a ground latitude/longitude using the aircraft's GPS
position, barometric/GPS altitude above the ground, attitude (roll/pitch/yaw)
and the camera's mounting and field of view. This turns "there is a combine at
pixel (812, 430)" into "there is a combine at 58.41732 N, 24.52031 E".

Assumptions (documented so the error budget is honest):
  * Flat local ground at the aircraft's launch elevation (``rel_alt`` is AGL).
    Over rolling terrain this introduces position error; a DEM lookup can be
    added later via ``ground_elevation_m``.
  * Pinhole camera, no lens distortion correction. The SIYI A2 Mini is fairly
    wide, so expect larger error toward the frame edges. Calibrate HFOV for best
    results.
  * Local tangent-plane (flat-Earth) conversion of metres to degrees. Fine over
    the few-km scale of one mission.

Accuracy is typically good to a few metres near the image centre at 80-120 m
AGL with a correct HFOV and attitude -- plenty to find the object again, not a
survey-grade fix.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

_EARTH_R = 6378137.0  # m, WGS-84 equatorial radius (local-tangent use)


def undistort_points(pts, K, D, P=None):
    """cv2.undistortPoints with enough iterations for strong wide-angle lenses
    (the default 5 leaves ~0.2 px error in the corners). Works on OpenCV 4
    (undistortPointsIter) and OpenCV 5 (criteria argument)."""
    import cv2
    crit = (cv2.TERM_CRITERIA_COUNT | cv2.TERM_CRITERIA_EPS, 100, 1e-9)
    if hasattr(cv2, "undistortPointsIter"):
        return cv2.undistortPointsIter(pts, K, D, None, P, crit)
    try:
        return cv2.undistortPoints(pts, K, D, None, None, P, crit)
    except TypeError:
        return cv2.undistortPoints(pts, K, D, None, P)


@dataclass
class GroundPoint:
    lat: float
    lon: float
    slant_range: float  # m from camera to ground point
    valid: bool


class GeoReferencer:
    def __init__(self, cfg: dict, image_size: tuple[int, int] | None = None):
        # Horizontal field of view of the lens, degrees (used when no
        # calibration is given).
        self.hfov = math.radians(float(cfg.get("hfov_deg", 86.0)))

        # Image size in pixels. Needed so vertical angles are right for
        # non-square (e.g. 16:9) frames.
        if image_size is None:
            image_size = (int(cfg.get("image_width", 1280)), int(cfg.get("image_height", 720)))
        self.W, self.H = int(image_size[0]), int(image_size[1])

        # Intrinsics. Either from a calibration (tools/calibrate_camera.py),
        # scaled to the current resolution, or derived from hfov with square
        # pixels and a centred principal point.
        self.dist = None
        calib = cfg.get("calibration")
        if calib:
            sx = self.W / float(calib["width"])
            sy = self.H / float(calib["height"])
            K = np.array(calib["camera_matrix"], dtype=np.float64)
            self.fx, self.fy = K[0, 0] * sx, K[1, 1] * sy
            self.cx, self.cy = K[0, 2] * sx, K[1, 2] * sy
            d = calib.get("dist_coeffs")
            if d is not None:
                self.dist = np.array(d, dtype=np.float64).reshape(-1)
        else:
            self.fx = self.fy = (self.W / 2.0) / math.tan(self.hfov / 2.0)
            self.cx, self.cy = self.W / 2.0, self.H / 2.0
        self.K = np.array([[self.fx, 0, self.cx], [0, self.fy, self.cy], [0, 0, 1.0]])
        # Camera mounting relative to the airframe body (FRD) frame.
        #   forward_tilt_deg = angle the optical axis is tilted down from the
        #   nose-forward direction. 90 = straight down (nadir), 0 = horizon.
        self.forward_tilt = math.radians(float(cfg.get("forward_tilt_deg", 90.0)))
        self.yaw_offset = math.radians(float(cfg.get("yaw_offset_deg", 0.0)))
        self.roll_offset = math.radians(float(cfg.get("roll_offset_deg", 0.0)))
        self.min_down = float(cfg.get("min_down_component", 0.05))
        self.ground_elevation_m = cfg.get("ground_elevation_m", None)

    # ------------------------------------------------------------------ #

    def _R_body_from_cam(self) -> np.ndarray:
        """Rotation taking a camera-optical vector into the body (FRD) frame.

        Optical frame: x=right, y=down-in-image, z=forward along the lens.
        Start from a nadir mount (lens pointing down, image-top = nose) and then
        tilt the lens up toward the nose by (90deg - forward_tilt).
        """
        # Nadir: optical x->body +y(right), optical y->body -x(aft), z->body +z(down)
        R_nadir = np.array([
            [0.0, -1.0, 0.0],
            [1.0, 0.0, 0.0],
            [0.0, 0.0, 1.0],
        ])
        # Tilt about body y-axis so the lens looks forward-down.
        a = (math.pi / 2.0) - self.forward_tilt  # 0 at nadir
        ca, sa = math.cos(a), math.sin(a)
        R_tilt = np.array([
            [ca, 0.0, sa],
            [0.0, 1.0, 0.0],
            [-sa, 0.0, ca],
        ])
        # Optional fixed yaw / roll mount offsets.
        cy, sy = math.cos(self.yaw_offset), math.sin(self.yaw_offset)
        R_yaw = np.array([[cy, -sy, 0], [sy, cy, 0], [0, 0, 1]])
        cr, sr = math.cos(self.roll_offset), math.sin(self.roll_offset)
        R_roll = np.array([[1, 0, 0], [0, cr, -sr], [0, sr, cr]])
        return R_yaw @ R_roll @ R_tilt @ R_nadir

    @staticmethod
    def _R_ned_from_body(roll: float, pitch: float, yaw: float) -> np.ndarray:
        """Standard aerospace 3-2-1 (yaw, pitch, roll) body->NED."""
        cphi, sphi = math.cos(roll), math.sin(roll)
        cth, sth = math.cos(pitch), math.sin(pitch)
        cpsi, spsi = math.cos(yaw), math.sin(yaw)
        return np.array([
            [cth * cpsi, sphi * sth * cpsi - cphi * spsi, cphi * sth * cpsi + sphi * spsi],
            [cth * spsi, sphi * sth * spsi + cphi * cpsi, cphi * sth * spsi - sphi * cpsi],
            [-sth,       sphi * cth,                        cphi * cth],
        ])

    def pixel_ray_cam(self, u_norm: float, v_norm: float) -> np.ndarray:
        """Unit ray in the camera-optical frame for a normalised pixel [0,1]
        (u across the width, v down the height)."""
        px, py = u_norm * self.W, v_norm * self.H
        if self.dist is not None and np.any(self.dist):
            pt = np.array([[[px, py]]], dtype=np.float64)
            x, y = undistort_points(pt, self.K, self.dist)[0, 0]
        else:
            x = (px - self.cx) / self.fx
            y = (py - self.cy) / self.fy
        v = np.array([x, y, 1.0])
        return v / np.linalg.norm(v)

    def ground_to_pixel(self, north: float, east: float, rel_alt: float,
                        roll: float, pitch: float, yaw: float):
        """Inverse projection: where does a flat-ground point, `north`/`east`
        metres from the point below the aircraft, appear in the image?
        Returns normalised (u, v) or None if it's behind the camera.
        (Ignores lens distortion; used for tests and overlays.)"""
        d_ned = np.array([north, east, rel_alt])
        R = self._R_ned_from_body(roll, pitch, yaw) @ self._R_body_from_cam()
        d_cam = R.T @ d_ned
        if d_cam[2] <= 1e-6:
            return None
        px = self.cx + self.fx * d_cam[0] / d_cam[2]
        py = self.cy + self.fy * d_cam[1] / d_cam[2]
        return px / self.W, py / self.H

    def project(
        self,
        u_norm: float,
        v_norm: float,
        lat: float,
        lon: float,
        rel_alt: float,
        roll: float,
        pitch: float,
        yaw: float,
    ) -> GroundPoint:
        if rel_alt <= 1.0:
            return GroundPoint(lat, lon, 0.0, False)

        ray_cam = self.pixel_ray_cam(u_norm, v_norm)
        R_bc = self._R_body_from_cam()
        R_nb = self._R_ned_from_body(roll, pitch, yaw)
        ray_ned = R_nb @ (R_bc @ ray_cam)  # north, east, down

        down = ray_ned[2]
        if down < self.min_down:
            # Ray points at or above the horizon -> no ground intersection.
            return GroundPoint(lat, lon, 0.0, False)

        t = rel_alt / down  # slant range to flat ground
        north = t * ray_ned[0]
        east = t * ray_ned[1]

        dlat = math.degrees(north / _EARTH_R)
        dlon = math.degrees(east / (_EARTH_R * math.cos(math.radians(lat))))
        return GroundPoint(lat + dlat, lon + dlon, slant_range=t, valid=True)
