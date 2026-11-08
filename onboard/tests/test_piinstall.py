"""Installing the aircraft computer from the Ground Station over SSH
(groundstation/piinstall.py).

The job logic runs against a scripted SSH client here. The same job against a
real SSH server (real sudo prompts, SFTP, install.sh --dry-run) runs when
SILM_SSH_TEST=user:password@host:port names one (a throw-away user with sudo).
"""

import io
import os
import sys
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ONBOARD = os.path.join(HERE, "..")
sys.path.insert(0, ONBOARD)

from groundstation import piinstall  # noqa: E402
from groundstation.piinstall import PiInstall  # noqa: E402

PROMPT = b"[sudo] password for pi: "


class FakeChan:
    def get_pty(self, **kw):
        pass

    def settimeout(self, t):
        pass

    def exec_command(self, cmd):
        self.client.commands.append(cmd)

    def send(self, data):
        self.sent.append(data)
        self.client.typed.append(data)

    def recv(self, n):
        if self.closed:
            return b""
        if not self.chunks:
            return b""
        c = self.chunks[0]
        if c == "WAIT":                         # wait for the password to be typed
            if not self.sent:
                time.sleep(0.01)
                raise TimeoutError
            self.chunks.pop(0)
            return self.recv(n)
        if c == "HANG":                         # a long step, until cancelled
            time.sleep(0.05)
            raise TimeoutError
        return self.chunks.pop(0)

    def recv_exit_status(self):
        return self.code

    def close(self):
        self.closed = True


class FakeClient:
    def __init__(self, install_lines=None, install_code=0, sudo_ok=True, internet=True, hang=False):
        self.commands, self.typed, self.uploaded, self.closed = [], [], {}, False
        self.install_lines, self.install_code = install_lines, install_code
        self.sudo_ok, self.internet, self.hang = sudo_ok, internet, hang

    def script(self, cmd):
        if cmd.startswith("sudo") and cmd.endswith("-v && echo 'sudo OK'"):
            return ([PROMPT, "WAIT", b"\r\nsudo OK\r\n"], 0) if self.sudo_ok else \
                ([b"pi is not in the sudoers file.\r\n"], 1)
        if "urlopen" in cmd:
            return ([b"internet OK\n"], 0) if self.internet else ([b"URLError\n"], 1)
        if cmd.startswith("python3 -c"):
            return [b"software 9.9.9 in /home/pi/uav-silm/onboard\n"], 0
        if "pi/install.sh" in cmd:
            if self.hang:
                return [b"\x1b[1m==> 1/9 System packages\x1b[0m\n", "HANG"], 0
            return (self.install_lines or [
                b"\x1b[1m==> 1/9 System packages\x1b[0m\r\n", PROMPT, "WAIT",
                b"\r\n    OK packages installed\r\n==> 6/9 Wi-Fi hotspot\r\n",
                b"    [debug] psk aircraft-secret\r\n==> 9/9 Checking the configuration\r\n", b"Done."]
            ), self.install_code
        if "reboot" in cmd:
            return [], 0
        raise AssertionError(cmd)

    def get_transport(self):
        return self

    def open_session(self):
        return _Session(self)

    def open_sftp(self):
        return _Sftp(self)

    def close(self):
        self.closed = True


class _Session(FakeChan):
    """One command's channel: plays the client's script for it."""
    def __init__(self, client):
        self.client, self.sent, self.closed, self.chunks, self.code = client, [], False, [], 0

    def exec_command(self, cmd):
        self.client.commands.append(cmd)
        self.chunks, self.code = self.client.script(cmd)


class _Sftp:
    def __init__(self, client):
        self.client = client

    def open(self, name, mode):
        client = self.client

        class W(io.BytesIO):
            def __exit__(self, *a):
                client.uploaded[name] = self.getvalue()
                return super().__exit__(*a)
        return W()

    def close(self):
        pass


def run_job(client, **kw):
    job = PiInstall(connect=lambda h, u, p, port=22: client, package=lambda: (b"PK-package", "9.9.9"))
    args = dict(host="silm.local", user="pi", password="pi-login-pw", hotspot_password="aircraft-secret")
    args.update(kw)
    r = job.start(**args)
    assert r["ok"], r
    t0 = time.time()
    while job.status()["state"] == "running" and time.time() - t0 < 10:
        time.sleep(0.02)
    return job, job.status()


def test_install_runs_every_step_answers_sudo_and_hides_passwords():
    c = FakeClient()
    job, s = run_job(c, ssid="SILM-Tartu", country="fi")
    assert s["state"] == "done" and s["progress"] == 100, s
    assert "9.9.9" in s["result"] and "restarts now" in s["result"]
    assert c.uploaded == {piinstall.REMOTE_PKG: b"PK-package"}
    install = next(x for x in c.commands if "pi/install.sh" in x)
    assert install.startswith("cd ~/uav-silm/onboard && bash pi/install.sh ")
    assert "--password aircraft-secret" in install and "--ssid SILM-Tartu" in install and "--country FI" in install
    assert c.typed == ["pi-login-pw\n"] * 2                       # sudo -v, then install.sh
    assert any("reboot" in x for x in c.commands) and c.closed
    log = "\n".join(s["log"])
    assert "aircraft-secret" not in log and "pi-login-pw" not in log and "\x1b[" not in log
    assert "==> 9/9 Checking the configuration" in log and "[sudo]" not in log


def test_progress_follows_the_installer_steps():
    job = PiInstall()
    job.state = {"state": "running", "log": []}
    job._log("==> 4/9 Services silm-recon")
    assert job.state["step"] == "Step 4 of 9: Services silm-recon" and 40 < job.state["progress"] < 60


def test_install_refusals_are_explained():
    _, s = run_job(FakeClient(sudo_ok=False))
    assert s["state"] == "failed" and "may not use sudo" in s["result"]
    _, s = run_job(FakeClient(internet=False))
    assert s["state"] == "failed" and "no internet" in s["result"]
    _, s = run_job(FakeClient(install_lines=[b"==> 4/9 Services\n", b"Failed to connect to bus\n"], install_code=1))
    assert s["state"] == "failed" and "code 1" in s["result"] and "Failed to connect to bus" in s["result"]
    job = PiInstall(connect=lambda *a, **k: None)
    assert "8 to 63" in job.start("silm.local", "pi", "pw", hotspot_password="short")["message"]
    assert "silm.local" in job.start("", "pi", "pw")["message"]
    assert "letters" in job.start("silm.local", "pi", "pw", hotspot_password="x" * 8, ssid="bad;name")["message"]
    assert "two letters" in job.start("silm.local", "pi", "pw", hotspot_password="x" * 8, country="EST")["message"]
    r = job.start("silm.local", "pi", "pw", hotspot=False, dry_run=True)       # no hotspot: no password needed
    assert r["ok"]


def test_no_hotspot_no_camera_net_dry_run_and_no_reboot():
    c = FakeClient()
    _, s = run_job(c, hotspot=False, camera_net=False, dry_run=True, hotspot_password="")
    install = next(x for x in c.commands if "pi/install.sh" in x)
    assert "--no-hotspot" in install and "--no-camera-net" in install and "--dry-run" in install
    assert "--password" not in install
    assert not any("urlopen" in x for x in c.commands)             # dry run: no internet needed
    assert not any("reboot" in x for x in c.commands) and "sudo reboot" in s["result"]


def test_cancel_stops_the_install():
    c = FakeClient(hang=True)
    job = PiInstall(connect=lambda *a, **k: c, package=lambda: (b"PK", "9.9.9"))
    assert job.start("silm.local", "pi", "pw", hotspot_password="x" * 8)["ok"]
    assert not job.start("silm.local", "pi", "pw", hotspot_password="x" * 8)["ok"]     # one at a time
    t0 = time.time()
    while "Step 1" not in job.status().get("step", "") and time.time() - t0 < 5:
        time.sleep(0.02)
    assert job.cancel()["ok"]
    while job.status()["state"] == "running" and time.time() - t0 < 10:
        time.sleep(0.02)
    assert job.status()["state"] == "stopped" and c.closed


def test_connect_errors_are_explained(tmp_path, monkeypatch):
    pytest.importorskip("paramiko")
    monkeypatch.setattr(piinstall, "known_hosts_file", lambda: str(tmp_path / "known_hosts"))
    with pytest.raises(piinstall.InstallError) as e:
        piinstall._connect("127.0.0.1", "pi", "pw", port=9)           # nothing listens there
    assert "cannot reach" in str(e.value) and "SSH" in str(e.value)


def test_unpack_script_swaps_and_keeps_models(tmp_path):
    """The script the Pi runs to put the software in place (run here for real)."""
    import subprocess
    import zipfile
    from groundstation import pisoftware
    root = tmp_path / "uav-silm" / "onboard"
    (root / "models").mkdir(parents=True)
    (root / "VERSION").write_text("0.1\n")
    (root / "models" / "coco_ssd_mobilenet_v1_quant.tflite").write_bytes(b"downloaded")
    z = tmp_path / piinstall.REMOTE_PKG
    z.write_bytes(pisoftware.build_package())
    r = subprocess.run([sys.executable, "-c", piinstall.UNPACK, str(z), str(root)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert (root / "run.py").exists() and os.access(root / "pi" / "silm-wifi", os.X_OK)
    assert (root / "models" / "coco_ssd_mobilenet_v1_quant.tflite").read_bytes() == b"downloaded"
    assert (tmp_path / "uav-silm" / "onboard.old" / "VERSION").read_text() == "0.1\n" and not z.exists()
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("../evil", "x")
    r = subprocess.run([sys.executable, "-c", piinstall.UNPACK, str(bad), str(root)], capture_output=True, text=True)
    assert r.returncode != 0 and "unsafe" in r.stderr and not (tmp_path / "uav-silm" / "evil").exists()


@pytest.mark.skipif(not os.environ.get("SILM_SSH_TEST"), reason="set SILM_SSH_TEST=user:password@host:port")
def test_install_over_a_real_ssh_server(tmp_path, monkeypatch):
    pytest.importorskip("paramiko")
    monkeypatch.setattr(piinstall, "known_hosts_file", lambda: str(tmp_path / "known_hosts"))
    cred, hostport = os.environ["SILM_SSH_TEST"].rsplit("@", 1)
    user, password = cred.split(":", 1)
    host, port = (hostport.split(":") + ["22"])[:2]
    job = PiInstall()
    assert job.start(host, user, password, hotspot_password="aircraft-pass-1", ssid="SILM-Test",
                     port=int(port), dry_run=True)["ok"]
    t0 = time.time()
    while job.status()["state"] == "running" and time.time() - t0 < 300:
        time.sleep(0.3)
    s = job.status()
    assert s["state"] == "done", s
    log = "\n".join(s["log"])
    assert "sudo OK" in log and "==> 9/9" in log and password not in log and "aircraft-pass-1" not in log
    assert job.start(host, user, "wrong-password", hotspot_password="x" * 8, port=int(port))["ok"]
    while job.status()["state"] == "running":
        time.sleep(0.2)
    assert "refused the user name or password" in job.status()["result"]
