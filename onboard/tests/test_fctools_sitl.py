"""Flight controller tools against ArduPilot's simulator (SITL), the real
ArduPlane code: servo reversing, battery voltage calibration, level, airspeed,
the start of the 6-position and compass calibrations, and log download.

    SILM_SITL_TESTS=1 SILM_ARDUPILOT=~/ardupilot python3 -m pytest tests/test_fctools_sitl.py
"""

import os
import sys
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, ".."))

from test_sitl_integration import PORT, Sitl, pytestmark  # noqa: E402,F401
from test_groundstation import full_project  # noqa: E402

from groundstation import fc, fctools  # noqa: E402
from silm_recon import mavmission as mm  # noqa: E402


@pytest.fixture(scope="module")
def sitl(tmp_path_factory):
    run_dir = tmp_path_factory.mktemp("sitl_tools")
    s = Sitl(str(run_dir), full_project()["mission"]["launch"])
    s.start(wipe=True)
    time.sleep(8)                                # sensors settle
    yield s
    s.stop()


def wait(jobs, states=("done", "failed", "cancelled"), timeout=60, answer=False):
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = jobs.status()
        if st["state"] in states:
            return st
        if answer and st["state"] == "waiting":
            jobs.answer(True)
        time.sleep(0.3)
    return jobs.status()


def test_servo_outputs_and_reversing(sitl):
    r = fctools.servos(PORT)
    names = {s["name"] for s in r["servos"]}
    assert r["ok"] and {"Aileron", "Elevator", "Throttle"} <= names, r
    ail = next(s for s in r["servos"] if s["name"] == "Aileron")
    flip = 0 if ail["reversed"] else 1
    assert fctools.set_reversed(PORT, ail["n"], bool(flip))["ok"]
    m = fc.connect(PORT)
    assert int(mm.get_param(m, f"SERVO{ail['n']}_REVERSED")) == flip
    m.close()
    fctools.set_reversed(PORT, ail["n"], not flip)


def test_battery_voltage_calibration(sitl):
    b = fctools.battery(PORT)
    assert b["ok"] and 5 < b["voltage"] < 60, b
    target = round(b["voltage"] * 1.05, 2)
    r = fctools.battery_cal(PORT, target)
    assert r["ok"] and abs(r["volt_mult"] - b["volt_mult"] * 1.05) < 0.05 * b["volt_mult"], r
    assert r["voltage"] is None or abs(r["voltage"] - target) < 0.3
    fctools.battery_cal(PORT, b["voltage"])                       # put it back
    with pytest.raises(ValueError):
        fctools.battery_cal(PORT, 500)


def test_level_and_airspeed_calibration(sitl):
    jobs = fctools.FCJobs()
    assert jobs.start("level", PORT)["ok"]
    st = wait(jobs)
    assert st["state"] == "done", st
    assert jobs.start("airspeed", PORT)["ok"]
    st = wait(jobs)
    assert st["state"] == "done", st


def test_accelerometer_calibration_asks_for_each_position(sitl):
    jobs = fctools.FCJobs()
    assert jobs.start("accel", PORT)["ok"]
    st = wait(jobs, ("waiting", "failed"), 30)
    assert st["state"] == "waiting" and "level" in st["prompt"], st
    jobs.answer(True)                            # level: the simulated plane is level
    t0 = time.time()
    while time.time() - t0 < 40:
        st = jobs.status()
        if st["state"] == "waiting" and st["step"] == "position 2 of 6":
            break
        time.sleep(0.3)
    assert st["step"] == "position 2 of 6" and "LEFT" in st["prompt"], st
    jobs.cancel()
    assert wait(jobs, timeout=10)["state"] in ("cancelled", "failed")


def test_compass_calibration_starts_reports_progress_and_cancels(sitl):
    jobs = fctools.FCJobs()
    assert jobs.start("compass", PORT)["ok"]
    t0 = time.time()
    while time.time() - t0 < 20 and jobs.status()["step"] != "turning":
        time.sleep(0.3)
    st = jobs.status()
    assert st["state"] == "running" and "Turn the aircraft" in st["prompt"], st
    time.sleep(3)
    jobs.cancel()
    assert wait(jobs, timeout=15)["state"] in ("cancelled", "failed")


def test_flight_logs_list_and_download(sitl, tmp_path):
    m = fc.connect(PORT)
    assert mm.set_param(m, "LOG_DISARMED", 1)    # make SITL write a log on the ground
    time.sleep(6)
    mm.set_param(m, "LOG_DISARMED", 0)
    m.close()
    time.sleep(2)
    r = fctools.log_list(PORT)
    assert r["ok"] and r["logs"], r
    log = min(r["logs"], key=lambda x: x["size"])
    jobs = fctools.FCJobs(log_dir=str(tmp_path))
    assert jobs.start("log", PORT, log_id=log["id"], size=log["size"])["ok"]
    st = wait(jobs, timeout=240)
    assert st["state"] == "done", st
    data = open(st["file"], "rb").read()
    assert len(data) == log["size"] and data[:2] == b"\xa3\x95"   # ArduPilot DataFlash header
