"""The desktop app's build script, installer scripts and its self-test."""

import os
import shutil
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ONBOARD = os.path.join(HERE, "..")
sys.path.insert(0, ONBOARD)
sys.path.insert(0, os.path.join(ONBOARD, "packaging"))

import build_app  # noqa: E402


def test_every_file_the_app_needs_is_packed():
    files = build_app.data_files()                      # raises if one is missing
    srcs = {s for s, _ in files}
    for must in ("groundstation/static", "ardupilot", "config.yaml", "VERSION", "LICENSE",
                 "THIRD_PARTY_NOTICES.md", "models/silm_oi.tflite", "models/LICENSE-silm_oi-AGPL-3.0.txt",
                 "tools/sitl_failsafe_test.py", "training/train.py", "training/classes.yaml"):
        assert must in srcs, must
    assert not any(s.startswith(("training/datasets", "training/runs")) for s in srcs)


def test_build_options():
    args = build_app.pyinstaller_args()
    assert args[0].endswith("app_entry.py") and "--name" in args
    hidden = {args[i + 1] for i, a in enumerate(args) if a == "--hidden-import"}
    assert {"groundstation.desktop", "survey", "pymavlink.dialects.v20.ardupilotmega",
            "serial.tools.list_ports"} <= hidden
    excluded = {args[i + 1] for i, a in enumerate(args) if a == "--exclude-module"}
    assert {"torch", "cv2", "numpy"} <= excluded
    for f in ("icon.ico", "icon.icns", "icon_512.png"):
        assert os.path.getsize(os.path.join(ONBOARD, "packaging", "icons", f)) > 1000


def test_windows_installer_script():
    iss = open(os.path.join(ONBOARD, "packaging", "windows", "installer.iss")).read()
    assert "PrivilegesRequired=lowest" in iss and "{autodesktop}" in iss and "LicenseFile=" in iss
    assert "UAV-SILM-Ground-Station.exe" in iss and "OutputBaseFilename=UAV-SILM-Ground-Station-Setup-" in iss


def test_linux_install_script_passes_shellcheck():
    sc = shutil.which("shellcheck")
    if sc is None:
        pytest.importorskip("shellcheck_py", reason="needs shellcheck")
        sc = shutil.which("shellcheck")
    if sc is None:
        pytest.skip("shellcheck not found")
    r = subprocess.run([sc, os.path.join(ONBOARD, "packaging", "linux", "install.sh")],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout


def test_desktop_selftest_from_source():
    r = subprocess.run([sys.executable, "-m", "groundstation.desktop", "--selftest"], cwd=ONBOARD,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0 and "SELFTEST OK" in r.stdout, r.stdout + r.stderr


def test_runtime_finds_python_for_helper_scripts(monkeypatch):
    from groundstation import runtime
    runtime.python_cmd.cache_clear()
    assert runtime.python_cmd() == (sys.executable,)        # from source: this Python
    monkeypatch.setattr(sys, "frozen", True, raising=False)  # inside the packaged app
    runtime.python_cmd.cache_clear()
    cmd = runtime.python_cmd()
    assert cmd is None or os.path.basename(cmd[0]).startswith(("python", "py"))
    runtime.python_cmd.cache_clear()
