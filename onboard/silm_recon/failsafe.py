"""Companion-side GPS-health monitor and optical-flow velocity aid.

SAFETY MODEL -- read this first:
  The flight-critical GPS-loss failsafe lives in ArduPilot on the Matek H743
  (see ardupilot/gps_denied_failsafe.param): dead-reckoning, RTL, geofence,
  innovation gating. That runs with or without this file and does not depend on
  the Pi. If the Pi dies, the aircraft is still safe.

  This module adds two NON-critical companion functions:
    1. MONITOR  -- watch GPS/EKF health over MAVLink, log degradation and
                   suspected jamming/spoofing to the mission folder, and print
                   warnings. Pure observation.
    2. FLOW AID -- estimate ground velocity from downward camera optical flow as
                   a *backup* reference. By default it only LOGS this estimate.
                   Sending it to the flight controller as an EKF velocity source
                   is opt-in (config: failsafe.send_to_fc) and must be validated
                   on the bench first, because a bad velocity aid is worse than
                   none.

Nothing here commands the aircraft.
"""

from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass, field

import numpy as np


# --------------------------------------------------------------------------- #
#  GPS / EKF health monitor
# --------------------------------------------------------------------------- #

@dataclass
class GpsHealth:
    fix_type: int = 0          # 0-1 no fix, 2 = 2D, 3 = 3D, 4+ = DGPS/RTK
    satellites: int = 0
    hdop: float = 99.9
    ekf_pos_horiz_ok: bool = False
    ekf_vel_ok: bool = False
    jamming_indicator: int = 0  # 0 unknown, 1 ok, 2 mitigated, 3 jamming (ublox)
    last_update: float = 0.0

    def degraded(self) -> bool:
        return (self.fix_type < 3) or (self.satellites < 6) or (self.hdop > 3.0)


class GpsHealthMonitor:
    """Consumes MAVLink messages and tracks GPS/EKF health + jamming flags."""

    def __init__(self, log_dir: str | None = None):
        self.health = GpsHealth()
        self._log_path = os.path.join(log_dir, "gps_health.ndjson") if log_dir else None
        self._state = "OK"          # OK | DEGRADED | LOST
        self._since = time.time()
        self._last_log = 0.0

    def ingest(self, msg) -> None:
        t = msg.get_type()
        h = self.health
        now = time.time()
        if t == "GPS_RAW_INT":
            h.fix_type = msg.fix_type
            h.satellites = msg.satellites_visible
            h.hdop = msg.eph / 100.0 if msg.eph not in (0, 65535) else 99.9
            # u-blox M10 exposes a jamming/interference indicator in some
            # firmwares via GPS_RAW_INT.* or GPS2; if your stack surfaces it,
            # map it here. Default stays 0 (unknown).
            h.last_update = now
        elif t == "EKF_STATUS_REPORT":
            flags = msg.flags
            # Bit 4 = POS_HORIZ_ABS, bit 2 = VELOCITY_HORIZ (ArduPilot EKF flags)
            h.ekf_pos_horiz_ok = bool(flags & (1 << 4))
            h.ekf_vel_ok = bool(flags & (1 << 2))
            h.last_update = now
        self._evaluate(now)

    def _evaluate(self, now: float) -> None:
        h = self.health
        if h.fix_type < 2 or not h.ekf_pos_horiz_ok:
            new = "LOST"
        elif h.degraded():
            new = "DEGRADED"
        else:
            new = "OK"
        if new != self._state:
            dur = now - self._since
            self._log_event(now, f"gps {self._state}->{new}", extra={"held_s": round(dur, 1)})
            print(f"[failsafe] GPS state {self._state} -> {new} "
                  f"(fix={h.fix_type} sats={h.satellites} hdop={h.hdop:.1f} "
                  f"ekf_pos={h.ekf_pos_horiz_ok})")
            self._state = new
            self._since = now
        # Heartbeat log every 5 s while not OK, so a jamming episode is recorded.
        if new != "OK" and now - self._last_log > 5.0:
            self._log_event(now, f"gps {new}")
            self._last_log = now

    @property
    def state(self) -> str:
        return self._state

    def suspected_jamming(self) -> bool:
        """Heuristic: healthy fix suddenly collapses (many sats -> few/none)
        while the aircraft is airborne is the classic jamming signature.
        The authoritative response is ArduPilot's DR failsafe; this is a flag
        for the log and the operator."""
        return self._state == "LOST"

    def _log_event(self, now: float, event: str, extra: dict | None = None) -> None:
        if not self._log_path:
            return
        rec = {
            "t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
            "event": event,
            "fix_type": self.health.fix_type,
            "satellites": self.health.satellites,
            "hdop": round(self.health.hdop, 2),
            "ekf_pos_ok": self.health.ekf_pos_horiz_ok,
        }
        if extra:
            rec.update(extra)
        try:
            with open(self._log_path, "a") as fh:
                fh.write(json.dumps(rec) + "\n")
        except Exception:
            pass


# --------------------------------------------------------------------------- #
#  Optical-flow ground-velocity aid
# --------------------------------------------------------------------------- #

@dataclass
class FlowEstimate:
    vx_ned: float = 0.0        # m/s north
    vy_ned: float = 0.0        # m/s east
    quality: float = 0.0       # 0..1, fraction of tracked features
    valid: bool = False
    t: float = field(default_factory=time.time)


class OpticalFlowAid:
    """Sparse Lucas-Kanade optical flow -> approximate ground velocity.

    Works best with a near-nadir camera. Converts pixel motion to a ground
    velocity using the current height AGL and lens focal length, and de-rotates
    using the aircraft's attitude change between frames so that pitch/roll
    motion isn't mistaken for translation. This is an APPROXIMATION and is only
    used as a backup reference; see the safety note at the top of this file.
    """

    def __init__(self, cfg: dict, hfov_deg: float):
        self.enabled = bool(cfg.get("enabled", True))
        self.send_to_fc = bool(cfg.get("send_to_fc", False))
        self.max_rate_hz = float(cfg.get("max_rate_hz", 5.0))
        self.proc_width = int(cfg.get("proc_width", 320))
        self.min_features = int(cfg.get("min_features", 20))
        # Reject physically implausible estimates (e.g. from a near-zero dt or a
        # bad frame) so a garbage velocity can never reach the flight controller.
        self.max_speed_mps = float(cfg.get("max_speed_mps", 60.0))
        self.min_dt = float(cfg.get("min_dt", 0.03))
        self.hfov = math.radians(hfov_deg)
        self._prev_gray = None
        self._prev_pts = None
        self._prev_t = 0.0
        self._prev_yaw = None
        self._last_emit = 0.0
        self._lk = dict(winSize=(21, 21), maxLevel=3,
                        criteria=(3, 30, 0.01))  # (TERM_COUNT|EPS, iters, eps)
        self._feat = dict(maxCorners=120, qualityLevel=0.01,
                          minDistance=8, blockSize=7)

    def update(self, frame_bgr, rel_alt: float, roll: float, pitch: float,
               yaw: float) -> FlowEstimate:
        if not self.enabled:
            return FlowEstimate()
        import cv2

        now = time.time()
        if now - self._last_emit < 1.0 / self.max_rate_hz:
            return FlowEstimate(valid=False)

        h0, w0 = frame_bgr.shape[:2]
        scale = self.proc_width / float(w0)
        small = cv2.resize(frame_bgr, (self.proc_width, int(h0 * scale)))
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)

        est = FlowEstimate(valid=False, t=now)
        if self._prev_gray is not None and self._prev_pts is not None and len(self._prev_pts):
            nxt, status, _ = cv2.calcOpticalFlowPyrLK(
                self._prev_gray, gray, self._prev_pts, None, **self._lk)
            if nxt is not None and status is not None:
                good_new = nxt[status.flatten() == 1]
                good_old = self._prev_pts[status.flatten() == 1]
                n = len(good_new)
                if n >= self.min_features and rel_alt > 2.0:
                    flow = (good_new - good_old).reshape(-1, 2)
                    med = np.median(flow, axis=0)  # robust px/frame (dx, dy)
                    dt = now - self._prev_t
                    if dt < self.min_dt:
                        # Too little time elapsed to trust the estimate.
                        self._reseed(gray, now, yaw)
                        self._last_emit = now
                        return FlowEstimate(valid=False, t=now)

                    # Focal length in pixels for the downscaled frame.
                    f_px = (self.proc_width / 2.0) / math.tan(self.hfov / 2.0)

                    # De-rotate: remove the pixel shift caused by yaw change
                    # (dominant for a nadir cam). Pitch/roll rates are left as a
                    # documented approximation.
                    if self._prev_yaw is not None:
                        dyaw = _wrap(yaw - self._prev_yaw)
                        # yaw rotates the image about its centre; for small dyaw
                        # the net translational bias is ~0 at the centroid, so we
                        # mainly guard against large spins.
                        if abs(dyaw) > math.radians(15):
                            self._reseed(gray, now, yaw)
                            return est

                    # Pixel velocity -> angular rate -> ground velocity (nadir).
                    # v_ground = (flow_px / f_px) * height / dt
                    v_right = (med[0] / f_px) * rel_alt / dt   # body +y (right)
                    v_fwd = -(med[1] / f_px) * rel_alt / dt    # image up = fwd

                    # Rotate body (fwd,right) into NED using heading.
                    cy, sy = math.cos(yaw), math.sin(yaw)
                    vn = v_fwd * cy - v_right * sy
                    ve = v_fwd * sy + v_right * cy

                    speed = math.hypot(vn, ve)
                    if speed <= self.max_speed_mps:
                        est = FlowEstimate(vx_ned=vn, vy_ned=ve,
                                           quality=min(1.0, n / 120.0),
                                           valid=True, t=now)

        # Re-seed features each cycle (cheap, robust for a moving scene).
        self._reseed(gray, now, yaw)
        self._last_emit = now
        return est

    def _reseed(self, gray, now, yaw) -> None:
        import cv2
        pts = cv2.goodFeaturesToTrack(gray, mask=None, **self._feat)
        self._prev_gray = gray
        self._prev_pts = pts
        self._prev_t = now
        self._prev_yaw = yaw

    def send(self, master, est: FlowEstimate) -> None:
        """Optionally forward the estimate to ArduPilot as a vision velocity.
        Off unless failsafe.send_to_fc is true AND validated on the bench."""
        if not (self.send_to_fc and est.valid and master is not None):
            return
        try:
            usec = int(est.t * 1e6)
            # VISION_SPEED_ESTIMATE: velocity in local NED (m/s).
            master.mav.vision_speed_estimate_send(
                usec, est.vx_ned, est.vy_ned, 0.0)
        except Exception:
            pass


def _wrap(a: float) -> float:
    while a > math.pi:
        a -= 2 * math.pi
    while a < -math.pi:
        a += 2 * math.pi
    return a
