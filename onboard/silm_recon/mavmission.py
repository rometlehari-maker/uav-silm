"""Missions: .waypoints files, MAVLink upload/download, and a no-RC sanity check.

Shared by the laptop planner (planner/), the preflight check on the Pi
(tools/preflight.py) and the simulator test (tools/sitl_failsafe_test.py).

File format: "QGC WPL 110", the plain-text format Mission Planner and
QGroundControl both read and write, so missions can be opened and edited in
either GUI as well.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

# MAVLink command / frame numbers used here
NAV_WAYPOINT = 16
NAV_LOITER_UNLIM = 17
NAV_RETURN_TO_LAUNCH = 20
NAV_LAND = 21
NAV_TAKEOFF = 22
DO_CHANGE_SPEED = 178
DO_LAND_START = 189
FRAME_GLOBAL = 0
FRAME_GLOBAL_RELATIVE_ALT = 3
NAMES = {NAV_WAYPOINT: "WAYPOINT", NAV_LOITER_UNLIM: "LOITER_UNLIM",
         NAV_RETURN_TO_LAUNCH: "RETURN_TO_LAUNCH", NAV_LAND: "LAND", NAV_TAKEOFF: "TAKEOFF",
         DO_CHANGE_SPEED: "DO_CHANGE_SPEED", DO_LAND_START: "DO_LAND_START"}


@dataclass
class Item:
    command: int
    lat: float = 0.0
    lon: float = 0.0
    alt: float = 0.0
    params: list = field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0])
    frame: int = FRAME_GLOBAL_RELATIVE_ALT
    autocontinue: int = 1

    def to_dict(self) -> dict:
        return {"command": self.command, "name": NAMES.get(self.command, str(self.command)),
                "lat": self.lat, "lon": self.lon, "alt": self.alt,
                "params": list(self.params), "frame": self.frame}


# ------------------------------------------------------------ .waypoints

def to_wpl(items: list[Item]) -> str:
    """items[0] is home. Returns QGC WPL 110 text."""
    lines = ["QGC WPL 110"]
    for seq, it in enumerate(items):
        frame = FRAME_GLOBAL if seq == 0 else it.frame
        p = (list(it.params) + [0.0] * 4)[:4]
        lines.append("\t".join([
            str(seq), "1" if seq == 0 else "0", str(frame), str(it.command),
            *[f"{v:.8g}" for v in p],
            f"{it.lat:.8f}", f"{it.lon:.8f}", f"{it.alt:.6g}", str(it.autocontinue)]))
    return "\n".join(lines) + "\n"


def from_wpl(text: str) -> list[Item]:
    rows = [ln for ln in text.splitlines() if ln.strip()]
    if not rows or not rows[0].startswith("QGC WPL"):
        raise ValueError("not a QGC WPL waypoint file")
    items = []
    for n, ln in enumerate(rows[1:], 2):
        f = ln.split("\t") if "\t" in ln else ln.split()
        if len(f) < 12:
            raise ValueError(f"line {n}: expected 12 fields, got {len(f)}")
        items.append(Item(command=int(f[3]), lat=float(f[8]), lon=float(f[9]), alt=float(f[10]),
                          params=[float(v) for v in f[4:8]], frame=int(f[2]),
                          autocontinue=int(float(f[11]))))
    return items


# ------------------------------------------------------------ sanity check

def check_mission(items: list[Item], fence_radius_m: float | None = None,
                  max_alt_m: float | None = None) -> tuple[list[str], list[str]]:
    """(errors, warnings) for flying `items` with NO RC link: it must take off
    automatically and end in an automatic landing."""
    from math import cos, hypot, radians
    errors, warnings = [], []
    if len(items) < 2:
        return ["mission is empty"], []
    home = items[0]
    body = items[1:]
    nav = [(i, it) for i, it in enumerate(body, 1) if it.command < 80 or it.command in (NAV_LAND,)]
    if not body or body[0].command != NAV_TAKEOFF:
        errors.append("the first item must be TAKEOFF (hand launch in AUTO)")
    lands = [i for i, it in enumerate(body, 1) if it.command == NAV_LAND]
    starts = [i for i, it in enumerate(body, 1) if it.command == DO_LAND_START]
    if not lands:
        errors.append("no LAND item: with no RC the aircraft would circle until the battery is empty")
    if not starts:
        errors.append("no DO_LAND_START: RTL / battery / GPS-loss failsafes have no landing to go to")
    if lands and starts and min(starts) > max(lands):
        errors.append("DO_LAND_START must come before the LAND item")
    if lands and nav and nav[-1][1].command != NAV_LAND:
        errors.append("the mission must END with the LAND item")
    if any(it.command == NAV_LOITER_UNLIM for it in body):
        errors.append("LOITER_UNLIM never ends - with no RC nobody can end it")
    if any(it.command == NAV_RETURN_TO_LAUNCH for it in body):
        warnings.append("RETURN_TO_LAUNCH item: fine with RTL_AUTOLAND=2, but the LAND item is what ends the flight")
    k = 111320.0
    far, high = [], []
    for i, it in enumerate(body, 1):
        if it.command in (NAV_WAYPOINT, NAV_TAKEOFF, NAV_LAND) and (it.lat or it.lon):
            d = hypot((it.lat - home.lat) * k, (it.lon - home.lon) * k * cos(radians(home.lat)))
            if fence_radius_m and d > fence_radius_m - 10:
                far.append((d, i))
            if max_alt_m and it.alt > max_alt_m:
                high.append((it.alt, i))
    if far:
        d, i = max(far)
        errors.append(f"{len(far)} item(s) outside FENCE_RADIUS {fence_radius_m:.0f} m "
                      f"(farthest: item {i}, {d:.0f} m from home)")
    if high:
        a, i = max(high)
        errors.append(f"{len(high)} item(s) above the {max_alt_m:.0f} m limit "
                      f"(highest: item {i}, {a:.0f} m)")
    return errors, warnings


# ------------------------------------------------------------ MAVLink transfer

def _recv(m, types, timeout):
    return m.recv_match(type=types, blocking=True, timeout=timeout)


def upload(m, items: list[Item], timeout: float = 30.0) -> None:
    """Upload items (items[0] = home) to the connected vehicle `m` (a pymavlink
    connection after wait_heartbeat). Raises RuntimeError on failure."""
    from pymavlink import mavutil
    mav = m.mav
    mav.mission_count_send(m.target_system, m.target_component, len(items))
    t0 = time.time()
    sent = set()
    while time.time() - t0 < timeout:
        msg = _recv(m, ["MISSION_REQUEST_INT", "MISSION_REQUEST", "MISSION_ACK"], 2.0)
        if msg is None:
            continue
        if msg.get_type() == "MISSION_ACK":
            if msg.type == mavutil.mavlink.MAV_MISSION_ACCEPTED and len(sent) == len(items):
                return
            raise RuntimeError(f"vehicle rejected the mission (MISSION_ACK type {msg.type})")
        seq = msg.seq
        if seq >= len(items):
            raise RuntimeError(f"vehicle asked for item {seq} of {len(items)}")
        it = items[seq]
        p = (list(it.params) + [0.0] * 4)[:4]
        mav.mission_item_int_send(m.target_system, m.target_component, seq,
                                  FRAME_GLOBAL if seq == 0 else it.frame, it.command,
                                  0, it.autocontinue, p[0], p[1], p[2], p[3],
                                  int(round(it.lat * 1e7)), int(round(it.lon * 1e7)), float(it.alt))
        sent.add(seq)
    raise RuntimeError("mission upload timed out")


def download(m, timeout: float = 30.0) -> list[Item]:
    """Download the mission stored on the vehicle (items[0] = home)."""
    from pymavlink import mavutil
    mav = m.mav
    mav.mission_request_list_send(m.target_system, m.target_component)
    msg = _recv(m, ["MISSION_COUNT"], 5.0)
    if msg is None:
        raise RuntimeError("no MISSION_COUNT from the vehicle")
    n = msg.count
    items: list[Item] = []
    t0 = time.time()
    while len(items) < n and time.time() - t0 < timeout:
        mav.mission_request_int_send(m.target_system, m.target_component, len(items))
        msg = _recv(m, ["MISSION_ITEM_INT"], 2.0)
        if msg is None or msg.seq != len(items):
            continue
        items.append(Item(command=msg.command, lat=msg.x / 1e7, lon=msg.y / 1e7, alt=msg.z,
                          params=[msg.param1, msg.param2, msg.param3, msg.param4],
                          frame=msg.frame, autocontinue=msg.autocontinue))
    if len(items) < n:
        raise RuntimeError(f"mission download incomplete ({len(items)}/{n})")
    mav.mission_ack_send(m.target_system, m.target_component, mavutil.mavlink.MAV_MISSION_ACCEPTED)
    return items


def set_param(m, name: str, value: float, timeout: float = 5.0) -> bool:
    from pymavlink import mavutil
    m.mav.param_set_send(m.target_system, m.target_component, name.encode(), float(value),
                         mavutil.mavlink.MAV_PARAM_TYPE_REAL32)
    t0 = time.time()
    while time.time() - t0 < timeout:
        msg = _recv(m, ["PARAM_VALUE"], 1.0)
        if msg is not None and msg.param_id == name:
            return abs(msg.param_value - value) < 1e-3 * max(1.0, abs(value))
    return False


MODE_AUTO = 10  # ArduPlane custom mode number


def set_mode(m, mode: int = MODE_AUTO, timeout: float = 5.0) -> bool:
    """Switch flight mode (ArduPlane custom mode number); True once the
    heartbeat reports it."""
    from pymavlink import mavutil
    t0 = time.time()
    while time.time() - t0 < timeout:
        m.mav.set_mode_send(m.target_system, mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED, mode)
        hb = _recv(m, ["HEARTBEAT"], 1.0)
        if hb is not None and hb.custom_mode == mode:
            return True
    return False


def arm_disarm(m, arm: bool, timeout: float = 8.0) -> tuple[bool, str]:
    """Arm (True) or disarm (False) with a MAVLink command. Returns (ok,
    message); on refusal the message carries the autopilot's reason."""
    from pymavlink import mavutil
    texts = []
    m.mav.command_long_send(m.target_system, m.target_component,
                            mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM, 0,
                            1 if arm else 0, 0, 0, 0, 0, 0, 0)
    t0 = time.time()
    while time.time() - t0 < timeout:
        msg = _recv(m, ["HEARTBEAT", "COMMAND_ACK", "STATUSTEXT"], 0.5)
        if msg is None:
            continue
        kind = msg.get_type()
        if kind == "STATUSTEXT":
            texts.append(msg.text)
        elif kind == "COMMAND_ACK" and msg.command == mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM \
                and msg.result != mavutil.mavlink.MAV_RESULT_ACCEPTED:
            time.sleep(0.3)
            while (extra := _recv(m, ["STATUSTEXT"], 0.2)) is not None:
                texts.append(extra.text)
            return False, "refused: " + ("; ".join(texts) or f"result {msg.result}")
        elif kind == "HEARTBEAT" and bool(msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED) == arm:
            return True, "armed" if arm else "disarmed"
    return False, "no confirmation" + (": " + "; ".join(texts) if texts else "")


def get_param(m, name: str, timeout: float = 3.0):
    m.mav.param_request_read_send(m.target_system, m.target_component, name.encode(), -1)
    t0 = time.time()
    while time.time() - t0 < timeout:
        msg = _recv(m, ["PARAM_VALUE"], 0.5)
        if msg is not None and msg.param_id == name:
            return msg.param_value
    return None
