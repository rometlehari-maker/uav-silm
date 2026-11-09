"""The SITL grading logic, on synthetic flight tracks."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
from sitl_failsafe_test import MODE_AUTO, MODE_RTL, analyse, analyse_normal, dist_m, offset  # noqa: E402

HOME = (59.40, 24.70)


def track(segments, landed=True):
    """segments: list of (duration_s, start_n, start_e, end_n, end_e, mode, est_err_m[, landing])."""
    rows, t = [], 0.0
    for seg in segments:
        dur, n0, e0, n1, e1, mode, err = seg[:7]
        landing = seg[7] if len(seg) > 7 else False
        steps = int(dur / 0.5)
        for i in range(steps):
            f = i / steps
            la, lo = offset(*HOME, n0 + f * (n1 - n0), e0 + f * (e1 - e0))
            ela, elo = offset(la, lo, err, 0)
            rows.append({"t": t, "true_lat": la, "true_lon": lo, "est_lat": ela,
                         "est_lon": elo, "mode": mode, "landing": landing, "armed": True,
                         "alt": 100.0})
            t += 0.5
    if landed:
        rows[-1].update(armed=False, alt=0.3)
    return rows


def good_flight():
    # RTL_AUTOLAND=2: RTL hands over to the mission's DO_LAND_START landing sequence.
    return track([
        (60, 0, 0, 700, 0, MODE_AUTO, 1),             # survey
        (6, 700, 0, 790, 0, MODE_AUTO, 5),            # GPS lost at t=60, still AUTO 6 s
        (2, 790, 0, 780, 0, MODE_RTL, 30),            # RTL ...
        (82, 780, 0, 100, 0, MODE_AUTO, 40, True),    # ... -> landing sequence, dead reckoning
        (20, 100, 0, 30, 0, MODE_AUTO, 8, True),      # GPS back at t=150, reconverges
        (30, 30, 0, -20, 0, MODE_AUTO, 2, True),      # final approach and touchdown
    ])


def test_good_flight_passes():
    r = analyse(good_flight(), fault_t=60, restore_t=150, home=HOME)
    assert r["passed"], r["checks"]
    assert r["rtl_after_s"] == 6.0
    assert r["dead_reckoning_error_max_m"] >= 40


def test_no_rtl_fails():
    rows = good_flight()
    for row in rows:                    # neither RTL nor a jump to the landing sequence
        row["mode"], row["landing"] = MODE_AUTO, False
    r = analyse(rows, 60, 150, HOME)
    assert not r["checks"]["rtl_in_time"] and not r["passed"]


def test_flying_away_while_blind_fails():
    rows = track([(60, 0, 0, 700, 0, MODE_AUTO, 1), (90, 700, 0, 1400, 0, MODE_RTL, 50),
                  (60, 1400, 0, 1400, 0, MODE_RTL, 3)])
    r = analyse(rows, 60, 150, HOME, fence_radius=1500)
    assert not r["checks"]["headed_home_while_blind"]
    assert not r["checks"]["landed_near_touchdown"]


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


def test_failsafe_detected_when_rtl_jumps_straight_to_landing_sequence():
    rows = track([(60, 0, 0, 700, 0, MODE_AUTO, 1), (6, 700, 0, 790, 0, MODE_AUTO, 5),
                  (84, 790, 0, 100, 0, MODE_AUTO, 40, True), (50, 100, 0, 0, 0, MODE_AUTO, 3, True)])
    r = analyse(rows, 60, 150, HOME)
    assert r["checks"]["rtl_in_time"] and r["rtl_after_s"] == 6.0


def test_circling_without_landing_fails():
    rows = track([(60, 0, 0, 700, 0, MODE_AUTO, 1), (90, 700, 0, 50, 0, MODE_RTL, 30),
                  (60, 50, 0, 50, 40, MODE_RTL, 2)], landed=False)
    r = analyse(rows, 60, 150, HOME)
    assert not r["checks"]["landed"] and not r["passed"]


def test_landing_far_from_planned_point_fails():
    land = offset(*HOME, -400, 0)        # planned touchdown 400 m south; it lands at home
    r = analyse(good_flight(), 60, 150, HOME, land=land, land_radius_m=150)
    assert r["landed"] and not r["checks"]["landed_near_touchdown"]
    assert r["touchdown_error_m"] > 350


def test_built_in_mission_is_a_valid_no_rc_mission():
    from types import SimpleNamespace

    from sitl_failsafe_test import build_mission
    from silm_recon import mavmission as mm
    args = SimpleNamespace(mission=None, bearing=45, out_m=1200, alt=100, wind_dir=250, fence_m=1500)
    items, land, start = build_mission(args, HOME)
    assert mm.check_mission(items, 1500)[0] == []
    assert items[1].command == mm.NAV_TAKEOFF and items[-1].command == mm.NAV_LAND
    assert items[start].command == mm.DO_LAND_START
    assert dist_m(*HOME, *land) < 1


def test_mission_file_from_the_planner_is_accepted(tmp_path):
    from types import SimpleNamespace

    from sitl_failsafe_test import build_mission
    from silm_recon import mavmission as mm
    args = SimpleNamespace(mission=None, bearing=0, out_m=1000, alt=90, wind_dir=0, fence_m=1500)
    items, _, _ = build_mission(args, HOME)
    f = tmp_path / "m.waypoints"
    f.write_text(mm.to_wpl(items))
    args.mission = str(f)
    items2, land, start = build_mission(args, HOME)
    assert len(items2) == len(items) and items2[start].command == mm.DO_LAND_START


def test_staying_armed_on_the_ground_fails():
    """Touched down, but stayed ARMED for minutes (it happened in SITL when a
    still-jammed GPS made it look like it was moving): must fail."""
    rows = good_flight()
    t_ground = rows[-60]["t"]
    for r in rows[-60:]:
        r["alt"] = 0.2                          # on the ground for the last 30 s
    r = analyse(rows, 60, 150, HOME)
    assert r["passed"] and r["armed_on_ground_s"] == rows[-1]["t"] - t_ground
    r = analyse(rows, 60, 150, HOME, max_armed_on_ground_s=10)
    assert not r["checks"]["disarmed_soon_after_landing"] and not r["passed"]


# ------------------------------------------------------------------ "nothing goes wrong" (--fault none)
LAND_START = 6               # mission items: 0 home, 1 takeoff, 2-5 survey, 6 DO_LAND_START, ...


def normal_flight(last_survey_item=5, rtl=False, est_err=3, end_n=-20):
    segs = [(80, 0, 0, 900, 0, MODE_AUTO, est_err)]                      # the survey, out and back
    if rtl:
        segs.append((4, 900, 0, 880, 0, MODE_RTL, est_err))             # a failsafe on the way
    segs += [(80, 880, 0, 60, 0, MODE_AUTO, est_err, True),             # landing sequence
             (20, 60, 0, end_n, 0, MODE_AUTO, 2, True)]                 # touchdown
    rows = track(segs)
    n_survey = sum(1 for r in rows if not r["landing"])
    for i, r in enumerate(rows):                                        # mission item flown
        r["seq"] = (LAND_START + 2 if r["landing"]
                    else 1 + min(last_survey_item - 1, int(i * last_survey_item / n_survey)))
    return rows


def test_normal_flight_passes():
    r = analyse_normal(normal_flight(), HOME, LAND_START)
    assert r["passed"], r["checks"]
    assert r["last_survey_item_reached"] == 5 and r["failsafe_modes"] == [] and r["estimate_error_max_m"] <= 5


def test_normal_flight_failures_are_named():
    r = analyse_normal(normal_flight(rtl=True), HOME, LAND_START)
    assert not r["passed"] and not r["checks"]["no_failsafe"] and r["checks"]["flew_whole_survey"]
    r = analyse_normal(normal_flight(last_survey_item=3), HOME, LAND_START)       # cut the survey short
    assert not r["checks"]["flew_whole_survey"]
    r = analyse_normal(normal_flight(est_err=60), HOME, LAND_START)                # lost track of itself
    assert not r["checks"]["position_estimate_good"]
    r = analyse_normal(normal_flight(end_n=400), HOME, LAND_START)                 # landed far away
    assert not r["checks"]["landed_near_touchdown"]
    r = analyse_normal(normal_flight(), HOME, LAND_START, fence_radius=500)
    assert not r["checks"]["stayed_inside_fence"]
