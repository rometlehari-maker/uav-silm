"""SIYI gimbal control over Ethernet (UDP), for the A2 mini's pitch gimbal.

The A2 mini is a single-axis (pitch / tilt) gimbal with Ethernet video, so the
camera's downward angle is set and stabilised by the gimbal, not fixed to the
airframe. This module:
  * points the camera (e.g. straight down, -90 deg) at start-up
  * polls the gimbal's reported attitude, so geo-referencing uses the angle the
    camera ACTUALLY has, not an assumed one

Protocol (SIYI SDK, as used by the open-source SIYI SDK implementations;
verified here against their published example frames):
    55 66 | CTRL | DATA_LEN (2B LE) | SEQ (2B LE) | CMD_ID | DATA | CRC16 (2B LE)
    CRC16 = CRC-16/XMODEM (poly 0x1021, init 0) over everything before it.
    0x0D  acquire attitude  -> yaw, pitch, roll, yaw_rate, pitch_rate, roll_rate
                               (int16 LE, units of 0.1 deg and 0.1 deg/s)
    0x0E  set angles        <- yaw, pitch (int16 LE, 0.1 deg)
    0x08  centre            <- 0x01
Default camera address 192.168.144.25, UDP port 37260.

SIYI's A2 mini manual v1.1 notes the SDK section was removed from that
revision; community projects report the same SDK works on the A2 mini. If the
gimbal never answers, the app falls back to the fixed tilt in config
(georef.forward_tilt_deg) and says so. tools/preflight.py tests this on the
bench.
"""

from __future__ import annotations

import socket
import struct
import threading
import time
from dataclasses import dataclass

STX = b"\x55\x66"
CMD_CENTER = 0x08
CMD_ACQUIRE_ATTITUDE = 0x0D
CMD_SET_ANGLES = 0x0E


def crc16_xmodem(data: bytes) -> int:
    crc = 0
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
    return crc


def build_frame(cmd_id: int, data: bytes = b"", seq: int = 0, need_ack: bool = True) -> bytes:
    head = STX + bytes([1 if need_ack else 0]) + struct.pack("<HH", len(data), seq & 0xFFFF)
    body = head + bytes([cmd_id]) + data
    return body + struct.pack("<H", crc16_xmodem(body))


def parse_frame(buf: bytes):
    """Return (cmd_id, data) for a valid frame, else None."""
    if len(buf) < 10 or buf[:2] != STX:
        return None
    (n,) = struct.unpack("<H", buf[3:5])
    end = 8 + n
    if len(buf) < end + 2:
        return None
    (crc,) = struct.unpack("<H", buf[end:end + 2])
    if crc != crc16_xmodem(buf[:end]):
        return None
    return buf[7], buf[8:end]


def set_angles_frame(yaw_deg: float, pitch_deg: float, seq: int = 0) -> bytes:
    return build_frame(CMD_SET_ANGLES, struct.pack("<hh", round(yaw_deg * 10), round(pitch_deg * 10)), seq)


@dataclass
class GimbalAttitude:
    yaw: float
    pitch: float
    roll: float
    t: float


def parse_attitude(data: bytes, t: float | None = None) -> GimbalAttitude | None:
    if len(data) < 6:
        return None
    yaw, pitch, roll = struct.unpack("<hhh", data[:6])
    return GimbalAttitude(yaw / 10.0, pitch / 10.0, roll / 10.0, time.time() if t is None else t)


class SiyiGimbal:
    """Background client: sets the pitch once, then polls attitude."""

    def __init__(self, cfg: dict):
        self.ip = cfg.get("ip", "192.168.144.25")
        self.port = int(cfg.get("port", 37260))
        self.pitch_deg = float(cfg.get("pitch_deg", -90.0))
        self.rate_hz = float(cfg.get("poll_hz", 5.0))
        self.timeout = float(cfg.get("timeout_s", 1.0))
        self._sock = None
        self._seq = 0
        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.Lock()
        self._att: GimbalAttitude | None = None
        self.answered = False

    def _send(self, frame: bytes) -> None:
        self._sock.sendto(frame, (self.ip, self.port))

    def _next_seq(self) -> int:
        self._seq = (self._seq + 1) & 0xFFFF
        return self._seq

    def start(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.settimeout(self.timeout)
        self._thread = threading.Thread(target=self._run, name="gimbal", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        if self._sock:
            self._sock.close()

    def point(self, pitch_deg: float | None = None) -> None:
        self._send(set_angles_frame(0.0, self.pitch_deg if pitch_deg is None else pitch_deg,
                                    self._next_seq()))

    def attitude(self, max_age_s: float = 1.0) -> GimbalAttitude | None:
        with self._lock:
            a = self._att
        if a is None or time.time() - a.t > max_age_s:
            return None
        return a

    def _run(self) -> None:
        last_point = 0.0
        while not self._stop.is_set():
            now = time.time()
            try:
                # Re-send the pointing command every few seconds: harmless, and
                # recovers if the gimbal restarted (power glitch) mid-flight.
                if now - last_point > 5.0:
                    self.point()
                    last_point = now
                self._send(build_frame(CMD_ACQUIRE_ATTITUDE, b"", self._next_seq()))
                buf, _ = self._sock.recvfrom(256)
                msg = parse_frame(buf)
                if msg and msg[0] == CMD_ACQUIRE_ATTITUDE:
                    att = parse_attitude(msg[1])
                    if att:
                        with self._lock:
                            self._att = att
                        self.answered = True
            except (socket.timeout, OSError):
                pass
            self._stop.wait(1.0 / self.rate_hz)
