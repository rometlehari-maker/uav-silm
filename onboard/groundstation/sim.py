"""Fly the mission in ArduPilot's simulator (SITL) from the Ground Station.

Needs ArduPilot built for SITL on this computer (Linux, or WSL2 on Windows:
SITL_HOWTO.md). The runner:
  1. writes your parameter file and mission into the SITL folder, copies the
     Lua script to its scripts/ folder
  2. starts SITL at your mission's launch point (no MAVProxy, nothing to type)
  3. runs tools/sitl_failsafe_test.py with your parameters, mission and wind;
     the first time, the script needs one SITL restart to load - done
     automatically
  4. reports PASS/FAIL per check, the numbers and the track picture
One run at a time; a run takes 4-6 minutes.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ONBOARD = os.path.dirname(HERE)
TEST_TOOL = os.path.join(ONBOARD, "tools", "sitl_failsafe_test.py")
LUA = os.path.join(ONBOARD, "ardupilot", "scripts", "silm_gps_loss.lua")


def _listening(port: int) -> bool:
    hexport = f":{port:04X} "
    for f in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(f) as fh:
                for line in fh:
                    parts = line.split()
                    if len(parts) > 3 and parts[1].endswith(hexport.strip()) and parts[3] == "0A":
                        return True
        except OSError:
            pass
    return False


class SimRunner:
    def __init__(self, ardupilot_dir: str | None = None, run_dir: str | None = None):
        self.ardupilot_dir = os.path.expanduser(ardupilot_dir or os.environ.get(
            "SILM_ARDUPILOT", "~/ardupilot"))
        self.run_dir = os.path.expanduser(run_dir or os.environ.get("SILM_SITL_DIR", "~/sitlrun"))
        self.job: dict | None = None
        self._lock = threading.Lock()
        self._sitl = None

    # ------------------------------------------------------------ setup
    def sim_vehicle(self) -> str:
        return os.path.join(self.ardupilot_dir, "Tools", "autotest", "sim_vehicle.py")

    def available(self) -> dict:
        sv = self.sim_vehicle()
        binp = os.path.join(self.ardupilot_dir, "build", "sitl", "bin", "arduplane")
        ok = sys.platform.startswith("linux") and os.path.isfile(sv) and os.path.isfile(binp)
        why = None
        if not sys.platform.startswith("linux"):
            why = ("The simulator runs on Linux. On Windows, run the Ground Station inside "
                   "Ubuntu (WSL2) to use this tab, or use the commands shown below.")
        elif not os.path.isfile(sv):
            why = f"ArduPilot not found at {self.ardupilot_dir} (SITL_HOWTO.md part A)"
        elif not os.path.isfile(binp):
            why = f"ArduPlane SITL is not built: cd {self.ardupilot_dir} && ./waf plane"
        return {"available": ok, "why": why, "ardupilot_dir": self.ardupilot_dir,
                "run_dir": self.run_dir}

    # ------------------------------------------------------------ run
    def start(self, params_text: str, mission_wpl: str, launch: tuple, wind: float,
              wind_dir: float, fault: str = "jam", fault_s: float = 90.0,
              trigger_s: float = 150.0, out_dir: str | None = None) -> dict:
        with self._lock:
            if self.job and self.job["state"] == "running":
                return {"ok": False, "message": "a simulation is already running"}
            av = self.available()
            if not av["available"]:
                return {"ok": False, "message": av["why"]}
            out_dir = out_dir or os.path.join(self.run_dir, "results",
                                              time.strftime("sim_%Y%m%d_%H%M%S"))
            self.job = {"state": "running", "log": [], "result": None, "out": out_dir,
                        "started": time.time(), "step": "preparing"}
        os.makedirs(os.path.join(self.run_dir, "scripts"), exist_ok=True)
        shutil.copyfile(LUA, os.path.join(self.run_dir, "scripts", "silm_gps_loss.lua"))
        pf = os.path.join(self.run_dir, "ground_station.param")
        mf = os.path.join(self.run_dir, "ground_station.waypoints")
        with open(pf, "w") as fh:
            fh.write(params_text)
        with open(mf, "w") as fh:
            fh.write(mission_wpl)
        args = dict(pf=pf, mf=mf, launch=launch, wind=wind, wind_dir=wind_dir, fault=fault,
                    fault_s=fault_s, trigger_s=trigger_s, out=out_dir)
        threading.Thread(target=self._run, args=(args,), daemon=True).start()
        return {"ok": True, "message": "simulation started", "out": out_dir}

    def _log(self, line: str):
        j = self.job
        j["log"].append(line.rstrip("\n"))
        if len(j["log"]) > 3000:
            del j["log"][:1000]

    def _start_sitl(self, launch):
        self._stop_sitl()
        loc = f"{launch[0]:.7f},{launch[1]:.7f},{launch[2] if len(launch) > 2 else 0:.1f},0"
        cmd = [sys.executable, self.sim_vehicle(), "-v", "ArduPlane", "--no-rebuild",
               "--no-mavproxy", f"--custom-location={loc}"]
        self._log("$ " + " ".join(cmd[1:]))
        self._sitl = subprocess.Popen(cmd, cwd=self.run_dir, stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL, start_new_session=True)
        t0 = time.time()
        while time.time() - t0 < 60:
            if _listening(5760):
                time.sleep(2)
                return True
            if self._sitl.poll() is not None:
                break
            time.sleep(0.5)
        return False

    def _stop_sitl(self):
        if self._sitl is not None and self._sitl.poll() is None:
            try:
                os.killpg(self._sitl.pid, signal.SIGTERM)
            except OSError:
                pass
            try:
                self._sitl.wait(10)
            except subprocess.TimeoutExpired:
                os.killpg(self._sitl.pid, signal.SIGKILL)
        self._sitl = None
        # sim_vehicle leaves the simulator running in its own process
        subprocess.run(["pkill", "-x", "arduplane"], capture_output=True)
        t0 = time.time()
        while _listening(5760) and time.time() - t0 < 10:
            time.sleep(0.3)

    def _test(self, a) -> int:
        cmd = [sys.executable, "-u", TEST_TOOL, "--connect", "tcp:127.0.0.1:5760",
               "--params", a["pf"], "--mission", a["mf"], "--fault", a["fault"],
               "--fault-s", str(a["fault_s"]), "--trigger-s", str(a["trigger_s"]),
               "--wind", str(a["wind"]), "--wind-dir", str(a["wind_dir"]), "--out", a["out"]]
        self._log("$ " + " ".join(os.path.basename(c) if i < 2 else c for i, c in enumerate(cmd)))
        p = subprocess.Popen(cmd, cwd=ONBOARD, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, bufsize=1)
        for line in p.stdout:
            if "Battery" in line and "Arm:" in line:
                continue
            self._log(line)
        return p.wait()

    def _run(self, a):
        j = self.job
        try:
            j["step"] = "starting the simulator"
            if not self._start_sitl(a["launch"]):
                raise RuntimeError("the simulator did not start (see SITL_HOWTO.md)")
            j["step"] = "flying the mission"
            code = self._test(a)
            if code == 2:      # the Lua script only loads after a restart (first run)
                self._log("--- the script needs one simulator restart to load: restarting ---")
                j["step"] = "restarting the simulator (first run)"
                if not self._start_sitl(a["launch"]):
                    raise RuntimeError("the simulator did not restart")
                j["step"] = "flying the mission"
                code = self._test(a)
            res = None
            rp = os.path.join(a["out"], "result.json")
            if os.path.isfile(rp):
                with open(rp) as fh:
                    res = json.load(fh)
            j["result"] = res
            j["exit_code"] = code
            j["state"] = "passed" if code == 0 else "failed"
        except Exception as exc:
            self._log(f"ERROR: {exc}")
            j["state"] = "error"
        finally:
            j["step"] = "done"
            self._stop_sitl()

    def stop(self) -> dict:
        self._stop_sitl()
        if self.job and self.job["state"] == "running":
            self.job["state"] = "stopped"
        return {"ok": True}

    def status(self, tail: int = 200) -> dict:
        j = self.job
        if not j:
            return {"state": "idle", **self.available()}
        return {"state": j["state"], "step": j["step"], "log": j["log"][-tail:],
                "result": j["result"], "out": j["out"],
                "track_png": os.path.join(j["out"], "track.png"),
                "elapsed_s": round(time.time() - j["started"]), **self.available()}


def commands_for(project_name: str, launch, wind, wind_dir) -> str:
    """What to type in Ubuntu (WSL2) when the Ground Station runs on Windows."""
    lat, lon = launch if launch else (59.40, 24.70)
    return (f"# In Ubuntu, terminal 1:\n"
            f"cd ~/sitlrun && ~/ardupilot/Tools/autotest/sim_vehicle.py -v ArduPlane --console "
            f"--custom-location={lat:.6f},{lon:.6f},0,0\n"
            f"# terminal 2 (copy ground_station.param and ground_station.waypoints from the\n"
            f"# 'Download files' buttons into ~/uav-silm/onboard first):\n"
            f"cd ~/uav-silm/onboard && python3 tools/sitl_failsafe_test.py --params "
            f"ground_station.param --mission ground_station.waypoints --trigger-s 150 "
            f"--fault jam --wind {wind:g} --wind-dir {wind_dir:g} --out sim_{project_name}\n")
