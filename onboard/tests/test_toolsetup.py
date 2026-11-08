"""Installing the training tools from the Ground Station (groundstation/toolsetup.py).

Real: a real Python environment is made and a small package installed from
PyPI (skipped without internet). The full ~2 GB install runs the same steps
with the real package list.
"""

import os
import socket
import sys
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

from groundstation import runtime, toolsetup, training  # noqa: E402
from groundstation.toolsetup import ToolInstall  # noqa: E402


def online(host="pypi.org"):
    try:
        socket.getaddrinfo(host, 443)
        return True
    except OSError:
        return False


def wait(job, limit=600):
    t0 = time.time()
    while job.status()["state"] == "running" and time.time() - t0 < limit:
        time.sleep(0.2)
    return job.status()


@pytest.mark.skipif(not online(), reason="needs pypi.org")
def test_real_environment_and_pip_install(tmp_path):
    venv = tmp_path / "training-venv"
    job = ToolInstall(venv=str(venv), packages=["six"], base_python=[sys.executable], cpu_torch=False)
    assert job.start()["ok"]
    assert not job.start()["ok"]                                        # one at a time
    s = wait(job)
    assert s["state"] == "done", s["log"][-15:]
    vpy = toolsetup.venv_python(str(venv))
    assert os.path.isfile(vpy)
    import subprocess
    assert subprocess.run([vpy, "-c", "import six"]).returncode == 0
    assert any("venv" in line for line in s["log"]) and any("six" in line for line in s["log"])
    s2 = ToolInstall(venv=str(venv), packages=["six"], base_python=[sys.executable], cpu_torch=False)
    s2.start()
    assert wait(s2)["state"] == "done"                                   # again: reuses the environment
    assert not any(" -m venv " in line for line in s2.status()["log"])


def test_failures_are_explained(tmp_path):
    job = ToolInstall(venv=str(tmp_path / "v"), base_python=[sys.executable, "-c", "import sys; sys.exit(1)", "--"])
    assert job.start()["ok"]
    s = wait(job)
    assert s["state"] == "failed" and "python3-venv" in s["result"] and not (tmp_path / "v").exists()
    job = ToolInstall(venv=str(tmp_path / "v"))
    import groundstation.toolsetup as ts
    orig = runtime.python_cmd
    try:
        ts.runtime.python_cmd = lambda: None
        r = job.start()
    finally:
        ts.runtime.python_cmd = orig
    assert not r["ok"] and "Installing Python" in r["message"]


@pytest.mark.skipif(not online(), reason="needs pypi.org")
def test_cancel_stops_pip(tmp_path):
    job = ToolInstall(venv=str(tmp_path / "v"), packages=["numpy==2.1.3", "--no-binary", ":all:"],
                      base_python=[sys.executable], cpu_torch=False)        # a long source build
    assert job.start()["ok"]
    t0 = time.time()
    while "training tools" not in job.status().get("step", "") and time.time() - t0 < 120:
        time.sleep(0.2)
    time.sleep(1)
    assert job.cancel()["ok"]
    s = wait(job, 60)
    assert s["state"] == "stopped"


def test_training_uses_the_installed_environment(tmp_path, monkeypatch):
    venv = tmp_path / "training-venv"
    monkeypatch.setattr(toolsetup, "venv_dir", lambda: str(venv))
    assert runtime.training_python() == runtime.python_cmd()             # none installed yet
    vpy = toolsetup.venv_python(str(venv))
    os.makedirs(os.path.dirname(vpy))
    with open(vpy, "w") as fh:
        fh.write("#!/bin/sh\nexit 1\n")                                  # a Python that can't import anything
    os.chmod(vpy, 0o755)
    assert runtime.training_python() == (vpy,)
    t = training.tools_available()
    assert t["available"] is False and t["can_install"] and "Install the training tools" in t["why"]
