"""Fly the mission in ArduPilot's simulator (SITL) from the Ground Station,
with nothing to build and nothing to type.

The simulator is ArduPilot's own program, built by the ArduPilot project
for Linux and Windows and published on firmware.ardupilot.org (the same
files Mission Planner downloads). "Install the simulator" fetches it into
~/SILM/sitl (about 7 MB on Linux, 25 MB on Windows). On a Mac there is no
ready-made file: the guide explains the one-time build.

A run:
  1. writes your parameter file and mission into ~/SILM/sitl/run, copies the
     Lua script to its scripts/ folder
  2. starts the simulator at your mission's launch point, fresh (--wipe),
     with scripting on from the first start (sitl_defaults.parm)
  3. flies the mission with tools/sitl_failsafe_test.py inside this process:
     your parameters, mission, wind and the chosen scenario; every recorded
     position goes to the live map
  4. reports PASS/FAIL per check, the numbers and the track picture
One run at a time; in real time a run takes 5-8 minutes, "fast" about 2.

A developer with an ArduPilot checkout can point SILM_SITL_BIN at any
arduplane binary; ~/ardupilot/build/sitl/bin/arduplane is found by itself.
"""

from __future__ import annotations

import importlib.util
import json
import os
import platform
import shutil
import ssl
import subprocess
import sys
import threading
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ONBOARD = os.path.dirname(HERE)
TEST_TOOL = os.path.join(ONBOARD, "tools", "sitl_failsafe_test.py")
LUA = os.path.join(ONBOARD, "ardupilot", "scripts", "silm_gps_loss.lua")
DEFAULTS = os.path.join(ONBOARD, "ardupilot", "sitl_defaults.parm")
SERVER = "https://firmware.ardupilot.org"
PORT = 5760

# What to download for this computer: (files, the one to run). The Windows
# build is the Cygwin one Mission Planner uses; it needs its DLLs next to it.
_WIN_DLLS = ["cygwin1.dll", "cyggcc_s-seh-1.dll", "cygstdc++-6.dll", "cygatomic-1.dll",
             "cyggomp-1.dll", "cygiconv-2.dll", "cygintl-8.dll", "cygquadmath-0.dll", "cygssp-0.dll"]
DOWNLOADS = {
    "linux-x86_64": ([("Plane/stable/SITL_x86_64_linux_gnu/arduplane", "arduplane"),
                      ("Plane/stable/SITL_x86_64_linux_gnu/git-version.txt", "git-version.txt")],
                     "arduplane"),
    "windows-x86_64": ([("Tools/MissionPlanner/sitl/PlaneStable/ArduPlane.elf", "ArduPlane.elf"),
                        ("Tools/MissionPlanner/sitl/PlaneStable/git.txt", "git-version.txt")] +
                       [("Tools/MissionPlanner/sitl/PlaneStable/" + d.replace("+", "%2B"), d) for d in _WIN_DLLS],
                       "ArduPlane.elf"),
}

SCENARIOS = {
    # what goes wrong, in the order the tab offers them
    "jam": {"title": "GPS jammed halfway", "fault": "jam", "fault_s": 90, "turbulence": 0.0,
            "what": "The test before the first flight: the GPS signal is scrambled for 90 s mid-survey. "
                    "The aircraft must notice, turn back, land on the strip and switch itself off."},
    "disable": {"title": "GPS lost completely", "fault": "disable", "fault_s": 90, "turbulence": 0.0,
                "what": "The GPS stops answering for 90 s. Same demands as the jamming test."},
    "gusty": {"title": "Strong, gusty wind with GPS jammed", "fault": "jam", "fault_s": 90, "turbulence": 6.0,
              "wind": 10.0,
              "what": "10 m/s wind with gusts and the GPS jammed: the hardest case. Shows how far the "
                      "landing can drift when the aircraft has to feel its way home."},
    "none": {"title": "A normal flight (nothing goes wrong)", "fault": "none", "fault_s": 0, "turbulence": 0.0,
             "what": "The whole mission as planned: route, flight time and the landing. The GPS-jammed "
                     "test is still needed before the first real flight."},
}
SPEEDS = {"real": 1, "fast": 4}


def host_key() -> str:
    m = platform.machine().lower()
    arch = "x86_64" if m in ("x86_64", "amd64") else m
    if sys.platform.startswith("linux"):
        return f"linux-{arch}"
    if sys.platform == "win32":
        return f"windows-{arch}"
    if sys.platform == "darwin":
        return f"macos-{arch}"
    return f"{sys.platform}-{arch}"


def _listening(port: int) -> bool:
    """Is something listening on 127.0.0.1:port? Looked up in the system's
    connection table, never by connecting: the simulator accepts ONE
    connection on its port, and a probe would take it (on Windows it then
    refuses the real one)."""
    if sys.platform.startswith("linux"):
        hexport = f":{port:04X}"
        for f in ("/proc/net/tcp", "/proc/net/tcp6"):
            try:
                with open(f) as fh:
                    for line in fh:
                        parts = line.split()
                        if len(parts) > 3 and parts[1].endswith(hexport) and parts[3] == "0A":
                            return True
            except OSError:
                pass
        return False
    try:
        cmd = ["netstat", "-an", "-p", "tcp"] if sys.platform == "win32" else ["netstat", "-an"]
        kw = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if sys.platform == "win32" else {}
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=10, **kw).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    for line in out.splitlines():
        if ("LISTEN" in line.upper()) and (f":{port} " in line + " " or f".{port} " in line + " "):
            return True
    return False


def _ssl_context():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001
        return ssl.create_default_context()


def _load_test_tool():
    spec = importlib.util.spec_from_file_location("sitl_failsafe_test", TEST_TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class SimRunner:
    def __init__(self, ardupilot_dir: str | None = None, run_dir: str | None = None,
                 sitl_dir: str | None = None):
        self.ardupilot_dir = os.path.expanduser(ardupilot_dir or os.environ.get("SILM_ARDUPILOT", "~/ardupilot"))
        self.sitl_dir = os.path.expanduser(sitl_dir or os.environ.get("SILM_SITL_DIR", "~/SILM/sitl"))
        self.run_dir = os.path.expanduser(run_dir or os.path.join(self.sitl_dir, "run"))
        self.job: dict | None = None
        self.inst: dict | None = None          # the install job
        self._lock = threading.Lock()
        self._sitl: subprocess.Popen | None = None
        self.rows: list[dict] = []

    # ------------------------------------------------------------ the binary
    def missing_files(self) -> list[str]:
        """Downloaded files that are no longer there (an antivirus may have
        removed the unsigned program): empty when nothing was installed or
        everything is in place."""
        dl = DOWNLOADS.get(host_key())
        if not dl or not os.path.isdir(self.sitl_dir):
            return []
        names = [n for _, n in dl[0] if n != "git-version.txt"]
        present = [n for n in names if os.path.isfile(os.path.join(self.sitl_dir, n))]
        return [] if not present else [n for n in names if n not in present]

    def binary(self) -> str | None:
        """The simulator program to run, or None."""
        env = os.environ.get("SILM_SITL_BIN")
        if env and os.path.isfile(os.path.expanduser(env)):
            return os.path.expanduser(env)
        dl = DOWNLOADS.get(host_key())
        if dl:
            p = os.path.join(self.sitl_dir, dl[1])
            if os.path.isfile(p) and not self.missing_files():
                return p
        dev = os.path.join(self.ardupilot_dir, "build", "sitl", "bin", "arduplane")
        if os.path.isfile(dev) and sys.platform != "win32":
            return dev
        return None

    def version(self) -> str:
        p = os.path.join(self.sitl_dir, "git-version.txt")
        commit = ""
        try:
            for line in open(p):
                if line.startswith("APMVERSION:"):
                    return line.split(":", 1)[1].strip()
                if line.startswith("commit "):
                    commit = "commit " + line.split()[1][:10]
        except OSError:
            pass
        return commit

    def available(self) -> dict:
        b = self.binary()
        key = host_key()
        can = key in DOWNLOADS
        why = None
        gone = self.missing_files()
        if not b and gone:
            why = (f"Part of the simulator is missing from {self.sitl_dir} ({', '.join(gone)}). "
                   "An antivirus program (on Windows usually Defender) may have removed it: the "
                   "simulator is an unsigned program from the ArduPilot project. Press \"Install the "
                   "simulator\" again; if it disappears again, open Windows Security -> Virus & threat "
                   "protection -> Protection history, restore the file and allow it.")
        elif not b:
            why = ("The simulator is not on this computer yet. Press \"Install the simulator\": "
                   "it downloads ArduPilot's own simulator (no building, nothing to type)." if can else
                   "There is no ready-made simulator for this computer "
                   f"({key.replace('-', ', ')}). The guide \"The simulator on a Mac\" explains the "
                   "one-time build, or run the Ground Station on a Windows or Linux computer.")
        out = {"available": bool(b), "installed": bool(b), "can_install": can and not b, "why": why,
               "missing": gone,
               "platform": key, "binary": b, "version": self.version() if b else "",
               "run_dir": self.run_dir, "sitl_dir": self.sitl_dir, "scenarios": SCENARIOS,
               "install": self.install_status()}
        return out

    # ------------------------------------------------------------ install
    def install(self) -> dict:
        with self._lock:
            if self.inst and self.inst["state"] == "running":
                return {"ok": False, "message": "the simulator is already being installed"}
            if host_key() not in DOWNLOADS:
                return {"ok": False, "message": self.available()["why"]}
            self.inst = {"state": "running", "progress": 0.0, "log": [], "file": "", "started": time.time()}
        threading.Thread(target=self._install, daemon=True).start()
        return {"ok": True, "message": "downloading the simulator"}

    def _install(self):
        j = self.inst
        files, exe = DOWNLOADS[host_key()]
        tmp = self.sitl_dir + ".part"
        try:
            os.makedirs(tmp, exist_ok=True)                 # a partial download resumes
            ctx = _ssl_context()
            total_files = len(files)
            for i, (path, name) in enumerate(files):
                j["file"] = name
                j["log"].append(f"downloading {name}")
                self._download(SERVER + "/" + path, os.path.join(tmp, name), ctx,
                               lambda part, i=i: j.__setitem__("progress", (i + part) / total_files))
            exe_path = os.path.join(tmp, exe)
            if os.path.getsize(exe_path) < 1_000_000:
                raise RuntimeError("the downloaded simulator is too small to be right")
            os.chmod(exe_path, 0o755)
            self._stop_sitl()
            if os.path.isdir(self.sitl_dir):
                keep = os.path.join(self.sitl_dir, "run")
                for n in os.listdir(self.sitl_dir):
                    p = os.path.join(self.sitl_dir, n)
                    if p != keep:
                        shutil.rmtree(p, ignore_errors=True) if os.path.isdir(p) else os.remove(p)
            else:
                os.makedirs(self.sitl_dir)
            for n in os.listdir(tmp):
                shutil.move(os.path.join(tmp, n), os.path.join(self.sitl_dir, n))
            shutil.rmtree(tmp, ignore_errors=True)
            j["log"].append(f"installed {self.version() or exe} in {self.sitl_dir}")
            j["progress"] = 1.0
            j["state"] = "done"
        except Exception as exc:  # noqa: BLE001
            j["log"].append(f"ERROR: {exc}")
            j["state"] = "error"
            j["message"] = (f"Could not download the simulator ({exc}). Check the internet "
                            "connection and press Install again: it continues where it stopped.")

    def _download(self, url: str, dest: str, ctx, progress, tries: int = 5) -> None:
        """Fetch url to dest, resuming a partial file and retrying when the
        connection stalls (the firmware server is sometimes slow)."""
        last = None
        for attempt in range(tries):
            have = os.path.getsize(dest) if os.path.isfile(dest) else 0
            headers = {"User-Agent": "SILM-GroundStation"}
            if have:
                headers["Range"] = f"bytes={have}-"
            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=120, context=ctx) as r:
                    if have and r.status != 206:         # the server did not resume: start over
                        have = 0
                    total = have + int(r.headers.get("Content-Length") or 0)
                    with open(dest, "ab" if have else "wb") as fh:
                        got = have
                        while True:
                            chunk = r.read(256 * 1024)
                            if not chunk:
                                break
                            fh.write(chunk)
                            got += len(chunk)
                            progress(got / total if total else 0.0)
                    if total and got < total:
                        raise OSError(f"only {got} of {total} bytes arrived")
                return
            except (OSError, ValueError) as exc:         # timeouts, resets, bad length
                last = exc
                self.inst["log"].append(f"  {os.path.basename(dest)}: {exc}; trying again")
                time.sleep(2 * (attempt + 1))
        raise RuntimeError(f"{os.path.basename(dest)} could not be downloaded ({last})")

    def install_status(self) -> dict:
        j = self.inst
        if not j:
            return {"state": "idle"}
        return {"state": j["state"], "progress": round(j["progress"], 3), "file": j["file"],
                "log": j["log"][-20:], "message": j.get("message"),
                "elapsed_s": round(time.time() - j["started"])}

    def remove(self) -> dict:
        self._stop_sitl()
        shutil.rmtree(self.sitl_dir, ignore_errors=True)
        return {"ok": True, "message": "the simulator was removed"}

    # ------------------------------------------------------------ run
    def start(self, params_text: str, mission_wpl: str, launch: tuple, wind: float,
              wind_dir: float, fault: str = "jam", fault_s: float = 90.0,
              trigger_s: float = 150.0, out_dir: str | None = None, speed: str = "real",
              turbulence: float = 0.0, scenario: str | None = None) -> dict:
        with self._lock:
            if self.job and self.job["state"] == "running":
                return {"ok": False, "message": "a simulation is already running"}
            if self.inst and self.inst["state"] == "running":
                return {"ok": False, "message": "wait for the simulator to finish installing"}
            av = self.available()
            if not av["available"]:
                return {"ok": False, "message": av["why"]}
            if _listening(PORT) and self._sitl is None:
                return {"ok": False, "message": f"something else is using port {PORT} (another simulator?). "
                                                "Close it and try again."}
            out_dir = out_dir or os.path.join(self.sitl_dir, "results", time.strftime("sim_%Y%m%d_%H%M%S"))
            self.job = {"state": "running", "log": [], "result": None, "out": out_dir,
                        "started": time.time(), "step": "preparing", "scenario": scenario or fault,
                        "fault": fault, "speed": SPEEDS.get(speed, 1), "launch": list(launch[:2])}
            self.rows = []
        os.makedirs(os.path.join(self.run_dir, "scripts"), exist_ok=True)
        shutil.copyfile(LUA, os.path.join(self.run_dir, "scripts", "silm_gps_loss.lua"))
        shutil.copyfile(DEFAULTS, os.path.join(self.run_dir, "silm_sitl.parm"))
        pf = os.path.join(self.run_dir, "ground_station.param")
        mf = os.path.join(self.run_dir, "ground_station.waypoints")
        with open(pf, "w") as fh:
            fh.write(params_text)
        with open(mf, "w") as fh:
            fh.write(mission_wpl)
        args = dict(pf=pf, mf=mf, launch=launch, wind=wind, wind_dir=wind_dir, fault=fault,
                    fault_s=fault_s, trigger_s=trigger_s, out=out_dir, turbulence=turbulence,
                    speedup=SPEEDS.get(speed, 1))
        threading.Thread(target=self._run, args=(args,), daemon=True).start()
        return {"ok": True, "message": "simulation started", "out": out_dir}

    def _log(self, line: str):
        j = self.job
        j["log"].append(line.rstrip("\n"))
        if len(j["log"]) > 3000:
            del j["log"][:1000]

    def _on_row(self, row: dict):
        self.rows.append(row)

    def _start_sitl(self, launch, speedup=1):
        self._stop_sitl()
        b = self.binary()
        loc = f"{launch[0]:.7f},{launch[1]:.7f},{launch[2] if len(launch) > 2 else 0:.1f},0"
        cmd = [b, "--model", "plane", "--home", loc, "--defaults", "silm_sitl.parm", "--wipe",
               "--speedup", str(int(speedup))]
        self._log("$ " + " ".join([os.path.basename(b)] + cmd[1:]))
        kw = {}
        if sys.platform == "win32":
            kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        else:
            kw["start_new_session"] = True
        gone = self.missing_files()
        if gone or not os.path.isfile(b):
            raise RuntimeError(f"the simulator's files are missing ({', '.join(gone) or b}): an antivirus "
                               "may have removed them. Press \"Install the simulator\" again.")
        try:
            with open(os.path.join(self.run_dir, "simulator.log"), "wb") as logf:
                self._sitl = subprocess.Popen(cmd, cwd=self.run_dir, stdout=logf, stderr=subprocess.STDOUT, **kw)
        except OSError as exc:
            raise RuntimeError(f"could not start {b}: {exc}. If an antivirus blocks it, allow it "
                               "(Windows Security -> Protection history) and try again.") from exc
        t0 = time.time()
        while time.time() - t0 < 60:
            if _listening(PORT):
                time.sleep(1.5)
                return True
            if self._sitl.poll() is not None:
                break
            time.sleep(0.5)
        try:
            tail = open(os.path.join(self.run_dir, "simulator.log"), errors="replace").read()[-600:]
            if tail.strip():
                self._log("simulator said: " + tail.strip())
        except OSError:
            pass
        return False

    def _stop_sitl(self):
        p = self._sitl
        if p is not None and p.poll() is None:
            try:
                p.terminate()
                p.wait(10)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    p.kill()
                except OSError:
                    pass
        self._sitl = None
        t0 = time.time()
        while _listening(PORT) and time.time() - t0 < 10:
            time.sleep(0.3)

    def _test(self, a) -> int:
        tool = _load_test_tool()
        tool.set_hooks(log=self._log, on_row=self._on_row)
        import argparse
        args = argparse.Namespace(connect=f"tcp:127.0.0.1:{PORT}", params=a["pf"], fault=a["fault"],
                                  fault_s=a["fault_s"], mission=a["mf"], trigger_m=700.0, out_m=1200.0,
                                  trigger_s=a["trigger_s"], bearing=45.0, fence_m=1500.0,
                                  land_radius_m=150.0, alt=100.0, wind=a["wind"], wind_dir=a["wind_dir"],
                                  timeout_s=1500.0, out=a["out"], turbulence=a.get("turbulence", 0.0))
        try:
            return int(tool.run(args) or 0)
        except SystemExit as exc:
            self._log(f"ERROR: {exc}")
            return 1
        except Exception as exc:  # noqa: BLE001
            self._log(f"ERROR: {exc!r}")
            return 1

    def _run(self, a):
        j = self.job
        try:
            j["step"] = "starting the simulator"
            if not self._start_sitl(a["launch"], a["speedup"]):
                raise RuntimeError("the simulator did not start (see the progress log)")
            j["step"] = "flying the mission"
            code = self._test(a)
            if code == 2:      # the script did not load: one restart
                self._log("--- the script did not load on the first start: restarting ---")
                j["step"] = "restarting the simulator"
                if not self._start_sitl(a["launch"], a["speedup"]):
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
            if j["state"] == "running":
                j["state"] = "passed" if code == 0 else "failed"
        except Exception as exc:  # noqa: BLE001
            self._log(f"ERROR: {exc}")
            j["state"] = "error"
        finally:
            j["step"] = "done"
            self._stop_sitl()

    def stop(self) -> dict:
        if self.job and self.job["state"] == "running":
            self.job["state"] = "stopped"
        self._stop_sitl()
        return {"ok": True}

    def status(self, tail: int = 200) -> dict:
        j = self.job
        if not j:
            return {"state": "idle", **self.available()}
        return {"state": j["state"], "step": j["step"], "log": j["log"][-tail:],
                "result": j["result"], "out": j["out"], "scenario": j["scenario"], "fault": j["fault"],
                "speed": j["speed"], "track_png": os.path.join(j["out"], "track.png"),
                "elapsed_s": round(time.time() - j["started"]), **self.available()}

    def live(self, since: int = 0) -> dict:
        """Recorded positions after index `since`, for the live map."""
        j = self.job
        rows = self.rows[since:]
        return {"state": j["state"] if j else "idle", "step": j["step"] if j else "",
                "next": since + len(rows), "launch": j["launch"] if j else None,
                "rows": [[r["t"], r["true_lat"], r["true_lon"], r["est_lat"], r["est_lon"],
                          round(r["alt"]), r["phase"], r["mode"], r["seq"], r["armed"]] for r in rows]}


def commands_for(project_name: str, launch, wind, wind_dir) -> str:
    """The same run by hand, for a developer with their own simulator."""
    lat, lon = launch if launch else (59.40, 24.70)
    return (f"# 1. in ~/SILM/sitl/run (scripts/silm_gps_loss.lua inside):\n"
            f"arduplane --model plane --home {lat:.6f},{lon:.6f},0,0 --defaults silm_sitl.parm --wipe\n"
            f"# 2. in a second terminal, from the onboard folder:\n"
            f"python3 tools/sitl_failsafe_test.py --connect tcp:127.0.0.1:5760 --params "
            f"ground_station.param --mission ground_station.waypoints --trigger-s 150 "
            f"--fault jam --wind {wind:g} --wind-dir {wind_dir:g} --out sim_{project_name}\n")
