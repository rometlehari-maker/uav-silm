#!/usr/bin/env python3
"""Bring-up / preflight check: does it actually work on THIS aircraft?

Run on the Pi with everything connected (camera on Ethernet, flight controller
on its serial port, GPS outdoors), before the first flight and after any
change:

    python3 tools/preflight.py --config config.yaml --alt-m 100 --speed-mps 19

Each part is checked on the real hardware and reported PASS / WARN / FAIL:
  camera   stream opens; real resolution and frame rate
  gimbal   answers; pointed down; reports its angle
  fc       heartbeat; position/attitude message rates; GPS fix; the no-RC
           launch / landing / failsafe params; the silm_gps_loss.lua script is
           loaded (its SLM_ params exist)
  mission  the mission on the flight controller can be flown with no RC:
           TAKEOFF first, DO_LAND_START landing sequence, ends in LAND, inside
           the fence
  model    loads; labels match; MEASURED inference speed with the configured
           detection region / tiling, against the frame rate the mission needs
           (2 sightings while an object crosses the detection region)
  system   free storage, CPU temperature / throttling, system clock
Skip parts with --skip, e.g. --skip fc,gimbal on a bench without them.
Exit code 0 = no FAIL.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, HERE)

import yaml  # noqa: E402

RESULTS = []


def report(part, status, msg):
    RESULTS.append((part, status, msg))
    print(f"  [{status:4s}] {part:7s} {msg}")


def required_fps(cfg: dict, alt_m: float, speed_mps: float, sightings: int = 2) -> tuple[float, float]:
    """(detection fps needed, seconds an object stays in the detection region)."""
    from pixels_on_target import focal_px, time_in_view
    g, cam, det = cfg.get("georef", {}), cfg.get("camera", {}), cfg.get("detector", {})
    calib = g.get("calibration")
    lens = (calib or {}).get("model") or g.get("lens", "pinhole")
    fw, fh = int(cam.get("width", 1920)), int(cam.get("height", 1080))
    fy = focal_px(fw, float(g.get("hfov_deg", 160)), calib, lens)
    roi_h = (det["roi"][3] - det["roi"][1]) if det.get("roi") else 1.0
    _, secs = time_in_view(alt_m, fy, fh, speed_mps, lens, roi_h)
    return sightings / secs, secs


def check_camera(cfg):
    from silm_recon.camera import Camera, CameraError
    ccfg = dict(cfg.get("camera", {}))
    ccfg.setdefault("open_timeout_s", 20)
    t0 = time.time()
    try:
        cam = Camera(ccfg)
    except CameraError as exc:
        report("camera", "FAIL", f"{exc}. Check the Ethernet cable, the Pi's address "
               "(192.168.144.x for the A2 mini) and camera.url / camera.backend")
        return None
    opened = time.time() - t0
    n, t1 = 0, time.time()
    while time.time() - t1 < 5:
        f, _ = cam.read_with_time()
        if f is not None:
            n += 1
        else:
            time.sleep(0.005)
    fps = n / 5.0
    w, h = cam.frame_size
    cam.release()
    status = "PASS" if fps >= 10 else ("WARN" if fps >= 3 else "FAIL")
    report("camera", status, f"{ccfg.get('source', 'rtsp')} {w}x{h}, {fps:.1f} new frames/s "
           f"(opened in {opened:.1f} s)")
    if (w, h) != (int(cfg.get("camera", {}).get("width", w)), int(cfg.get("camera", {}).get("height", h))):
        report("camera", "WARN", f"config says {cfg['camera'].get('width')}x{cfg['camera'].get('height')}; "
               f"set camera.width/height to {w}x{h} (pixels_on_target and calibration use them)")
    return w, h


def check_gimbal(cfg):
    from silm_recon.siyi import SiyiGimbal
    gcfg = cfg.get("gimbal", {}) or {}
    if not gcfg.get("enabled"):
        report("gimbal", "SKIP", "gimbal.enabled is false (fixed georef.forward_tilt_deg is used)")
        return
    g = SiyiGimbal(dict(gcfg, poll_hz=10))
    g.start()
    t0 = time.time()
    while time.time() - t0 < 6 and (g.attitude() is None or abs(g.attitude().pitch - g.pitch_deg) > 3):
        time.sleep(0.1)
    att = g.attitude()
    g.stop()
    if att is None:
        report("gimbal", "FAIL", f"no answer from {g.ip}:{g.port}. If the camera streams but the "
               "gimbal never answers, its firmware may not support the SDK: set gimbal.enabled: "
               "false and measure the camera's real tilt for georef.forward_tilt_deg")
        return
    ok = abs(att.pitch - g.pitch_deg) <= 3
    report("gimbal", "PASS" if ok else "WARN",
           f"reports pitch {att.pitch:.1f} deg (asked {g.pitch_deg:.0f}), roll {att.roll:.1f}. "
           "Now tilt the aircraft nose-up by hand: with angle_frame: earth the reported pitch must "
           "stay put (stabilised); if it moves with the airframe, set angle_frame: body")


def check_fc(cfg, timeout=20):
    try:
        from pymavlink import mavutil
    except ImportError:
        report("fc", "FAIL", "pymavlink not installed")
        return
    t = cfg["telemetry"]
    try:
        m = mavutil.mavlink_connection(t["connection"], baud=int(t.get("baud", 115200)),
                                       source_system=201)
        if not m.wait_heartbeat(timeout=timeout):
            raise TimeoutError("no heartbeat")
    except Exception as exc:
        report("fc", "FAIL", f"{t['connection']}: {exc}. Check wiring, SERIALx_PROTOCOL=2 and baud")
        return
    report("fc", "PASS", f"heartbeat from system {m.target_system}, mode {m.flightmode}")
    for sid in (mavutil.mavlink.MAV_DATA_STREAM_POSITION, mavutil.mavlink.MAV_DATA_STREAM_EXTRA1,
                mavutil.mavlink.MAV_DATA_STREAM_EXTENDED_STATUS):
        m.mav.request_data_stream_send(m.target_system, m.target_component, sid, 5, 1)
    counts = {"GLOBAL_POSITION_INT": 0, "ATTITUDE": 0}
    gps = None
    t0 = time.time()
    while time.time() - t0 < 5:
        msg = m.recv_match(blocking=True, timeout=0.5)
        if msg is None:
            continue
        if msg.get_type() in counts:
            counts[msg.get_type()] += 1
        if msg.get_type() == "GPS_RAW_INT":
            gps = msg
    for k, n in counts.items():
        hz = n / 5.0
        report("fc", "PASS" if hz >= 4 else "WARN" if hz > 0 else "FAIL",
               f"{k} at {hz:.1f} Hz (want >= 4 Hz for geo-referencing)")
    if gps is None:
        report("fc", "WARN", "no GPS_RAW_INT received")
    else:
        report("fc", "PASS" if gps.fix_type >= 3 else "WARN",
               f"GPS fix type {gps.fix_type}, {gps.satellites_visible} satellites")

    def param(name):
        m.mav.param_request_read_send(m.target_system, m.target_component, name.encode(), -1)
        t1 = time.time()
        while time.time() - t1 < 3:
            msg = m.recv_match(type="PARAM_VALUE", blocking=True, timeout=0.5)
            if msg and msg.param_id == name:
                return msg.param_value
        return None

    # No RC receiver on this aircraft: these decide whether it can arm, launch
    # and land on its own, and whether a failsafe ends in a landing.
    expect = {"THR_FAILSAFE": 0, "FS_LONG_ACTN": 0, "FS_GCS_ENABL": 0, "ARSPD_USE": 1,
              "SCR_ENABLE": 1, "EK3_OPTIONS": 1, "RTL_AUTOLAND": 2, "ARMING_MIS_ITEMS": 13,
              "BATT_FS_LOW_ACT": 2, "BATT_FS_CRT_ACT": 2, "INITIAL_MODE": 10,
              # GPS-loss source set (silm_gps_loss.lua switches to it) and no
              # DCM fallback: without these a jammed GPS steers the aircraft.
              "AHRS_OPTIONS": 1, "EK3_SRC2_POSXY": 0, "EK3_SRC2_POSZ": 1,
              "EK3_SRC2_VELZ": 0, "EK3_SRC2_YAW": 1}
    for name, want in expect.items():
        v = param(name)
        if v is None:
            report("fc", "WARN", f"{name}: no answer")
        else:
            report("fc", "PASS" if int(v) == want else "FAIL",
                   f"{name} = {v:g} (gps_denied_failsafe.param sets {want})")
    v = param("ARMING_SKIPCHK")
    if v is not None:
        report("fc", "PASS" if int(v) & 64 else "FAIL",
               f"ARMING_SKIPCHK = {v:g}: " + ("RC check skipped (no receiver)" if int(v) & 64 else
                                              "RC bit 64 not set - it will refuse to arm without a receiver"))
    v = param("TKOFF_THR_MINACC")
    if v is not None:
        report("fc", "PASS" if v > 0 else "FAIL",
               f"TKOFF_THR_MINACC = {v:g}" + ("" if v > 0 else ": 0 means the motor starts on arming, "
                                              "not on the throw - set 15"))
    v = param("SLM_ENABLE")
    report("fc", "PASS" if v == 1 else "FAIL",
           "silm_gps_loss.lua loaded (SLM_ENABLE=1)" if v == 1 else
           "silm_gps_loss.lua NOT loaded: copy it to APM/scripts on the FC's SD card, SCR_ENABLE=1, reboot twice")
    check_onboard_mission(m, param("FENCE_RADIUS"), param("FENCE_ALT_MAX"))


def check_onboard_mission(m, fence_radius=None, alt_max=None):
    """Download the mission stored on the flight controller and check it can be
    flown with no RC: TAKEOFF first, a DO_LAND_START landing sequence, ends in LAND."""
    from silm_recon import mavmission as mm
    try:
        items = mm.download(m)
    except Exception as exc:
        report("mission", "FAIL", f"cannot download the mission: {exc}")
        return
    errors, warnings = mm.check_mission(items, fence_radius or None, alt_max or None)
    n = len(items) - 1
    for e in errors:
        report("mission", "FAIL", e)
    for w in warnings:
        report("mission", "WARN", w)
    if not errors:
        land = next(it for it in items if it.command == mm.NAV_LAND)
        report("mission", "PASS", f"{n} items, takeoff -> ... -> landing sequence -> LAND at "
               f"{land.lat:.6f}, {land.lon:.6f}. Plan it in the laptop planner (planner/app.py)")


def check_model(cfg, frame_size, alt_m, speed_mps):
    import numpy as np
    from silm_recon.detector import ObjectDetector
    d = cfg.get("detector", {})
    if not d.get("enabled", True):
        report("model", "SKIP", "detector disabled")
        return
    try:
        det = ObjectDetector(d["model"], d["labels"], float(d.get("score_threshold", 0.45)),
                             fmt=d.get("format", "auto"), class_thresholds=d.get("class_thresholds"))
    except Exception as exc:
        report("model", "FAIL", f"cannot load {d.get('model')}: {exc}")
        return
    if det.num_classes is not None and det.num_classes != len(det.labels):
        report("model", "FAIL", f"model has {det.num_classes} classes, labels file has {len(det.labels)}")
    else:
        report("model", "PASS", f"{os.path.basename(d['model'])}: input {det.input_size}, "
               f"{len(det.labels)} labels")
    missing = [t for t in cfg.get("targets", []) if t not in set(det.labels.values())]
    if missing:
        report("model", "WARN", f"targets not in this model (never detected): {missing}")
    w, h = frame_size or (int(cfg["camera"].get("width", 1920)), int(cfg["camera"].get("height", 1080)))
    frame = (np.random.default_rng(0).random((h, w, 3)) * 255).astype(np.uint8)
    roi = tuple(d["roi"]) if d.get("roi") else None
    grid = tuple(d.get("tile_grid", [2, 2])) if d.get("tiled") else (1, 1)
    det.detect_roi(frame, roi, grid)                       # warm-up
    n, t0 = 0, time.time()
    while time.time() - t0 < 8 and n < 20:
        det.detect_roi(frame, roi, grid)
        n += 1
    per_frame = (time.time() - t0) / n
    have = 1.0 / per_frame
    need, secs = required_fps(cfg, alt_m, speed_mps)
    msg = (f"{have:.2f} detection frames/s measured ({grid[0]}x{grid[1]} tiles, region "
           f"{'whole frame' if roi is None else roi}); an object stays in the region "
           f"{secs:.1f} s at {alt_m:.0f} m / {speed_mps:.0f} m/s, so 2 sightings need {need:.2f}/s")
    report("model", "PASS" if have >= need * 1.3 else "WARN" if have >= need else "FAIL", msg)


def check_system(cfg):
    out = os.path.expanduser(cfg.get("storage", {}).get("output_dir", "~/silm_captures"))
    os.makedirs(out, exist_ok=True)
    free = shutil.disk_usage(out).free / 1e9
    try:
        p = os.path.join(out, ".preflight_write_test")
        with open(p, "w") as fh:
            fh.write("ok")
        os.remove(p)
        writable = True
    except OSError:
        writable = False
    report("system", "PASS" if free > 2 and writable else "FAIL" if not writable or free < 0.2 else "WARN",
           f"storage {out}: {free:.1f} GB free, {'writable' if writable else 'NOT writable'}")
    from silm_recon.health import decode_throttled, read_cpu_temp_c, read_throttled
    temp, thr = read_cpu_temp_c(), read_throttled()
    if temp is not None:
        report("system", "PASS" if temp < 70 else "WARN", f"CPU {temp:.0f} C")
    if thr is not None:
        flags = decode_throttled(thr)
        report("system", "PASS" if not flags else "WARN",
               "no throttling or under-voltage" if not flags else "throttle flags: " + ", ".join(flags))
    year = time.gmtime().tm_year
    report("system", "PASS" if year >= 2025 else "WARN",
           f"system clock {time.strftime('%Y-%m-%d %H:%M:%SZ', time.gmtime())}"
           + ("" if year >= 2025 else ": the Pi has no clock battery; timestamps will be wrong "
              "without network time - fix with chrony + the GPS, or set the date before flight"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=os.path.join(HERE, "..", "config.yaml"))
    ap.add_argument("--alt-m", type=float, default=100.0, help="mission altitude AGL")
    ap.add_argument("--speed-mps", type=float, default=19.0, help="mission ground speed")
    ap.add_argument("--skip", default="", help="comma list: camera,gimbal,fc,model,system")
    ap.add_argument("--settings", default=None,
                    help="Ground Station settings file (default ~/silm-settings/settings.yaml)")
    a = ap.parse_args(argv)
    # the same configuration the flight program uses: config.yaml + settings.yaml
    import run as run_mod  # noqa: E402
    try:
        cfg = run_mod.load_checked(a.config, a.settings)
    except SystemExit as exc:          # invalid config: report it, check the rest anyway
        report("config", "FAIL", str(exc).replace("\n", " "))
        with open(a.config, encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh) or {}
        cfg.setdefault("_settings_file", a.settings or "?")
        cfg.setdefault("_settings_loaded", False)
    if cfg.get("_settings_rejected"):
        report("settings", "FAIL", "the Ground Station settings file was rejected: "
               + "; ".join(cfg["_settings_rejected"]))
    else:
        report("settings", "PASS", f"{cfg['_settings_file']} "
               + ("loaded" if cfg["_settings_loaded"] else "not present - config.yaml defaults"))
    skip = {s.strip() for s in a.skip.split(",") if s.strip()}
    print(f"preflight check ({a.config}, mission {a.alt_m:.0f} m at {a.speed_mps:.0f} m/s)")
    size = None
    for part, fn in (("camera", lambda: check_camera(cfg)), ("gimbal", lambda: check_gimbal(cfg)),
                     ("fc", lambda: check_fc(cfg)),
                     ("model", lambda: check_model(cfg, size, a.alt_m, a.speed_mps)),
                     ("system", lambda: check_system(cfg))):
        if part in skip:
            report(part, "SKIP", "--skip")
            continue
        try:
            r = fn()
            if part == "camera" and r:
                size = r
        except Exception as exc:  # one broken part must not hide the others
            report(part, "FAIL", f"check crashed: {exc!r}")
    fails = [r for r in RESULTS if r[1] == "FAIL"]
    warns = [r for r in RESULTS if r[1] == "WARN"]
    print(f"\n{len(fails)} FAIL, {len(warns)} WARN" + ("  -> fix the FAILs before flying" if fails else ""))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
