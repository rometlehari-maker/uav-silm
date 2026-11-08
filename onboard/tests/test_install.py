"""The Pi installer (pi/install.sh) and the service files it installs.

The installer can't really run outside a Raspberry Pi, so its --dry-run mode
is run here: every step must be reached and print the commands it would run.
"""

import os
import re
import shutil
import subprocess

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ONBOARD = os.path.abspath(os.path.join(HERE, ".."))
INSTALL = os.path.join(ONBOARD, "pi", "install.sh")


def dry_run(*args):
    r = subprocess.run(["bash", INSTALL, "--dry-run", "--skip-apt", *args],
                       capture_output=True, text=True, timeout=60, cwd=ONBOARD)
    return r.returncode, r.stdout + r.stderr


def test_dry_run_reaches_every_step_and_changes_nothing(tmp_path):
    code, out = dry_run("--ssid", "SILM-test", "--password", "secret123")
    assert code == 0, out
    for n in range(2, 10):
        assert f"{n}/9" in out
    assert "silm-recon.service silm-agent.service" in out
    assert "ssid SILM-test mode ap" in out and "10.42.0.1/24" in out
    assert "LABEL=SILM /mnt/silm_usb" in out
    assert "NOPASSWD: /usr/bin/systemctl restart silm-recon" in out
    assert "nmcli radio wifi off" in out
    assert "run.py --check" in out


def test_camera_network_and_serial_port_are_set_up():
    code, out = dry_run("--password", "secret123")
    assert code == 0, out
    assert "con-name siyi-camera" in out and "192.168.144.20/24" in out
    assert "do_serial_hw 0" in out and "do_serial_cons 1" in out
    code, out = dry_run("--password", "secret123", "--no-camera-net")
    assert code == 0 and "skipped (--no-camera-net)" in out and "siyi-camera autoconnect" not in out


def test_no_hotspot_option():
    code, out = dry_run("--no-hotspot")
    assert code == 0 and "skipped (--no-hotspot)" in out and "mode ap" not in out


def test_unknown_option_is_refused():
    code, out = dry_run("--bogus")
    assert code == 2 and "unknown option" in out


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck not installed")
def test_installer_passes_shellcheck():
    r = subprocess.run(["shellcheck", "-S", "warning", INSTALL], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout


@pytest.mark.parametrize("svc", ["silm-recon", "silm-agent"])
def test_service_templates(svc, tmp_path):
    text = open(os.path.join(ONBOARD, "systemd", f"{svc}.service")).read()
    body = "\n".join(ln for ln in text.splitlines() if not ln.startswith("#"))
    assert set(re.findall(r"@[A-Z]+@", body)) == {"@USER@", "@ONBOARD@", "@VENV@"}
    assert "Restart=always" in body and "MemoryMax=" in body
    unit = tmp_path / f"{svc}.service"
    unit.write_text(body.replace("@USER@", "root").replace("@ONBOARD@", ONBOARD)
                    .replace("@VENV@", "/usr"))
    if shutil.which("systemd-analyze"):
        r = subprocess.run(["systemd-analyze", "verify", str(unit)], capture_output=True, text=True)
        bad = [ln for ln in (r.stdout + r.stderr).splitlines()
               if str(unit) in ln and ("nknown" in ln or "rror" in ln or "nvalid" in ln)]
        assert not bad, bad


def test_model_checksum_in_installer_matches_the_known_download():
    text = open(INSTALL).read()
    assert "a809cd290b4d6a2e8a9d5dad076e0bd695b8091974e0eed1052b480b2f21b6dc" in text
    assert "coco_ssd_mobilenet_v1_quant.tflite" in text
