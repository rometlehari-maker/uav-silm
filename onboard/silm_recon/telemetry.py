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

    def __init__(self, connection: str, baud: int = 115200, source_system: int = 200):
        if mavutil is None:
            raise RuntimeError(
                "pymavlink is not installed. Run: pip install pymavlink"
            )
        self.connection = connection
        self.baud = baud
        self.source_system = source_system
        self.state = State()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._master = None

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
            mavutil.mavlink.MAV_DATA_STREAM_EXTRA1,
            mavutil.mavlink.MAV_DATA_STREAM_EXTRA2,
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
            if t == "GLOBAL_POSITION_INT":
                with st.lock:
                    st.lat = msg.lat / 1e7
                    st.lon = msg.lon / 1e7
                    st.alt_amsl = msg.alt / 1000.0
                    st.rel_alt = msg.relative_alt / 1000.0
                    st.have_fix = not (msg.lat == 0 and msg.lon == 0)
                    st.last_pos_t = now
            elif t == "ATTITUDE":
                with st.lock:
                    st.roll = msg.roll
                    st.pitch = msg.pitch
                    st.yaw = msg.yaw  # radians, 0..2pi or -pi..pi depending on fw
                    st.last_att_t = now
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


def normalise_heading(yaw_rad: float) -> float:
    """Return heading in [0, 2pi)."""
    return yaw_rad % (2.0 * math.pi)
