"""Flight controller tools, over the USB cable: everything that otherwise needs
Mission Planner, except installing ArduPlane the very first time.

    servos / set_reversed     which output drives what; reverse a control surface
    battery / battery_cal     the measured voltage; calibrate it to a multimeter
    log_list                  the flight logs on the board
    FCJobs                    long, step-by-step jobs, one at a time, in the
                              background (the page polls status()):
        level      level the attitude (aircraft sitting level, flying attitude)
        accel      full 6-position accelerometer calibration (you move it)
        compass    compass calibration (you turn it in every direction)
        airspeed   zero the airspeed sensor (and the barometer), pitot covered
        log        download one flight log
        firmware   update ArduPlane through the board's bootloader (the
                   ArduPilot/PX4 uploader, vendor/ardupilot_uploader.py)

Messages and commands are ArduPilot's own (checked against its source): the
same ones Mission Planner uses.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
import zlib

from groundstation import fc
from silm_recon import mavmission as mm

HERE = os.path.dirname(os.path.abspath(__file__))
UPLOADER = os.path.join(HERE, "vendor", "ardupilot_uploader.py")

# ArduPlane SERVOn_FUNCTION values for the outputs a plane uses
SERVO_FUNCTIONS = {
    1: "RC passthrough", 2: "Flap", 3: "Flap (auto)", 4: "Aileron", 16: "Differential spoiler",
    19: "Elevator", 21: "Rudder", 24: "Flaperon left", 25: "Flaperon right", 26: "Ground steering",
    33: "Motor 1", 34: "Motor 2", 70: "Throttle", 73: "Throttle left", 74: "Throttle right",
    77: "Elevon left", 78: "Elevon right", 79: "V-tail left", 80: "V-tail right",
}

ACCEL_POSITIONS = {1: "level, sitting on its wheels or belly as in flight", 2: "on its LEFT side (left wing down)",
                   3: "on its RIGHT side (right wing down)", 4: "nose DOWN (pointing at the floor)",
                   5: "nose UP (tail on the floor)", 6: "on its BACK (upside down)"}
ACCELCAL_SUCCESS, ACCELCAL_FAILED = 16777215, 16777216

# firmware.ardupilot.org: official builds, by board name as ArduPilot calls it
FIRMWARE_URL = "https://firmware.ardupilot.org/Plane/{release}/{board}/arduplane.apj"
BOARDS = {"MatekH743": "Matek H743-WING / H743-WLITE / H743-SLIM (all versions)",
          "MatekF765-Wing": "Matek F765-WING", "MatekF405-Wing": "Matek F405-WING (no Lua scripts!)",
          "CubeOrange": "CubeOrange", "Pixhawk6X": "Holybro Pixhawk 6X", "Durandal": "Holybro Durandal"}


def _cmd(m, command, *params):
    p = list(params) + [0] * (7 - len(params))
    m.mav.command_long_send(m.target_system, m.target_component, command, 0, *p)


def _wait_ack(m, command, timeout=10.0, final=True):
    """The COMMAND_ACK result for `command` (skipping IN_PROGRESS if final)."""
    from pymavlink import mavutil
    t0 = time.time()
    while time.time() - t0 < timeout:
        msg = m.recv_match(type="COMMAND_ACK", blocking=True, timeout=0.5)
        if msg is None or msg.command != command:
            continue
        if final and msg.result == mavutil.mavlink.MAV_RESULT_IN_PROGRESS:
            continue
        return msg.result
    return None


def _armed(m) -> bool:
    from pymavlink import mavutil
    hb = m.recv_match(type="HEARTBEAT", blocking=True, timeout=3)
    return bool(hb and hb.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)


def _open_disarmed(port, baud):
    m = fc.connect(port, baud)
    if _armed(m):
        m.close()
        raise fc.FCError("the aircraft is ARMED: disarm it first")
    return m


# ---------------------------------------------------------------- servos
def servos(port: str, baud: int = 115200) -> dict:
    m = _open_disarmed(port, baud)
    try:
        out = []
        for n in range(1, 17):
            f = mm.get_param(m, f"SERVO{n}_FUNCTION", timeout=2.0)
            if f is None:
                break
            if int(f) == 0:
                continue
            row = {"n": n, "function": int(f), "name": SERVO_FUNCTIONS.get(int(f), f"function {int(f)}")}
            for k in ("REVERSED", "MIN", "MAX", "TRIM"):
                v = mm.get_param(m, f"SERVO{n}_{k}", timeout=2.0)
                row[k.lower()] = None if v is None else int(v)
            out.append(row)
        return {"ok": True, "servos": out}
    finally:
        m.close()


def set_reversed(port: str, n: int, reversed_: bool, baud: int = 115200) -> dict:
    if not 1 <= int(n) <= 16:
        raise ValueError("output number must be 1-16")
    m = _open_disarmed(port, baud)
    try:
        ok = mm.set_param(m, f"SERVO{int(n)}_REVERSED", 1 if reversed_ else 0)
        return {"ok": ok, "message": f"output {n} {'reversed' if reversed_ else 'normal'}" if ok
                else f"could not set SERVO{n}_REVERSED"}
    finally:
        m.close()


# ---------------------------------------------------------------- battery
def _voltage(m) -> float | None:
    t0 = time.time()
    while time.time() - t0 < 4.0:
        msg = m.recv_match(type="SYS_STATUS", blocking=True, timeout=0.5)
        if msg is not None and msg.voltage_battery not in (0, 65535):
            return msg.voltage_battery / 1000.0
    return None


def battery(port: str, baud: int = 115200) -> dict:
    m = fc.connect(port, baud)
    try:
        v, mult = _voltage(m), mm.get_param(m, "BATT_VOLT_MULT")
        return {"ok": v is not None, "voltage": v, "volt_mult": mult,
                "message": f"the flight controller measures {v:.2f} V" if v is not None
                else "no battery voltage: is the battery in, and BATT_MONITOR set (the aircraft settings do)?"}
    finally:
        m.close()


def battery_cal(port: str, measured_v: float, baud: int = 115200) -> dict:
    """Make the flight controller's voltage match a multimeter."""
    measured_v = float(measured_v)
    if not 3.0 <= measured_v <= 60.0:
        raise ValueError("enter the battery voltage the multimeter shows (3-60 V)")
    m = _open_disarmed(port, baud)
    try:
        v, mult = _voltage(m), mm.get_param(m, "BATT_VOLT_MULT")
        if v is None or not mult:
            return {"ok": False, "message": "no battery voltage from the flight controller"}
        new = round(mult * measured_v / v, 4)
        if not 0.5 * mult <= new <= 2.0 * mult:
            return {"ok": False, "message": f"{measured_v:.2f} V is far from the measured {v:.2f} V: "
                                            "check the multimeter and the battery monitor wiring"}
        if not mm.set_param(m, "BATT_VOLT_MULT", new):
            return {"ok": False, "message": "could not set BATT_VOLT_MULT"}
        time.sleep(1.5)
        after = _voltage(m)
        return {"ok": True, "volt_mult": new, "voltage": after,
                "message": f"voltage multiplier {mult:g} -> {new:g}; it now reads "
                           + (f"{after:.2f} V" if after else "(no reading yet)")}
    finally:
        m.close()


# ---------------------------------------------------------------- logs
def log_list(port: str, baud: int = 115200) -> dict:
    m = fc.connect(port, baud)
    try:
        m.mav.log_request_list_send(m.target_system, m.target_component, 0, 0xFFFF)
        logs, total, t0 = {}, None, time.time()
        while time.time() - t0 < 6.0 and (total is None or len(logs) < total):
            e = m.recv_match(type="LOG_ENTRY", blocking=True, timeout=1.0)
            if e is None:
                if total is not None:
                    break
                continue
            total = e.num_logs
            if e.num_logs and e.size:
                logs[e.id] = {"id": e.id, "size": e.size,
                              "time": time.strftime("%Y-%m-%d %H:%M", time.gmtime(e.time_utc)) if e.time_utc else ""}
        return {"ok": True, "logs": [logs[k] for k in sorted(logs)]}
    finally:
        m.close()


# ---------------------------------------------------------------- firmware files
def firmware_dir() -> str:
    d = os.path.join(os.path.expanduser("~"), "SILM", "firmware")
    os.makedirs(d, exist_ok=True)
    return d


def check_apj(data: bytes) -> dict:
    """A firmware file (.apj): the facts the uploader needs, or ValueError."""
    try:
        desc = json.loads(data)
        image = zlib.decompress(base64.b64decode(desc["image"]))
    except Exception as exc:
        raise ValueError(f"not an ArduPilot firmware file (.apj): {exc}") from exc
    if "board_id" not in desc:
        raise ValueError("the firmware file has no board_id")
    return {"board_id": int(desc["board_id"]), "image_size": len(image),
            "version": desc.get("version", ""), "git_identity": desc.get("git_identity", ""),
            "summary": desc.get("summary", "")}


def save_firmware(name: str, data: bytes) -> dict:
    info = check_apj(data)
    safe = "".join(c for c in os.path.basename(name or "firmware.apj") if c.isalnum() or c in "._-") or "firmware.apj"
    if not safe.endswith(".apj"):
        safe += ".apj"
    path = os.path.join(firmware_dir(), safe)
    with open(path, "wb") as fh:
        fh.write(data)
    return {"ok": True, "file": safe, **info, "message": f"{safe} saved (board id {info['board_id']})"}


def download_firmware(board: str = "MatekH743", release: str = "stable") -> dict:
    if board not in BOARDS and not board.replace("-", "").replace("_", "").isalnum():
        raise ValueError(f"unknown board {board!r}")
    if release not in ("stable", "beta", "latest"):
        raise ValueError("release must be stable, beta or latest")
    url = FIRMWARE_URL.format(board=board, release=release)
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            data = r.read()
    except Exception as exc:
        raise fc.FCError(f"could not download {url} (internet needed): {exc}") from exc
    res = save_firmware(f"arduplane-{board}-{release}.apj", data)
    res["url"] = url
    return res


def firmware_files() -> list[dict]:
    out = []
    for f in sorted(os.listdir(firmware_dir())):
        if f.endswith(".apj"):
            p = os.path.join(firmware_dir(), f)
            out.append({"file": f, "size_mb": round(os.path.getsize(p) / 1e6, 1),
                        "saved": time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(p)))})
    return out


def uploader_cmd() -> list[str]:
    """The ArduPilot uploader as its own process (stoppable): this Python with
    the vendored script, or the packaged app started in uploader mode."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--ardupilot-uploader"]
    return [sys.executable, "-u", UPLOADER]


# ---------------------------------------------------------------- jobs
class FCJobs:
    """One long flight-controller job at a time, in a thread."""

    def __init__(self, log_dir: str | None = None):
        self.job: dict | None = None
        self.log_dir = log_dir or os.path.join(os.path.expanduser("~"), "SILM", "fc_logs")
        self._lock = threading.Lock()

    # ---------------- control
    def start(self, kind: str, port: str, baud: int = 115200, **kw) -> dict:
        with self._lock:
            if self.job and self.job["state"] in ("running", "waiting"):
                return {"ok": False, "message": f"'{self.job['kind']}' is still running"}
            run = getattr(self, f"_{kind}", None)
            if kind not in ("level", "accel", "compass", "airspeed", "log", "firmware") or run is None:
                return {"ok": False, "message": f"unknown job {kind!r}"}
            self.job = {"kind": kind, "state": "running", "step": "starting", "prompt": "", "progress": 0,
                        "log": [], "result": None, "answer": None, "cancel": False, "started": time.time()}
            threading.Thread(target=self._wrap, args=(run, port, int(baud), kw), daemon=True).start()
            return {"ok": True, "message": f"{kind} started"}

    def status(self) -> dict:
        j = self.job
        if not j:
            return {"state": "idle"}
        return {k: v for k, v in j.items() if k not in ("answer", "cancel", "proc")} | {"log": j["log"][-60:]}

    def answer(self, value=True) -> dict:
        if not self.job or self.job["state"] != "waiting":
            return {"ok": False, "message": "nothing is waiting for you"}
        self.job["answer"] = value
        return {"ok": True}

    def cancel(self) -> dict:
        if not self.job or self.job["state"] not in ("running", "waiting"):
            return {"ok": False, "message": "nothing is running"}
        self.job["cancel"] = True
        p = self.job.get("proc")
        if p is not None and p.poll() is None:
            p.kill()
        return {"ok": True, "message": "stopping"}

    # ---------------- helpers
    def _say(self, line: str):
        self.job["log"].append(time.strftime("%H:%M:%S ") + line)
        del self.job["log"][:-400]

    def _wrap(self, run, port, baud, kw):
        j = self.job
        try:
            run(port, baud, **kw)
        except Exception as exc:  # noqa: BLE001 - shown to the user
            j["state"], j["result"] = "failed", str(exc)
            self._say(f"ERROR: {exc}")
        finally:
            if j["state"] in ("running", "waiting"):
                j["state"] = "cancelled" if j["cancel"] else "failed"
                j["result"] = j["result"] or ("cancelled" if j["cancel"] else "stopped")

    def _done(self, ok: bool, msg: str):
        self.job["state"], self.job["result"] = ("done" if ok else "failed"), msg
        self.job["progress"] = 100 if ok else self.job["progress"]
        self._say(msg)

    def _texts(self, m, timeout=0.2):
        msg = m.recv_match(type="STATUSTEXT", blocking=True, timeout=timeout)
        if msg is not None:
            self._say("FC: " + msg.text)
        return msg

    # ---------------- level
    def _level(self, port, baud):
        from pymavlink import mavutil
        m = _open_disarmed(port, baud)
        try:
            self.job["step"] = "levelling (keep it still)"
            _cmd(m, mavutil.mavlink.MAV_CMD_PREFLIGHT_CALIBRATION, 0, 0, 0, 0, 2)
            r = _wait_ack(m, mavutil.mavlink.MAV_CMD_PREFLIGHT_CALIBRATION, 15)
            self._done(r == mavutil.mavlink.MAV_RESULT_ACCEPTED,
                       "level saved: the attitude it sits in now is 'level'" if r == 0
                       else f"levelling failed (result {r}): keep it still and level, then try again")
        finally:
            m.close()

    # ---------------- airspeed (+ barometer)
    def _airspeed(self, port, baud):
        from pymavlink import mavutil
        m = _open_disarmed(port, baud)
        try:
            self.job["step"] = "zeroing the airspeed sensor (pitot covered, no wind)"
            _cmd(m, mavutil.mavlink.MAV_CMD_PREFLIGHT_CALIBRATION, 0, 0, 1)
            r = _wait_ack(m, mavutil.mavlink.MAV_CMD_PREFLIGHT_CALIBRATION, 30)
            self._done(r == mavutil.mavlink.MAV_RESULT_ACCEPTED,
                       "airspeed sensor and barometer zeroed" if r == 0
                       else f"airspeed calibration failed (result {r}): is the sensor connected and enabled?")
        finally:
            m.close()

    # ---------------- 6-position accelerometer
    def _accel(self, port, baud):
        from pymavlink import mavutil
        j = self.job
        m = _open_disarmed(port, baud)
        try:
            _cmd(m, mavutil.mavlink.MAV_CMD_PREFLIGHT_CALIBRATION, 0, 0, 0, 0, 1)
            r = _wait_ack(m, mavutil.mavlink.MAV_CMD_PREFLIGHT_CALIBRATION, 15, final=False)
            if r != mavutil.mavlink.MAV_RESULT_ACCEPTED:
                return self._done(False, f"the flight controller refused the calibration (result {r})")
            current, t0 = None, time.time()
            while time.time() - t0 < 900 and not j["cancel"]:
                msg = m.recv_match(type=["COMMAND_LONG", "STATUSTEXT"], blocking=True, timeout=0.3)
                if msg is not None and msg.get_type() == "STATUSTEXT":
                    self._say("FC: " + msg.text)
                    if "successful" in msg.text.lower():
                        return self._done(True, "accelerometers calibrated")
                    if "failed" in msg.text.lower() or "cancelled" in msg.text.lower():
                        return self._done(False, "accelerometer calibration failed: hold each position still, "
                                                 "and turn it exactly as asked")
                elif msg is not None and msg.command == mavutil.mavlink.MAV_CMD_ACCELCAL_VEHICLE_POS:
                    pos = int(msg.param1)
                    if pos == ACCELCAL_SUCCESS:
                        return self._done(True, "accelerometers calibrated")
                    if pos == ACCELCAL_FAILED:
                        return self._done(False, "accelerometer calibration failed")
                    if pos in ACCEL_POSITIONS and pos != current:
                        current = pos
                        j.update(state="waiting", step=f"position {pos} of 6", answer=None,
                                 prompt=f"Place the aircraft {ACCEL_POSITIONS[pos]}, hold it still, then press Done.",
                                 progress=int(100 * (pos - 1) / 6))
                if j["state"] == "waiting" and j["answer"]:
                    j.update(state="running", answer=None, prompt="", step=f"measuring position {current}")
                    _cmd(m, mavutil.mavlink.MAV_CMD_ACCELCAL_VEHICLE_POS, current)
            self._done(False, "cancelled" if j["cancel"] else "timed out")
        finally:
            m.close()

    # ---------------- compass
    def _compass(self, port, baud):
        from pymavlink import mavutil
        j = self.job
        m = _open_disarmed(port, baud)
        try:
            # all compasses, retry on failure, save automatically, no delay, no reboot
            _cmd(m, mavutil.mavlink.MAV_CMD_DO_START_MAG_CAL, 0, 1, 1, 0, 0)
            r = _wait_ack(m, mavutil.mavlink.MAV_CMD_DO_START_MAG_CAL, 10)
            if r != mavutil.mavlink.MAV_RESULT_ACCEPTED:
                return self._done(False, f"the flight controller refused the compass calibration (result {r})")
            j.update(state="running", step="turning",
                     prompt="Turn the aircraft slowly through every direction: nose up, nose down, both wing tips "
                            "down, upside down, round and round, until the bar is full.")
            progress, reports, t0 = {}, {}, time.time()
            while time.time() - t0 < 300 and not j["cancel"]:
                msg = m.recv_match(type=["MAG_CAL_PROGRESS", "MAG_CAL_REPORT", "STATUSTEXT"],
                                   blocking=True, timeout=0.5)
                if msg is None:
                    continue
                t = msg.get_type()
                if t == "STATUSTEXT":
                    self._say("FC: " + msg.text)
                elif t == "MAG_CAL_PROGRESS":
                    progress[msg.compass_id] = msg.completion_pct
                    j["progress"] = int(min(progress.values()))
                elif t == "MAG_CAL_REPORT":
                    reports[msg.compass_id] = msg.cal_status
                    self._say(f"compass {msg.compass_id + 1}: "
                              + ("OK" if msg.cal_status == mavutil.mavlink.MAG_CAL_SUCCESS else f"status {msg.cal_status}")
                              + f", fitness {msg.fitness:.1f}")
                    if set(reports) >= set(progress):
                        ok = all(s == mavutil.mavlink.MAG_CAL_SUCCESS for s in reports.values())
                        return self._done(ok, "compass calibrated and saved: restart the flight controller "
                                              "(battery and USB out for 10 s) to use it" if ok else
                                              "compass calibration failed: away from metal and magnets, turn it "
                                              "slowly through every direction, and try again")
            if j["cancel"]:
                _cmd(m, mavutil.mavlink.MAV_CMD_DO_CANCEL_MAG_CAL, 0)
            self._done(False, "cancelled" if j["cancel"] else "timed out")
        finally:
            m.close()

    # ---------------- one log
    def _log(self, port, baud, log_id: int = 0, size: int = 0, name: str = ""):
        j = self.job
        m = fc.connect(port, baud)
        try:
            size, log_id = int(size), int(log_id)
            if size <= 0:
                return self._done(False, "unknown log size: list the logs first")
            data, got = bytearray(size), [False] * ((size + 89) // 90)
            j["step"] = f"downloading log {log_id} ({size / 1e6:.1f} MB)"
            m.mav.log_request_data_send(m.target_system, m.target_component, log_id, 0, 0xFFFFFFFF)
            last, tries = time.time(), 0
            while not all(got) and not j["cancel"]:
                msg = m.recv_match(type="LOG_DATA", blocking=True, timeout=1.0)
                if msg is not None and msg.id == log_id:
                    data[msg.ofs:msg.ofs + msg.count] = bytes(msg.data[:msg.count])
                    got[msg.ofs // 90] = True
                    last = time.time()
                    j["progress"] = int(100 * sum(got) / len(got))
                    if msg.count < 90 and msg.ofs + msg.count >= size:
                        got[-1] = True
                elif time.time() - last > 2.0:              # ask again for what is missing
                    tries += 1
                    if tries > 30:
                        return self._done(False, "the download stalled: plug the USB cable directly, try again")
                    first = got.index(False)
                    m.mav.log_request_data_send(m.target_system, m.target_component, log_id,
                                                first * 90, size - first * 90)
                    last = time.time()
            m.mav.log_request_end_send(m.target_system, m.target_component)
            if j["cancel"]:
                return self._done(False, "cancelled")
            os.makedirs(self.log_dir, exist_ok=True)
            path = os.path.join(self.log_dir, name or f"log_{log_id:03d}_{time.strftime('%Y%m%d_%H%M%S')}.bin")
            with open(path, "wb") as fh:
                fh.write(data)
            j["file"] = path
            self._done(True, f"saved {path} (open it in Mission Planner / UAV Log Viewer)")
        finally:
            m.close()

    # ---------------- firmware
    def _firmware(self, port, baud, file: str = "", ports: list | None = None):
        j = self.job
        path = os.path.join(firmware_dir(), os.path.basename(file))
        with open(path, "rb") as fh:
            info = check_apj(fh.read())
        self._say(f"firmware {os.path.basename(path)}: board id {info['board_id']}, {info['image_size']} bytes")
        try:                                     # refuse while armed (when it still answers MAVLink)
            m = fc.connect(port, baud, timeout=4)
            armed = _armed(m)
            m.close()
            if armed:
                return self._done(False, "the aircraft is ARMED: disarm it first")
        except fc.FCError:
            self._say("no MAVLink answer: it may already be in its bootloader")
        try_ports = ",".join(dict.fromkeys([port] + [p for p in (ports or []) if p]))
        cmd = uploader_cmd() + ["--port", try_ports, path]
        self._say("$ uploader --port " + try_ports)
        j["step"] = "restarting into the bootloader"
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        j["proc"] = p
        buf, t0 = b"", time.time()
        while True:
            if time.time() - t0 > 300:
                p.kill()
                return self._done(False, "timed out: unplug and replug the USB cable, then try again")
            ch = p.stdout.read(1)
            if not ch:
                break
            if ch in (b"\n", b"\r"):
                line = buf.decode(errors="replace").strip()
                buf = b""
                if not line:
                    continue
                for label, base in (("Erase", 0), ("Program", 10), ("Verify", 90)):
                    if line.startswith(label) and "%" in line:
                        try:
                            pct = float(line.rsplit("]", 1)[1].strip().rstrip("%"))
                            j["progress"] = int(base + pct * (0.1 if label != "Program" else 0.8))
                            j["step"] = label.lower()
                        except (ValueError, IndexError):
                            pass
                        break
                else:
                    self._say(line)
                    if "not suitable for this board" in line:   # the uploader would retry for ever
                        p.kill()
                        p.wait()
                        return self._done(False, "this firmware is for a different flight controller: nothing "
                                                 "was written. Choose the file for your board.")
            else:
                buf += ch
        rc = p.wait()
        if j["cancel"]:
            return self._done(False, "cancelled")
        if rc != 0:
            return self._done(False, "the update did not finish (see the log above). The board is safe: its "
                                     "bootloader stays; run the update again.")
        self._done(True, "ArduPlane updated. The board restarts by itself; your settings are kept.")
