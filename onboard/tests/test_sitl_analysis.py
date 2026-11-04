"""The SITL grading logic, on synthetic flight tracks."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
from sitl_failsafe_test import MODE_AUTO, MODE_RTL, analyse, dist_m, offset  # noqa: E402

HOME = (59.40, 24.70)


def track(segments):
    """segments: list of (duration_s, start_n, start_e, end_n, end_e, mode, est_err_m)."""
    rows, t = [], 0.0
    for dur, n0, e0, n1, e1, mode, err in segments:
        steps = int(dur / 0.5)
        for i in range(steps):
            f = i / steps
            la, lo = offset(*HOME, n0 + f * (n1 - n0), e0 + f * (e1 - e0))
            ela, elo = offset(la, lo, err, 0)
            rows.append({"t": t, "true_lat": la, "true_lon": lo, "est_lat": ela,
                         "est_lon": elo, "mode": mode})
            t += 0.5
    return rows


def good_flight():
    return track([
        (60, 0, 0, 700, 0, MODE_AUTO, 1),       # outbound
        (6, 700, 0, 790, 0, MODE_AUTO, 5),      # GPS lost at t=60, still AUTO 6 s
        (84, 790, 0, 100, 0, MODE_RTL, 40),     # RTL on dead reckoning, 40 m error
        (20, 100, 0, 30, 0, MODE_RTL, 8),       # GPS back at t=150, reconverges
        (70, 30, 0, 50, 40, MODE_RTL, 2),       # loiter at home
    ])


def test_good_flight_passes():
    r = analyse(good_flight(), fault_t=60, restore_t=150, home=HOME)
    assert r["passed"], r["checks"]
    assert r["rtl_after_s"] == 6.0
    assert r["dead_reckoning_error_max_m"] >= 40


def test_no_rtl_fails():
    rows = good_flight()
    for row in rows:
        row["mode"] = MODE_AUTO
    r = analyse(rows, 60, 150, HOME)
    assert not r["checks"]["rtl_in_time"] and not r["passed"]


def test_flying_away_while_blind_fails():
    rows = track([(60, 0, 0, 700, 0, MODE_AUTO, 1), (90, 700, 0, 1400, 0, MODE_RTL, 50),
                  (60, 1400, 0, 1400, 0, MODE_RTL, 3)])
    r = analyse(rows, 60, 150, HOME, fence_radius=1500)
    assert not r["checks"]["headed_home_while_blind"]
    assert not r["checks"]["ended_near_home"]


def test_fence_breach_fails():
    rows = track([(60, 0, 0, 1600, 0, MODE_AUTO, 1), (90, 1600, 0, 100, 0, MODE_RTL, 20),
                  (60, 100, 0, 50, 0, MODE_RTL, 2)])
    r = analyse(rows, 60, 150, HOME, fence_radius=1500)
    assert not r["checks"]["stayed_inside_fence"]


def test_estimate_never_reconverging_fails():
    rows = good_flight()
    for row in rows:
        if row["t"] >= 150:
            row["est_lat"], row["est_lon"] = offset(row["true_lat"], row["true_lon"], 60, 0)
    r = analyse(rows, 60, 150, HOME)
    assert not r["checks"]["estimate_reconverged"]


def test_geometry_helpers():
    la, lo = offset(*HOME, 300, 400)
    assert abs(dist_m(*HOME, la, lo) - 500) < 0.5
