"""Install the training tools from the Ground Station (Teach new objects tab).

Training runs Ultralytics YOLO and exports with LiteRT; together 2-6 GB
(the most on Linux, where PyTorch brings NVIDIA's libraries).
This makes a separate Python environment for them, ~/SILM/training-venv, with
the Python found on this computer, so nothing else on the computer changes.
Removing that folder removes them again.

Needs a Python 3.9+ on the computer (from source the Ground Station's own; the
packaged app looks for one) and internet (pypi.org; on Linux without an NVIDIA
card also download.pytorch.org for the small CPU-only PyTorch, optional).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time

from groundstation import runtime

PACKAGES = ["ultralytics", "litert-torch", "ai-edge-litert", "ai-edge-quantizer"]
CPU_TORCH = ["torch", "torchvision", "--index-url", "https://download.pytorch.org/whl/cpu"]
VERIFY = "import ultralytics, litert_torch; print('ultralytics', ultralytics.__version__)"


def venv_dir() -> str:
    return os.path.join(os.path.expanduser("~"), "SILM", "training-venv")


def venv_python(venv: str | None = None) -> str:
    venv = venv or venv_dir()
    return os.path.join(venv, "Scripts", "python.exe") if os.name == "nt" else os.path.join(venv, "bin", "python")


def has_nvidia() -> bool:
    return shutil.which("nvidia-smi") is not None


class ToolInstall:
    """One install at a time, in the background, polled by the page."""

    def __init__(self, venv: str | None = None, packages=None, base_python=None, cpu_torch=None):
        self.venv = venv
        self.packages = list(packages or PACKAGES)
        self._base = base_python                    # tests: a fixed interpreter
        self.cpu_torch = cpu_torch                  # None: decide (Linux without NVIDIA)
        self._lock = threading.Lock()
        self._thread = None
        self._proc = None
        self._cancel = threading.Event()
        self.state = {"state": "idle"}

    def status(self) -> dict:
        with self._lock:
            return dict(self.state, log=list(self.state.get("log", []))[-200:])

    def start(self) -> dict:
        if self._thread and self._thread.is_alive():
            return {"ok": False, "message": "the install is already running"}
        base = self._base or (runtime.python_cmd() and list(runtime.python_cmd()))
        if not base:
            return {"ok": False, "message": "no Python 3 found on this computer: install it first "
                                            "(see the guide 'Installing Python'), then press this again"}
        self._cancel.clear()
        self.state = {"state": "running", "step": "Starting", "progress": 0, "log": [], "result": "",
                      "started": time.time()}
        self._thread = threading.Thread(target=self._run, args=(list(base),), daemon=True)
        self._thread.start()
        return {"ok": True, "message": "installing the training tools"}

    def cancel(self) -> dict:
        if not (self._thread and self._thread.is_alive()):
            return {"ok": False, "message": "nothing is being installed"}
        self._cancel.set()
        p = self._proc
        if p and p.poll() is None:
            p.terminate()
        return {"ok": True, "message": "stopping"}

    # ------------------------------------------------------------ the job
    def _log(self, line: str) -> None:
        line = line.rstrip()
        if line:
            with self._lock:
                self.state["log"].append(line)

    def _set(self, step: str, progress: int) -> None:
        with self._lock:
            self.state.update(step=step, progress=progress)

    def _cmd(self, cmd: list[str], timeout: float = 3600) -> int:
        self._log("$ " + " ".join(os.path.basename(c) if i == 0 else c for i, c in enumerate(cmd)))
        env = dict(os.environ, PIP_DISABLE_PIP_VERSION_CHECK="1", PYTHONUNBUFFERED="1")
        self._proc = p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                          errors="replace", env=env,
                                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        t0 = time.time()
        for line in p.stdout:
            self._log(line)
            if time.time() - t0 > timeout:
                p.kill()
        code = p.wait()
        self._proc = None
        if self._cancel.is_set():
            raise RuntimeError("stopped")
        return code

    def _run(self, base: list[str]) -> None:
        venv = self.venv or venv_dir()
        vpy = venv_python(venv)
        try:
            if not os.path.isfile(vpy):
                self._set("Making a Python environment for the tools", 3)
                os.makedirs(os.path.dirname(venv), exist_ok=True)
                if self._cmd(base + ["-m", "venv", venv], 300) != 0:
                    shutil.rmtree(venv, ignore_errors=True)
                    raise RuntimeError(
                        "Python could not make an environment. On Ubuntu/Debian install it first: "
                        "sudo apt install python3-venv (then press Install again)")
            self._set("Updating the installer (pip)", 8)
            if self._cmd([vpy, "-m", "pip", "install", "--upgrade", "pip"], 600) != 0:
                raise RuntimeError("pip could not update itself: is this computer on the internet?")
            cpu = self.cpu_torch if self.cpu_torch is not None else (sys.platform.startswith("linux")
                                                                     and not has_nvidia())
            if cpu:
                self._set("PyTorch for the processor (no NVIDIA card found; ~200 MB)", 15)
                if self._cmd([vpy, "-m", "pip", "install", "--no-cache-dir"] + CPU_TORCH, 1800) != 0:
                    self._log("(the CPU-only PyTorch is not reachable; the standard one is installed "
                              "instead - larger, works the same)")
            self._set("Downloading and installing the training tools (2-6 GB, 5 minutes to an hour)", 25)
            # --no-cache-dir: pip would otherwise keep a second copy of every
            # download (several GB), doubling the disk space this needs
            if self._cmd([vpy, "-m", "pip", "install", "--no-cache-dir"] + self.packages, 7200) != 0:
                with self._lock:
                    tail = " / ".join(x.strip() for x in self.state["log"][-2:])
                hint = ("not enough disk space: free about 8 GB and press "
                        "Install again" if "No space left" in tail else "often no internet: the last lines say why")
                raise RuntimeError(f"the install stopped ({hint}): {tail}")
            self._set("Checking", 95)
            if self.packages == PACKAGES and self._cmd([vpy, "-c", VERIFY], 300) != 0:
                raise RuntimeError("the tools were installed but don't start (see the messages)")
            runtime.forget()
            with self._lock:
                self.state.update(state="done", step="Done", progress=100,
                                  result=f"The training tools are installed (in {venv}). You can train now.")
        except Exception as e:  # noqa: BLE001
            with self._lock:
                self.state.update(state="stopped" if self._cancel.is_set() else "failed", result=str(e))
