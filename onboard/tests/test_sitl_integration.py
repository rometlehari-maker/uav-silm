"""The whole chain in ArduPilot's simulator, as on the real aircraft.

A FACTORY-FRESH simulated flight controller (wiped settings, no script) is
set up only by the Ground Station's own code - parameters over MAVLink, the
Lua script over MAVFTP, a reboot, the mission - and then flies that mission
with the GPS jammed halfway. Every check must pass, with no manual step
(the simulator's 3S battery is matched to the 4S parameters automatically).

Slow (about 6 minutes) and needs ArduPilot SITL:
    SILM_SITL_TESTS=1 SILM_ARDUPILOT=~/ardupilot python3 -m pytest tests/test_sitl_integration.py
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import time

import pytest

from test_groundstation import full_project

from groundstation import fc
from groundstation import project as P
from groundstation.app import Station
from groundstation.sim import _listening

ARDU = os.path.expanduser(os.environ.get("SILM_ARDUPILOT", "~/ardupilot"))
SIM_VEHICLE = os.path.join(ARDU, "Tools", "autotest", "sim_vehicle.py")
ONBOARD = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

pytestmark = pytest.mark.skipif(
    not (os.environ.get("SILM_SITL_TESTS") and os.path.isfile(SIM_VEHICLE)),
    reason="set SILM_SITL_TESTS=1 (and SILM_ARDUPILOT) to fly in ArduPilot SITL")


class Sitl:
    def __init__(self, run_dir, launch):
        self.run_dir, self.launch, self.p = run_dir, launch, None

    def start(self, wipe=False):
        self.stop()
        cmd = [sys.executable, SIM_VEHICLE, "-v", "ArduPlane", "--no-rebuild", "--no-mavproxy",
               f"--custom-location={self.launch[0]},{self.launch[1]},0,0"] + (["-w"] if wipe else [])
        self.p = subprocess.Popen(cmd, cwd=self.run_dir, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, start_new_session=True)
        t0 = time.time()
        while not _listening(5760):
            assert time.time() - t0 < 90, "SITL did not start"
            time.sleep(0.5)
        time.sleep(3)

    def stop(self):
        if self.p and self.p.poll() is None:
            os.killpg(self.p.pid, signal.SIGTERM)
            self.p.wait(10)
        subprocess.run(["pkill", "-x", "arduplane"], capture_output=True)
        t0 = time.time()
        while _listening(5760) and time.time() - t0 < 15:
            time.sleep(0.3)


@pytest.fixture
def sitl(tmp_path):
    run_dir = tmp_path / "sitl"
    run_dir.mkdir()
    s = Sitl(str(run_dir), full_project()["mission"]["launch"])
    yield s
    s.stop()


PORT = "tcp:127.0.0.1:5760"


def test_fresh_flight_controller_set_up_by_the_ground_station_then_flies(sitl, tmp_path):
    station = Station(library=str(tmp_path / "lib"), projects=str(tmp_path / "proj"))
    project = full_project()                 # 4S battery, hand launch, wind 5 m/s from 250
    out = station.outputs(project)

    sitl.start(wipe=True)                    # factory-fresh: no parameters, no script
    info = fc.info(PORT)
    assert info["armed"] is False and info["script_loaded"] is False

    # 1. parameters, each read back
    params = P.read_param_text(out["params_text"])
    r = fc.send_params(PORT, params)
    assert r["failed"] == [] and r["not_on_aircraft"] == [], r
    assert r["reboot_needed"]

    # 2. the Lua script over MAVFTP, read back
    assert not os.path.exists(os.path.join(sitl.run_dir, "scripts", "silm_gps_loss.lua"))
    r = fc.send_script(PORT, os.path.join(ONBOARD, "ardupilot", "scripts", "silm_gps_loss.lua"))
    assert r["ok"], r
    assert os.path.isfile(os.path.join(sitl.run_dir, "scripts", "silm_gps_loss.lua"))

    # 3. reboot: the script runs, the parameters stayed
    sitl.start()
    info = fc.info(PORT)
    assert info["script_loaded"] and info["scripting_enabled"], info

    # 4. the mission, read back identical
    r = fc.send_mission(PORT, out["plan"]["items"], fence_m=out["plan"]["fence_needed_m"])
    assert r["ok"] and "identical" in r["message"] and "WARNING" not in r["message"], r

    # 5. fly it: GPS jammed for 90 s halfway through the survey
    mission = tmp_path / "m.waypoints"
    mission.write_text(out["mission_wpl"])
    pfile = tmp_path / "a.param"
    pfile.write_text(out["params_text"])
    res_dir = tmp_path / "result"
    w = project["wind"]
    cmd = [sys.executable, "-u", os.path.join(ONBOARD, "tools", "sitl_failsafe_test.py"),
           "--connect", PORT, "--params", str(pfile), "--mission", str(mission),
           "--fault", "jam", "--fault-s", "90", "--trigger-s", "150",
           "--wind", str(w["speed_mps"]), "--wind-dir", str(w["from_deg"]), "--out", str(res_dir)]
    run = subprocess.run(cmd, cwd=ONBOARD, capture_output=True, text=True, timeout=1500)
    log = run.stdout + run.stderr
    shots = os.environ.get("SILM_SITL_RESULTS")
    if shots:
        shutil.copytree(res_dir, os.path.join(shots, "integration"), dirs_exist_ok=True)
        open(os.path.join(shots, "integration", "log.txt"), "w").write(log)
    assert "could not arm" not in log, log[-3000:]
    assert "Battery" not in "".join(ln for ln in log.splitlines() if "PreArm" in ln), \
        "a battery pre-arm error appeared"
    res = json.load(open(res_dir / "result.json"))
    assert res["passed"], json.dumps(res["checks"]) + "\n" + log[-3000:]
