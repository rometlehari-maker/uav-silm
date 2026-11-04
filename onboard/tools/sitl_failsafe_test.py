#!/usr/bin/env python3
"""Desk-test the GPS-loss failsafe AND the no-RC autoland in ArduPilot's simulator.

Flies a real NO-RC mission in ArduPlane SITL - automatic takeoff, survey lines,
DO_LAND_START landing sequence, automatic landing - with your parameters and the
silm_gps_loss.lua script. Mid-survey it either switches the simulated GPS off or
turns on SITL's GPS JAMMING simulation (SIM_GPS1_JAM: blackout, then flickering
lock with random satellites and position jumps). It records the TRUE position
(from the simulator) against what the autopilot THINKS its position is, and
grades the outcome:

  * the failsafe engaged (RTL -> landing sequence) within SLM_GPS_LOSS_S + margin
  * the aircraft headed for the landing point while blind (true distance fell)
  * it never left the geofence (true position)
  * after GPS returns, the position estimate re-converges
  * it LANDED and DISARMED by itself, close to the planned touchdown point

The mission is built by the laptop planner's code (planner/survey.py), or pass
your own planner output with --mission silm_mission.waypoints (start SITL at
that mission's launch point with --custom-location).

Run on your PC (not on the aircraft). Step-by-step guide for Windows/WSL2,
including first-time setup and repeat runs: onboard/SITL_HOWTO.md. Short form:

  1. Get ArduPilot and build SITL once (see ArduPilot "SITL setup" docs):
       git clone --recursive https://github.com/ArduPilot/ardupilot
       cd ardupilot && Tools/environment_install/install-prereqs-ubuntu.sh -y
       . ~/.profile && ./waf configure --board sitl && ./waf plane
  2. Make a folder to start SITL from, with the script in its `scripts/`
     subfolder (SITL loads scripts and keeps its eeprom.bin in that folder):
       mkdir -p ~/sitlrun/scripts
       cp ~/uav-silm/onboard/ardupilot/scripts/silm_gps_loss.lua ~/sitlrun/scripts/
  3. Start SITL there (near your field, e.g. Tallinn). Use --custom-location;
     -L only accepts a NAMED location from locations.txt:
       cd ~/sitlrun && ~/ardupilot/Tools/autotest/sim_vehicle.py -v ArduPlane \
           --console --custom-location=59.40,24.70,30,0   # lat,lon,alt,heading
  4. Once, at the MAVProxy prompt: SITL's plane has a 3S (12.6 V) battery but
     gps_denied_failsafe.param has 4S thresholds, so scale the simulated
     reading to 4S (simulator only - never put this in your param file):
       param set BATT_VOLT_MULT 13.4667
  5. In another terminal, from onboard/:
       python3 tools/sitl_failsafe_test.py --fault jam --wind 6 --out sitl_results

  The first run sets SCR_ENABLE=1; SITL then needs one restart for the script
  to load (the tool tells you). Restart SITL before every run for a clean
  start. Repeat with --fault disable, different --wind / --wind-dir /
  --trigger-m, and longer --fault-s to see how far dead reckoning drifts
  (a long --fault-s means the landing itself is flown on dead reckoning - that is
  the real worst case, and it shows how far off the touchdown can be). The
  jamming simulation is random, so one PASS is not enough: run several
  scenarios.

  SITL keeps parameters in eeprom.bin between runs; the tool loads your param
  file every run, but a parameter you DELETE from the file keeps its old value
  in SITL until you delete eeprom.bin (then redo step 4 and the restart).

  SITL's plane takes off from a runway, not from a throw, so the tool sets
  TKOFF_THR_MINACC=0 in the simulator only. Everything else is your file.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "planner"))

MODE_AUTO, MODE_RTL = 10, 11
R_EARTH = 6378137.0


# ------------------------------------------------------------------ geometry

def offset(lat, lon, north_m, east_m):
    dlat = math.degrees(north_m / R_EARTH)
    dlon = math.degrees(east_m / (R_EARTH * math.cos(math.radians(lat))))
    return lat + dlat, lon + dlon


def dist_m(lat1, lon1, lat2, lon2):
    dn = math.radians(lat2 - lat1) * R_EARTH
    de = math.radians(lon2 - lon1) * R_EARTH * math.cos(math.radians(0.5 * (lat1 + lat2)))
    return math.hypot(dn, de)


# ------------------------------------------------------------------ analysis

def analyse(rows, fault_t, restore_t, home, loss_s=5.0, fence_radius=1500.0,
            rtl_margin_s=6.0, reconverge_m=15.0, reconverge_within_s=40.0,
            land=None, land_radius_m=150.0) -> dict:
    """Grade a recorded flight. rows: dicts with t, true_lat, true_lon,
    est_lat, est_lon, mode, and optionally landing (in the DO_LAND_START
    sequence), armed and alt. home = (lat, lon) of launch, land = planned
    touchdown (default home). Times in seconds. Returns {checks..., passed}."""
    hl, hn = home
    ll, ln = land or home

    def at(t):
        return min(rows, key=lambda r: abs(r["t"] - t))

    res: dict = {}
    fs_rows = [r for r in rows if r["t"] >= fault_t and (r["mode"] == MODE_RTL or r.get("landing"))]
    rtl_after = (fs_rows[0]["t"] - fault_t) if fs_rows else None
    res["rtl_after_s"] = None if rtl_after is None else round(rtl_after, 1)
    res["rtl_in_time"] = rtl_after is not None and rtl_after <= loss_s + rtl_margin_s

    d_fault = dist_m(ll, ln, at(fault_t)["true_lat"], at(fault_t)["true_lon"])
    d_restore = dist_m(ll, ln, at(restore_t)["true_lat"], at(restore_t)["true_lon"])
    res["true_dist_at_fault_m"] = round(d_fault, 1)
    res["true_dist_at_restore_m"] = round(d_restore, 1)
    res["headed_home_while_blind"] = d_restore < d_fault

    max_d = max(dist_m(hl, hn, r["true_lat"], r["true_lon"]) for r in rows)
    res["max_true_dist_m"] = round(max_d, 1)
    res["stayed_inside_fence"] = max_d <= fence_radius

    blind = [r for r in rows if fault_t <= r["t"] <= restore_t]
    errs = [dist_m(r["true_lat"], r["true_lon"], r["est_lat"], r["est_lon"]) for r in blind]
    res["dead_reckoning_error_max_m"] = round(max(errs), 1) if errs else None
    res["dead_reckoning_error_at_restore_m"] = round(errs[-1], 1) if errs else None

    after = [r for r in rows if r["t"] >= restore_t]
    conv_t = None
    for r in after:
        if dist_m(r["true_lat"], r["true_lon"], r["est_lat"], r["est_lon"]) < reconverge_m:
            conv_t = r["t"] - restore_t
            break
    res["reconverged_after_s"] = None if conv_t is None else round(conv_t, 1)
    res["estimate_reconverged"] = conv_t is not None and conv_t <= reconverge_within_s

    last = rows[-1]
    res["disarmed_at_end"] = not last.get("armed", True)
    res["landed"] = res["disarmed_at_end"] and last.get("alt", 0.0) < 5.0
    res["blind_at_touchdown"] = bool(res["landed"] and restore_t > _touchdown_t(rows))
    d_end = dist_m(ll, ln, last["true_lat"], last["true_lon"])
    res["touchdown_error_m"] = round(d_end, 1)
    res["landed_near_touchdown"] = res["landed"] and d_end <= land_radius_m

    checks = ["rtl_in_time", "headed_home_while_blind", "stayed_inside_fence",
              "estimate_reconverged", "landed", "landed_near_touchdown"]
    res["passed"] = all(res[c] for c in checks)
    res["checks"] = {c: res[c] for c in checks}
    return res


def _touchdown_t(rows):
    """Time it disarmed (end of the landing), or the last row's time."""
    for r in rows:
        if r.get("landing") and not r.get("armed", True):
            return r["t"]
    return rows[-1]["t"]


# ------------------------------------------------------------------ MAVLink

def read_param_file(path):
    out = []
    for line in open(path):
        line = line.split("#", 1)[0].strip()
        if line:
            name, val = [x.strip() for x in line.split(",")[:2]]
            out.append((name, float(val)))
    return out


class Sitl:
    def __init__(self, url):
        from pymavlink import mavutil
        self.mavutil = mavutil
        self.m = mavutil.mavlink_connection(url, source_system=250)
        self.m.wait_heartbeat(timeout=60)
        self.texts: list[str] = []
        for sid in (mavutil.mavlink.MAV_DATA_STREAM_ALL,):
            self.m.mav.request_data_stream_send(self.m.target_system, self.m.target_component,
                                                sid, 10, 1)

    def recv(self, types=None, timeout=1.0):
        msg = self.m.recv_match(type=types, blocking=True, timeout=timeout)
        if msg is not None and msg.get_type() == "STATUSTEXT":
            self.texts.append(msg.text)
            print(f"  [ap] {msg.text}")
        return msg

    def set_param(self, name, value, retries=5):
        for _ in range(retries):
            self.m.mav.param_set_send(self.m.target_system, self.m.target_component,
                                      name.encode(), float(value),
                                      self.mavutil.mavlink.MAV_PARAM_TYPE_REAL32)
            t0 = time.time()
            while time.time() - t0 < 2:
                msg = self.recv(["PARAM_VALUE", "STATUSTEXT"], timeout=0.5)
                if msg and msg.get_type() == "PARAM_VALUE" and msg.param_id == name:
                    if abs(msg.param_value - value) < 1e-4 * max(1, abs(value)):
                        return True
        return False

    def get_param(self, name):
        self.m.mav.param_request_read_send(self.m.target_system, self.m.target_component,
                                           name.encode(), -1)
        t0 = time.time()
        while time.time() - t0 < 3:
            msg = self.recv(["PARAM_VALUE", "STATUSTEXT"], timeout=0.5)
            if msg and msg.get_type() == "PARAM_VALUE" and msg.param_id == name:
                return msg.param_value
        return None

    def mode(self):
        hb = self.m.messages.get("HEARTBEAT")
        return hb.custom_mode if hb else None

    def set_mode(self, mode):
        self.m.mav.set_mode_send(self.m.target_system,
                                 self.mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED, mode)

    def true_pos(self):
        s = self.m.messages.get("SIMSTATE")
        return (s.lat / 1e7, s.lng / 1e7) if s else None

    def est_pos(self):
        g = self.m.messages.get("GLOBAL_POSITION_INT")
        return (g.lat / 1e7, g.lon / 1e7, g.relative_alt / 1000.0) if g else None

    def mission_seq(self):
        mc = self.m.messages.get("MISSION_CURRENT")
        return mc.seq if mc else None

    def armed(self):
        return bool(self.m.motors_armed())

    def arm(self, timeout=120):
        t0 = time.time()
        while time.time() - t0 < timeout:
            self.m.mav.command_long_send(self.m.target_system, self.m.target_component,
                                         self.mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
                                         0, 1, 0, 0, 0, 0, 0, 0)
            t1 = time.time()
            while time.time() - t1 < 2:
                self.recv(timeout=0.5)
                if self.m.motors_armed():
                    return True
        return False


def build_mission(args, home):
    """Items (items[0] = home) and (land_lat, land_lon, index of DO_LAND_START)."""
    from silm_recon import mavmission as mm
    if args.mission:
        items = mm.from_wpl(open(args.mission).read())
    else:
        import survey
        hl, hn = home
        b = math.radians(args.bearing)
        perp = b + math.pi / 2

        def pt(along, side):
            return offset(hl, hn, along * math.cos(b) + side * math.cos(perp),
                          along * math.sin(b) + side * math.sin(perp))
        area = [pt(0.3 * args.out_m, -250), pt(args.out_m, -250), pt(args.out_m, 250),
                pt(0.3 * args.out_m, 250)]
        r = survey.plan(survey.PlanInput(
            launch=home, area=area, landing=home, landing_heading_deg=args.wind_dir % 360,
            alt_m=args.alt, fence_radius_m=args.fence_m))
        if r["errors"]:
            raise SystemExit("planner refused the test mission: " + "; ".join(r["errors"]))
        items = r["items"]
    errors, warnings = mm.check_mission(items, args.fence_m)
    if errors:
        raise SystemExit("mission is not flyable with no RC: " + "; ".join(errors))
    land = next(it for it in items if it.command == mm.NAV_LAND)
    start = min(i for i, it in enumerate(items) if it.command == mm.DO_LAND_START)
    return items, (land.lat, land.lon), start


def run(args) -> int:
    from silm_recon import mavmission as mm
    s = Sitl(args.connect)
    print("connected to SITL")

    # Parameters: your file + test setup. SITL's plane rolls off a runway, so the
    # throw-detection threshold is switched off in the simulator only.
    params = read_param_file(args.params)
    params += [("SIM_WIND_SPD", args.wind), ("SIM_WIND_DIR", args.wind_dir),
               ("SIM_GPS1_ENABLE", 1), ("SIM_GPS1_JAM", 0), ("TKOFF_THR_MINACC", 0)]
    bad = [n for n, v in params if not s.set_param(n, v)]
    if bad:
        print(f"WARNING: could not set {bad}")

    slm = s.get_param("SLM_ENABLE")
    if slm is None:
        print("\nThe silm_gps_loss.lua script is not running in SITL (no SLM_ENABLE).")
        print("Check that scripts/silm_gps_loss.lua exists where you started sim_vehicle.py,")
        print("SCR_ENABLE is now 1 - restart SITL once, then re-run this test.")
        return 2
    loss_s = s.get_param("SLM_GPS_LOSS_S") or 5.0
    fence_r = s.get_param("FENCE_RADIUS") or args.fence_m
    args.fence_m = fence_r

    # Wait for a position, take home = true position on the ground.
    print("waiting for GPS/EKF ...")
    t0 = time.time()
    while time.time() - t0 < 120 and (s.true_pos() is None or s.est_pos() is None):
        s.recv(timeout=1)
    home = s.true_pos()
    hl, hn = home

    items, land, land_start = build_mission(args, home)
    if dist_m(hl, hn, items[1].lat or hl, items[1].lon or hn) > 200:
        print("WARNING: the mission's takeoff is far from where SITL started - start SITL with "
              "--custom-location=<lat>,<lon>,<alt>,0 at the mission's launch point")
    try:
        mm.upload(s.m, items)
    except RuntimeError as exc:
        print(f"mission upload failed: {exc}")
        return 1
    print(f"mission uploaded ({len(items) - 1} items; landing sequence from item {land_start}, "
          f"touchdown {land[0]:.6f}, {land[1]:.6f})")

    if not s.arm():
        print("could not arm (check pre-arm messages above)")
        return 1
    s.set_mode(MODE_AUTO)
    print("armed, AUTO - no RC from here on")

    os.makedirs(args.out, exist_ok=True)
    rows = []
    t_start = time.time()
    fault_t = restore_t = None
    phase = "survey"
    last_log = 0.0
    flew = False
    while True:
        s.recv(timeout=0.2)
        now = time.time() - t_start
        tp, ep = s.true_pos(), s.est_pos()
        if tp is None or ep is None:
            continue
        seq = s.mission_seq()
        landing = s.mode() == MODE_AUTO and seq is not None and seq >= land_start
        armed = s.armed()
        flew = flew or ep[2] > 20
        # Log every 0.2 s, and always on an arm/disarm change: the loop stops
        # right after disarm, and grading reads the last row's armed state.
        if now - last_log >= 0.2 or (rows and rows[-1]["armed"] != armed):
            rows.append({"t": round(now, 2), "phase": phase, "mode": s.mode(), "seq": seq,
                         "landing": landing, "armed": armed,
                         "true_lat": tp[0], "true_lon": tp[1], "est_lat": ep[0], "est_lon": ep[1],
                         "alt": ep[2]})
            last_log = now
        d_home = dist_m(hl, hn, tp[0], tp[1])

        if fault_t is None and not landing and ep[2] > 0.5 * args.alt and (
                d_home >= args.trigger_m or (args.trigger_s and now >= args.trigger_s)):
            fault_t = now
            phase = "gps_fault"
            if args.fault == "jam":
                s.set_param("SIM_GPS1_JAM", 1)
            else:
                s.set_param("SIM_GPS1_ENABLE", 0)
            print(f"t={now:.0f}s  {d_home:.0f} m from home: GPS {args.fault.upper()} ON")
        elif fault_t is not None and restore_t is None and now - fault_t >= args.fault_s:
            restore_t = now
            phase = "gps_restored"
            s.set_param("SIM_GPS1_JAM", 0)
            s.set_param("SIM_GPS1_ENABLE", 1)
            print(f"t={now:.0f}s  GPS restored, true distance to touchdown "
                  f"{dist_m(land[0], land[1], tp[0], tp[1]):.0f} m")
        elif restore_t is not None and flew and not armed and now - restore_t >= 10:
            print(f"t={now:.0f}s  landed and disarmed")
            break
        elif fault_t is None and flew and not armed:
            print("it landed before the fault was triggered - reduce --trigger-m or use --trigger-s")
            break
        if now > args.timeout_s:
            print("timeout")
            break

    csv_path = os.path.join(args.out, "track.csv")
    with open(csv_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    if fault_t is None or restore_t is None:
        print("fault was never triggered/restored - increase --timeout-s or reduce --trigger-m")
        return 1
    res = analyse(rows, fault_t, restore_t, home, loss_s=loss_s, fence_radius=fence_r,
                  land=land, land_radius_m=args.land_radius_m)
    res.update({"fault": args.fault, "fault_s": args.fault_s, "wind_mps": args.wind,
                "wind_dir_deg": args.wind_dir, "touchdown_planned": land,
                "ap_messages": s.texts[-40:]})
    with open(os.path.join(args.out, "result.json"), "w") as fh:
        json.dump(res, fh, indent=2)

    plot(rows, home, fault_t, restore_t, os.path.join(args.out, "track.png"), land)
    print("\n=== RESULT ===")
    for k, v in res["checks"].items():
        print(f"  {'PASS' if v else 'FAIL'}  {k}")
    for k in ("rtl_after_s", "true_dist_at_fault_m", "true_dist_at_restore_m", "max_true_dist_m",
              "dead_reckoning_error_max_m", "reconverged_after_s", "touchdown_error_m",
              "blind_at_touchdown"):
        print(f"        {k}: {res[k]}")
    print(f"\n{'PASSED' if res['passed'] else 'FAILED'} - details in {args.out}/")
    return 0 if res["passed"] else 1


def plot(rows, home, fault_t, restore_t, path, land=None):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return
    hl, hn = home

    def ne(lat, lon):
        return ((lat - hl) * math.radians(1) * R_EARTH,
                (lon - hn) * math.radians(1) * R_EARTH * math.cos(math.radians(hl)))

    fig, ax = plt.subplots(figsize=(7, 7))
    for label, sel, style in [("true track", lambda r: True, "-"),]:
        pts = [ne(r["true_lat"], r["true_lon"]) for r in rows if sel(r)]
        ax.plot([p[1] for p in pts], [p[0] for p in pts], style, color="#333", lw=1.2, label=label)
    blind = [r for r in rows if fault_t <= r["t"] <= restore_t]
    pe = [ne(r["est_lat"], r["est_lon"]) for r in blind]
    pt = [ne(r["true_lat"], r["true_lon"]) for r in blind]
    ax.plot([p[1] for p in pt], [p[0] for p in pt], color="#d33", lw=2.5, label="true, GPS lost")
    ax.plot([p[1] for p in pe], [p[0] for p in pe], "--", color="#36c", lw=1.5,
            label="autopilot's estimate, GPS lost")
    ax.plot(0, 0, "k^", ms=10, label="launch")
    if land:
        n, e = ne(*land)
        ax.plot(e, n, "gx", ms=12, mew=2.5, label="planned touchdown")
    ax.set_xlabel("east (m)")
    ax.set_ylabel("north (m)")
    ax.set_aspect("equal")
    ax.grid(alpha=0.3)
    ax.legend(loc="best", fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=130)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--connect", default="tcp:127.0.0.1:5762",
                    help="SITL MAVLink endpoint (5762 is free while MAVProxy uses 5760; "
                         "use tcp:127.0.0.1:5760 if SITL runs with --no-mavproxy)")
    ap.add_argument("--params", default=os.path.join(os.path.dirname(__file__), "..",
                                                     "ardupilot", "gps_denied_failsafe.param"))
    ap.add_argument("--fault", choices=["jam", "disable"], default="jam")
    ap.add_argument("--fault-s", type=float, default=90, help="how long GPS stays bad")
    ap.add_argument("--mission", default=None,
                    help="fly this .waypoints file (from the laptop planner) instead of the built-in survey")
    ap.add_argument("--trigger-m", type=float, default=700, help="fault when this far from home")
    ap.add_argument("--out-m", type=float, default=1200, help="mission's outbound distance")
    ap.add_argument("--trigger-s", type=float, default=None,
                    help="or fault this many seconds after start (for your own --mission)")
    ap.add_argument("--bearing", type=float, default=45)
    ap.add_argument("--fence-m", type=float, default=1500)
    ap.add_argument("--land-radius-m", type=float, default=150,
                    help="pass if it touches down this close to the planned point")
    ap.add_argument("--alt", type=float, default=100)
    ap.add_argument("--wind", type=float, default=5.0, help="simulated wind m/s")
    ap.add_argument("--wind-dir", type=float, default=250.0, help="wind from, degrees")
    ap.add_argument("--timeout-s", type=float, default=1500)
    ap.add_argument("--out", default="sitl_results")
    return run(ap.parse_args())


if __name__ == "__main__":
    sys.exit(main())
