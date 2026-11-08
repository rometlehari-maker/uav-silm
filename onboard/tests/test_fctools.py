"""Flight controller tools: firmware files, the firmware update against a fake
bootloader on a virtual serial port, and the job runner's step logic against a
scripted fake flight controller. (The MAVLink calibrations and logs are also
flown against ArduPilot's simulator in test_fctools_sitl.py.)"""

import base64
import json
import os
import struct
import sys
import threading
import time
import types
import zlib

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

from groundstation import fc, fctools  # noqa: E402
from groundstation.vendor import ardupilot_uploader as U  # noqa: E402

BOARD_ID = 1013                              # MatekH743


def apj(image: bytes, board_id=BOARD_ID) -> bytes:
    return json.dumps({"board_id": board_id, "board_revision": 0, "image_size": len(image),
                       "image": base64.b64encode(zlib.compress(image)).decode(),
                       "version": "4.7.0", "summary": "MatekH743"}).encode()


@pytest.fixture
def fwdir(tmp_path, monkeypatch):
    monkeypatch.setattr(fctools, "firmware_dir", lambda: str(tmp_path))
    return tmp_path


def test_firmware_files_are_checked(fwdir):
    with pytest.raises(ValueError, match="not an ArduPilot firmware"):
        fctools.save_firmware("x.apj", b"hello")
    r = fctools.save_firmware("../../evil name.apj", apj(b"\x01" * 1000))
    assert r["ok"] and r["file"] == "evilname.apj" and r["board_id"] == BOARD_ID
    assert (fwdir / "evilname.apj").exists()
    assert [f["file"] for f in fctools.firmware_files()] == ["evilname.apj"]
    with pytest.raises(ValueError):
        fctools.download_firmware("Matek H743; rm -rf /")


class FakeBootloader(threading.Thread):
    """Speaks the PX4/ArduPilot bootloader protocol on a pty, like a board does."""

    def __init__(self, fd, board_id=BOARD_ID, flash=128 * 1024):
        super().__init__(daemon=True)
        self.fd, self.board_id, self.flash = fd, board_id, flash
        self.image, self.erased, self.rebooted = bytearray(), False, False

    def read(self, n):
        out = b""
        while len(out) < n:
            out += os.read(self.fd, n - len(out))
        return out

    def sync(self):
        os.write(self.fd, U.uploader.INSYNC + U.uploader.OK)

    def run(self):
        try:
            while not self.rebooted:
                c = self.read(1)
                if c == U.uploader.GET_SYNC:
                    self.read(1); self.sync()
                elif c == U.uploader.GET_DEVICE:
                    p = self.read(1)[0]; self.read(1)
                    val = {1: 5, 2: self.board_id, 3: 0, 4: self.flash, 6: 0}.get(p, 0)
                    os.write(self.fd, struct.pack("<I", val)); self.sync()
                elif c in (U.uploader.GET_OTP, U.uploader.GET_SN):
                    self.read(5); os.write(self.fd, b"\x00\x00\x00\x00"); self.sync()
                elif c == U.uploader.GET_CHIP:
                    self.read(1); os.write(self.fd, struct.pack("<I", 0x450)); self.sync()
                elif c == U.uploader.GET_CHIP_DES:
                    self.read(1); d = b"STM32H7[4|5]x,V"; os.write(self.fd, struct.pack("<I", len(d)) + d); self.sync()
                elif c in (U.uploader.CHIP_ERASE, U.uploader.CHIP_FULL_ERASE):
                    self.read(1); self.image = bytearray(); self.erased = True; self.sync()
                elif c == U.uploader.PROG_MULTI:
                    n = self.read(1)[0]; self.image += self.read(n); self.read(1); self.sync()
                elif c == U.uploader.GET_CRC:
                    self.read(1)
                    state = U.crc32(self.image, 0)
                    for _ in range(len(self.image), self.flash - 1, 4):
                        state = U.crc32(bytearray(b"\xff\xff\xff\xff"), state)
                    os.write(self.fd, struct.pack("<I", state)); self.sync()
                elif c == U.uploader.REBOOT:
                    self.read(1); self.sync(); self.rebooted = True
                # anything else (MAVLink reboot packets, NSH text): ignored, like a bootloader
        except OSError:
            pass


@pytest.mark.skipif(not hasattr(os, "openpty"), reason="needs a virtual serial port (Linux/macOS)")
@pytest.mark.parametrize("board_id,ok", [(BOARD_ID, True), (9, False)])
def test_firmware_update_through_the_bootloader(fwdir, monkeypatch, board_id, ok):
    master, slave = os.openpty()
    port = os.ttyname(slave)
    bl = FakeBootloader(master, board_id=board_id)
    bl.start()
    image = os.urandom(5000)
    fctools.save_firmware("plane.apj", apj(image))
    monkeypatch.setattr(fc, "connect", lambda *a, **k: (_ for _ in ()).throw(fc.FCError("in bootloader")))
    jobs = fctools.FCJobs()
    assert jobs.start("firmware", port, file="plane.apj")["ok"]
    t0 = time.time()
    while jobs.status()["state"] == "running" and time.time() - t0 < 60:
        time.sleep(0.2)
    st = jobs.status()
    if ok:
        assert st["state"] == "done", st["log"]
        assert bl.erased and bl.rebooted and bytes(bl.image[:len(image)]) == image
        assert st["progress"] == 100
    else:                                       # firmware for another board: refused, nothing written
        assert st["state"] == "failed" and not bl.erased, st["log"]
        assert any("not suitable" in line for line in st["log"])
    os.close(slave)


# ---------------------------------------------------------------- job logic, scripted FC
class Msg(types.SimpleNamespace):
    def get_type(self):
        return self._type


def mk(t, **kw):
    return Msg(_type=t, **kw)


class FakeFC:
    """Answers like ArduPlane: a queue of messages per command sent."""

    def __init__(self, script):
        self.script, self.queue, self.sent = script, [], []
        self.target_system = self.target_component = 1
        self.mav = types.SimpleNamespace(command_long_send=self._cmd)

    def _cmd(self, ts, tc, command, conf, *p):
        self.sent.append((command, p[0], p[2], p[4]))
        self.queue += self.script(command, p)

    def recv_match(self, type=None, blocking=True, timeout=0.5):
        types_ = [type] if isinstance(type, str) else (type or [])
        for i, m in enumerate(self.queue):
            if not types_ or m.get_type() in types_:
                return self.queue.pop(i)
        time.sleep(min(timeout, 0.02))
        return None

    def close(self):
        pass


def run_job(monkeypatch, kind, script, answer_each=False):
    from pymavlink import mavutil  # noqa: F401
    f = FakeFC(script)
    monkeypatch.setattr(fctools, "_open_disarmed", lambda port, baud: f)
    jobs = fctools.FCJobs()
    assert jobs.start(kind, "fake")["ok"]
    t0 = time.time()
    while time.time() - t0 < 20:
        st = jobs.status()
        if st["state"] == "waiting" and answer_each:
            jobs.answer(True)
        if st["state"] not in ("running", "waiting"):
            break
        time.sleep(0.05)
    return jobs.status(), f


def test_level_and_airspeed(monkeypatch):
    from pymavlink import mavutil
    ack = lambda c, p: [mk("COMMAND_ACK", command=c, result=0)]   # noqa: E731
    st, f = run_job(monkeypatch, "level", ack)
    assert st["state"] == "done" and f.sent[0][3] == 2              # param5 = 2: level
    st, f = run_job(monkeypatch, "airspeed", lambda c, p: [mk("COMMAND_ACK", command=c, result=5),
                                                          mk("COMMAND_ACK", command=c, result=0)])
    assert st["state"] == "done" and f.sent[0][2] == 1              # param3 = 1: baro + airspeed
    st, _ = run_job(monkeypatch, "level", lambda c, p: [mk("COMMAND_ACK", command=c, result=4)])
    assert st["state"] == "failed"
    assert mavutil.mavlink.MAV_CMD_PREFLIGHT_CALIBRATION == f.sent[0][0]


def test_six_position_accelerometer_calibration(monkeypatch):
    from pymavlink import mavutil
    POS = mavutil.mavlink.MAV_CMD_ACCELCAL_VEHICLE_POS

    def script(c, p):
        if c == mavutil.mavlink.MAV_CMD_PREFLIGHT_CALIBRATION:
            return [mk("COMMAND_ACK", command=c, result=0), mk("COMMAND_LONG", command=POS, param1=1)]
        nxt = int(p[0]) + 1                                         # sample taken: ask for the next
        return [mk("COMMAND_LONG", command=POS, param1=nxt if nxt <= 6 else fctools.ACCELCAL_SUCCESS)]
    st, f = run_job(monkeypatch, "accel", script, answer_each=True)
    assert st["state"] == "done", st
    assert [s[1] for s in f.sent if s[0] == POS] == [1, 2, 3, 4, 5, 6]


def test_compass_calibration_success_and_failure(monkeypatch):
    from pymavlink import mavutil

    def script_for(status):
        def script(c, p):
            if c != mavutil.mavlink.MAV_CMD_DO_START_MAG_CAL:
                return []
            return [mk("COMMAND_ACK", command=c, result=0),
                    mk("MAG_CAL_PROGRESS", compass_id=0, completion_pct=40),
                    mk("MAG_CAL_PROGRESS", compass_id=0, completion_pct=100),
                    mk("MAG_CAL_REPORT", compass_id=0, cal_status=status, fitness=8.2)]
        return script
    st, _ = run_job(monkeypatch, "compass", script_for(mavutil.mavlink.MAG_CAL_SUCCESS))
    assert st["state"] == "done" and "restart" in st["result"]
    st, _ = run_job(monkeypatch, "compass", script_for(mavutil.mavlink.MAG_CAL_FAILED))
    assert st["state"] == "failed"


def test_one_job_at_a_time_and_cancel(monkeypatch):
    from pymavlink import mavutil
    POS = mavutil.mavlink.MAV_CMD_ACCELCAL_VEHICLE_POS
    f = FakeFC(lambda c, p: [mk("COMMAND_ACK", command=c, result=0), mk("COMMAND_LONG", command=POS, param1=1)])
    monkeypatch.setattr(fctools, "_open_disarmed", lambda port, baud: f)
    jobs = fctools.FCJobs()
    assert jobs.start("accel", "fake")["ok"]
    time.sleep(0.4)
    assert jobs.status()["state"] == "waiting" and "LEVEL" not in jobs.status()["prompt"]
    assert not jobs.start("level", "fake")["ok"]                   # busy
    assert not jobs.start("nonsense", "fake")["ok"]
    assert jobs.cancel()["ok"]
    time.sleep(0.6)
    assert jobs.status()["state"] in ("failed", "cancelled")
