"""Telemetry reader.

Opens a MAVLink connection to the ArduPilot flight controller (Matek H743)
and keeps a thread-safe snapshot of position, altitude, attitude, flight mode
and arm state. This is read-only: the companion never commands the aircraft,
so it can run while the plane flies an AUTO mission with no operator link.

Wiring on the current airframe: the Pi talks to a free UART on the H743
(e.g. TELEM2 / SERIALx set to MAVLink2) via a 3V3 serial-USB adapter, or over
the H743's USB port. Set SERIALx_PROTOCOL=2 and a matching baud in ArduPilot.
"""

from __future__ import annotations

import bisect
import collections
import dataclasses
import math
import threading
import time
from dataclasses import dataclass, field

try:
    from pymavlink import mavutil
except Exception:  # pragma: no cover - import guarded for dev machines
    mavutil = None


@dataclass
class State:
    """Latest known aircraft state. All fields guarded by `lock`."""

    lock: threading.Lock = field(default_factory=threading.Lock)

    # Fix / position
    have_fix: bool = False
    lat: float = 0.0            # deg
    lon: float = 0.0            # deg
    alt_amsl: float = 0.0       # m above mean sea level
    rel_alt: float = 0.0        # m above the home/launch point (AGL on flat ground)

    # Attitude (radians, ArduPilot convention)
    roll: float = 0.0
    pitch: float = 0.0
    yaw: float = 0.0            # heading, 0 = North, clockwise positive

    # Ground track / speed
    ground_speed: float = 0.0  # m/s

    # Status
    armed: bool = False
    mode: str = "UNKNOWN"

    # Freshness
    last_pos_t: float = 0.0
    last_att_t: float = 0.0
    last_hb_t: float = 0.0

    def snapshot(self) -> "StateView":
        with self.lock:
            return StateView(
                have_fix=self.have_fix,
                lat=self.lat, lon=self.lon,
                alt_amsl=self.alt_amsl, rel_alt=self.rel_alt,
                roll=self.roll, pitch=self.pitch, yaw=self.yaw,
                ground_speed=self.ground_speed,
                armed=self.armed, mode=self.mode,
                age_pos=time.time() - self.last_pos_t if self.last_pos_t else 1e9,
                age_att=time.time() - self.last_att_t if self.last_att_t else 1e9,
                age_hb=time.time() - self.last_hb_t if self.last_hb_t else 1e9,
            )


@dataclass
class StateView:
    """Immutable copy handed to the main loop."""

    have_fix: bool
    lat: float
    lon: float
    alt_amsl: float
    rel_alt: float
    roll: float
    pitch: float
    yaw: float
    ground_speed: float
    armed: bool
    mode: str
    age_pos: float
    age_att: float
    age_hb: float

    def telemetry_ok(self, max_age: float = 2.0) -> bool:
        return (
            self.have_fix
            and self.age_pos < max_age
            and self.age_att < max_age
            and self.age_hb < max_age * 2
        )


class TelemetryLink:
    """Background MAVLink reader."""

    def __init__(self, connection: str, baud: int = 115200, source_system: int = 200,
                 msg_callback=None):
        if mavutil is None:
            raise RuntimeError(
                "pymavlink is not installed. Run: pip install pymavlink"
            )
        self.connection = connection
        self.baud = baud
        self.source_system = source_system
        self.state = State()
        # Recent (t, lat, lon, rel_alt, roll, pitch, yaw) samples, so a camera
        # frame can be paired with the state at the moment it was captured.
        self._hist: collections.deque = collections.deque(maxlen=400)
        self._hist_lock = threading.Lock()
        # Optional callback(msg) invoked for every received MAVLink message,
        # used by the GPS-health monitor. Must be fast and non-blocking.
        self.msg_callback = msg_callback
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._master = None

    @property
    def master(self):
        """The live mavutil connection, or None before first connect.
        Used only to forward an optional optical-flow velocity aid."""
        return self._master

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="telemetry", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3.0)

    # ------------------------------------------------------------------ #

    def _connect(self):
        master = mavutil.mavlink_connection(
            self.connection,
            baud=self.baud,
            source_system=self.source_system,
        )
        master.wait_heartbeat(timeout=30)
        # Ask the autopilot for the streams we need.
        self._request_streams(master)
        return master

    def _request_streams(self, master) -> None:
        # Request POSITION (GLOBAL_POSITION_INT) and EXTRA1 (ATTITUDE).
        for stream_id in (
            mavutil.mavlink.MAV_DATA_STREAM_POSITION,
            mavutil.mavlink.MAV_DATA_STREAM_EXTRA1,        # ATTITUDE
            mavutil.mavlink.MAV_DATA_STREAM_EXTRA2,        # VFR_HUD
            mavutil.mavlink.MAV_DATA_STREAM_EXTRA3,        # EKF_STATUS_REPORT
            mavutil.mavlink.MAV_DATA_STREAM_EXTENDED_STATUS,  # GPS_RAW_INT
        ):
            try:
                master.mav.request_data_stream_send(
                    master.target_system,
                    master.target_component,
                    stream_id,
                    5,  # Hz
                    1,  # start
                )
            except Exception:
                pass

    def _run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                self._master = self._connect()
                backoff = 1.0
                self._pump()
            except Exception as exc:  # reconnect on any link error
                print(f"[telemetry] link error: {exc}; retry in {backoff:.0f}s")
                time.sleep(backoff)
                backoff = min(backoff * 2, 15.0)

    def _pump(self) -> None:
        master = self._master
        st = self.state
        while not self._stop.is_set():
            msg = master.recv_match(blocking=True, timeout=2.0)
            if msg is None:
                # No data for 2s -> treat as link drop, let _run reconnect.
                raise TimeoutError("no MAVLink messages for 2s")
            t = msg.get_type()
            now = time.time()
            if self.msg_callback is not None:
                try:
                    self.msg_callback(msg)
                except Exception:
                    pass
            if t == "GLOBAL_POSITION_INT":
                with st.lock:
                    st.lat = msg.lat / 1e7
                    st.lon = msg.lon / 1e7
                    st.alt_amsl = msg.alt / 1000.0
                    st.rel_alt = msg.relative_alt / 1000.0
                    st.have_fix = not (msg.lat == 0 and msg.lon == 0)
                    st.last_pos_t = now
                self._record(now)
            elif t == "ATTITUDE":
                with st.lock:
                    st.roll = msg.roll
                    st.pitch = msg.pitch
                    st.yaw = msg.yaw  # radians, 0..2pi or -pi..pi depending on fw
                    st.last_att_t = now
                self._record(now)
            elif t == "VFR_HUD":
                with st.lock:
                    st.ground_speed = msg.groundspeed
            elif t == "HEARTBEAT":
                with st.lock:
                    st.armed = bool(
                        msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED
                    )
                    try:
                        st.mode = master.flightmode
                    except Exception:
                        st.mode = "UNKNOWN"
                    st.last_hb_t = now


    # ---------------------------------------------------------------- history

    def _record(self, now: float) -> None:
        st = self.state
        with st.lock:
            if not st.have_fix:
                return
            sample = (now, st.lat, st.lon, st.rel_alt, st.roll, st.pitch, st.yaw)
        with self._hist_lock:
            self._hist.append(sample)

    def state_at(self, t: float) -> "StateView":
        """Aircraft state interpolated to time t (falls back to the latest)."""
        view = self.state.snapshot()
        with self._hist_lock:
            hist = list(self._hist)
        s = interpolate_state(hist, t)
        if s is None:
            return view
        lat, lon, alt, roll, pitch, yaw = s
        return dataclasses.replace(view, lat=lat, lon=lon, rel_alt=alt,
                                   roll=roll, pitch=pitch, yaw=yaw)


def _lerp_angle(a: float, b: float, f: float) -> float:
    d = (b - a + math.pi) % (2 * math.pi) - math.pi
    return a + f * d


def interpolate_state(hist: list, t: float):
    """hist: time-sorted (t, lat, lon, alt, roll, pitch, yaw). Returns the
    interpolated (lat, lon, alt, roll, pitch, yaw) at t, clamped to the ends;
    None if there is no history or t is more than 2 s outside it."""
    if not hist:
        return None
    times = [h[0] for h in hist]
    if t <= times[0]:
        return hist[0][1:] if times[0] - t <= 2.0 else None
    if t >= times[-1]:
        return hist[-1][1:] if t - times[-1] <= 2.0 else None
    i = bisect.bisect_right(times, t)
    a, b = hist[i - 1], hist[i]
    f = (t - a[0]) / (b[0] - a[0]) if b[0] > a[0] else 0.0
    lat = a[1] + f * (b[1] - a[1])
    lon = a[2] + f * (b[2] - a[2])
    alt = a[3] + f * (b[3] - a[3])
    return (lat, lon, alt, _lerp_angle(a[4], b[4], f), _lerp_angle(a[5], b[5], f),
            _lerp_angle(a[6], b[6], f))


def normalise_heading(yaw_rad: float) -> float:
    """Return heading in [0, 2pi)."""
    return yaw_rad % (2.0 * math.pi)
