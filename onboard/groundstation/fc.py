"""The flight controller over its USB cable (Matek H743 running ArduPlane).

Everything here is done on the ground, disarmed, with the USB cable plugged in:

    info              is it there, armed?, GPS, battery, firmware, script loaded?
    send_params       write the aircraft's parameters, read each one back
    send_script       copy silm_gps_loss.lua to the SD card (MAVFTP), read it back
    send_mission      upload the mission, read it back, compare
    arm / disarm      for the launch (arming checks the wind, the mission and
                      the autopilot's own pre-arm checks)

Every function opens and closes its own connection, so the cable can be
unplugged at any moment between steps.
"""

from __future__ import annotations

import os
import tempfile
import time

from silm_recon import mavmission as mm

SCRIPT_NAME = "silm_gps_loss.lua"
# where scripts live: SD card of a real board, then SITL's working folder
SCRIPT_DIRS = ["/APM/scripts", "APM/scripts", "scripts"]


class FCError(RuntimeError):
    pass


def connect(port: str, baud: int = 115200, timeout: float = 10.0):
    from pymavlink import mavutil
    if not port:
        raise FCError("choose the flight controller's port (USB) first")
    try:
        m = mavutil.mavlink_connection(port, baud=int(baud), source_system=255,
                                       autoreconnect=False)
    except Exception as exc:
        raise FCError(f"could not open {port}: {exc}") from exc
    if not m.wait_heartbeat(timeout=timeout):
        m.close()
        raise FCError(f"no answer on {port} - is the flight controller plugged in and powered?")
    return m


def _texts(m, seconds=1.5) -> list[str]:
    out, t0 = [], time.time()
    while time.time() - t0 < seconds:
        msg = m.recv_match(type="STATUSTEXT", blocking=True, timeout=0.2)
        if msg is not None:
            out.append(msg.text)
    return out


def info(port: str, baud: int = 115200) -> dict:
    """A snapshot of the aircraft: armed, mode, GPS, battery, firmware, script."""
    from pymavlink import mavutil
    m = connect(port, baud)
    try:
        m.mav.request_data_stream_send(m.target_system, m.target_component,
                                       mavutil.mavlink.MAV_DATA_STREAM_ALL, 4, 1)
        m.mav.command_long_send(m.target_system, m.target_component,
                                mavutil.mavlink.MAV_CMD_REQUEST_AUTOPILOT_CAPABILITIES,
                                0, 1, 0, 0, 0, 0, 0, 0)
        got: dict = {}
        t0 = time.time()
        while time.time() - t0 < 3.0:
            msg = m.recv_match(type=["HEARTBEAT", "GPS_RAW_INT", "SYS_STATUS",
                                     "AUTOPILOT_VERSION", "STATUSTEXT"], blocking=True, timeout=0.3)
            if msg is None:
                continue
            t = msg.get_type()
            if t == "HEARTBEAT" and msg.get_srcComponent() == 1:
                got["armed"] = bool(msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
                got["mode"] = mavutil.mode_string_v10(msg)
            elif t == "GPS_RAW_INT":
                got["gps_fix"] = int(msg.fix_type)
                got["satellites"] = int(msg.satellites_visible)
            elif t == "SYS_STATUS":
                got["battery_v"] = round(msg.voltage_battery / 1000.0, 2) if msg.voltage_battery > 0 else None
            elif t == "AUTOPILOT_VERSION":
                v = msg.flight_sw_version
                got["firmware"] = f"{v >> 24 & 255}.{v >> 16 & 255}.{v >> 8 & 255}"
            elif t == "STATUSTEXT":
                got.setdefault("messages", []).append(msg.text)
        slm = mm.get_param(m, "SLM_ENABLE")
        got["script_loaded"] = slm is not None
        got["scripting_enabled"] = mm.get_param(m, "SCR_ENABLE") == 1
        return got
    finally:
        m.close()


def send_params(port: str, params: dict, baud: int = 115200, progress=None) -> dict:
    """Write each parameter and read it back. Refuses an armed aircraft."""
    m = connect(port, baud)
    try:
        if m.motors_armed():
            raise FCError("the aircraft is ARMED - disarm before changing parameters")
        ok, failed, missing = [], [], []
        for i, (name, value) in enumerate(params.items()):
            if progress:
                progress(i, len(params), name)
            cur = mm.get_param(m, name, timeout=2.0)
            if cur is None:
                if name.startswith("SLM_"):
                    missing.append(name)      # appear after the script's first boot
                    continue
                missing.append(name)
                continue
            if abs(cur - float(value)) < 1e-3 * max(1.0, abs(float(value))):
                ok.append(name)
                continue
            (ok if mm.set_param(m, name, float(value)) else failed).append(name)
        needs_reboot = any(n.startswith(("SCR_", "EK3_SRC", "AHRS_", "BATT_MONITOR", "ARSPD_TYPE"))
                           for n in params)
        return {"ok": not failed and not missing, "written": len(ok), "failed": failed,
                "not_on_aircraft": missing, "reboot_needed": needs_reboot}
    finally:
        m.close()


def send_script(port: str, local_path: str, baud: int = 115200) -> dict:
    """Copy the Lua script to the flight controller's scripts folder and read
    it back to compare."""
    from pymavlink.mavftp import MAVFTP
    with open(local_path, "rb") as fh:
        data = fh.read()
    m = connect(port, baud)
    try:
        if m.motors_armed():
            raise FCError("the aircraft is ARMED - disarm before copying the script")
        ftp = MAVFTP(m, m.target_system, m.target_component)
        tried = []
        for d in SCRIPT_DIRS:
            remote = f"{d}/{SCRIPT_NAME}"
            ftp.cmd_mkdir([d])                         # fine if it already exists
            r = ftp.cmd_put([local_path, remote])
            if getattr(r, "error_code", 1) != 0:
                tried.append(f"{remote}: {getattr(r, 'error_code', r)}")
                continue
            with tempfile.TemporaryDirectory() as td:
                back = os.path.join(td, "back.lua")
                g = ftp.cmd_get([remote, back])
                same = getattr(g, "error_code", 1) == 0 and os.path.isfile(back) and \
                    open(back, "rb").read() == data
            return {"ok": same, "path": remote, "bytes": len(data),
                    "message": (f"copied to {remote} and read back identical ({len(data)} bytes). "
                                "Reboot the flight controller to start it.") if same else
                               f"copied to {remote} but the read-back did not match - copy again"}
        return {"ok": False, "message": "could not copy the script (" + "; ".join(tried) +
                "). Is there an SD card in the flight controller?"}
    finally:
        m.close()


def send_mission(port: str, items: list, baud: int = 115200, fence_m: float | None = None) -> dict:
    m = connect(port, baud)
    try:
        if m.motors_armed():
            raise FCError("the aircraft is ARMED - disarm before uploading")
        errors, _ = mm.check_mission(items, None)
        if errors:
            raise FCError("mission has errors: " + "; ".join(errors))
        mm.upload(m, items)
        back = mm.download(m)
        same = len(back) == len(items) and all(
            a.command == b.command and abs(a.lat - b.lat) < 1e-6 and abs(a.lon - b.lon) < 1e-6
            and abs(a.alt - b.alt) < 0.05 for a, b in zip(items[1:], back[1:]))
        msg = f"uploaded {len(items) - 1} items, read back: " + ("identical" if same else "MISMATCH")
        if fence_m:
            fr = mm.get_param(m, "FENCE_RADIUS")
            if fr is not None and fr < fence_m:
                msg += f". WARNING: FENCE_RADIUS on the aircraft is {fr:.0f} m, the mission needs " \
                       f"{fence_m:.0f} m - send the parameters too"
        return {"ok": same, "message": msg}
    finally:
        m.close()


def arm(port: str, baud: int = 115200) -> dict:
    """Arm in AUTO for the launch, after checking the mission ON the aircraft."""
    m = connect(port, baud)
    try:
        items = mm.download(m)
        errors, _ = mm.check_mission(items, mm.get_param(m, "FENCE_RADIUS") or None)
        if errors:
            return {"ok": False, "message": "not arming - mission on the aircraft: " + "; ".join(errors)}
        if not mm.set_mode(m, mm.MODE_AUTO):
            return {"ok": False, "message": "could not switch to AUTO"}
        ok, msg = mm.arm_disarm(m, True)
        return {"ok": ok, "message": msg}
    finally:
        m.close()


def disarm(port: str, baud: int = 115200) -> dict:
    m = connect(port, baud)
    try:
        ok, msg = mm.arm_disarm(m, False)
        return {"ok": ok, "message": msg}
    finally:
        m.close()


def list_ports() -> list:
    try:
        from serial.tools import list_ports as lp
        return [{"device": p.device, "description": p.description} for p in lp.comports()]
    except Exception:
        return []
