"""Install the aircraft computer from the Ground Station, over SSH.

The Pi needs a memory card written with Raspberry Pi Imager (Raspberry Pi OS
Lite 64-bit, hostname, user and password, the home Wi-Fi, SSH on) and must be
on the same network as this laptop with internet. Then this:

  1. connects with the user name and password (the Pi's key is remembered in
     ~/SILM/known_hosts; a different key later is refused),
  2. checks it is allowed to use sudo and has internet,
  3. copies this app's aircraft software onto it (~/uav-silm/onboard; an older
     one is kept as onboard.old),
  4. runs pi/install.sh (about 15 minutes) and shows its progress,
  5. restarts the Pi.

Passwords are only passed on, never stored. One install at a time.
"""

from __future__ import annotations

import os
import re
import shlex
import threading
import time

from groundstation import pisoftware

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
STEP = re.compile(r"==> (\d+)/(\d+) (.*)")
SUDO_PROMPT = re.compile(r"\[sudo\] password for [^:]*:|^Password:", re.M)
NAME_OK = re.compile(r"^[A-Za-z0-9 ._-]{1,32}$")
REMOTE_PKG = ".silm-pi-software.zip"

# Unpacks the package next to an existing install and swaps it in, keeping
# downloaded models (runs on the Pi with its own python3; argv: zip, folder).
UNPACK = r"""
import os, shutil, sys, zipfile
zpath, root = sys.argv[1], os.path.expanduser(sys.argv[2])
new, old = root + ".new", root + ".old"
shutil.rmtree(new, ignore_errors=True)
with zipfile.ZipFile(zpath) as z:
    for n in z.namelist():
        if n.startswith("/") or ".." in n.split("/"):
            sys.exit("unsafe name in the package: " + n)
    z.extractall(new)
    for i in z.infolist():
        m = (i.external_attr >> 16) & 0o777
        if m:
            os.chmod(os.path.join(new, i.filename), m)
if os.path.isdir(os.path.join(root, "models")):
    for f in os.listdir(os.path.join(root, "models")):
        s, d = os.path.join(root, "models", f), os.path.join(new, "models", f)
        if os.path.isfile(s) and not os.path.exists(d):
            shutil.copy2(s, d)
if os.path.isdir(root):
    shutil.rmtree(old, ignore_errors=True)
    os.rename(root, old)
os.makedirs(os.path.dirname(root), exist_ok=True)
os.rename(new, root)
os.remove(zpath)
print("software", open(os.path.join(root, "VERSION")).read().strip(), "in", root)
"""

INTERNET = ("python3 -c \"import urllib.request; urllib.request.urlopen('https://pypi.org/simple/pip/', timeout=15)"
            "; print('internet OK')\"")


class InstallError(RuntimeError):
    pass


def known_hosts_file() -> str:
    return os.path.join(os.path.expanduser("~"), "SILM", "known_hosts")


def _connect(host: str, user: str, password: str, port: int = 22):
    try:
        import paramiko
    except ImportError as e:
        raise InstallError("this Ground Station lacks the SSH part (pip install paramiko)") from e
    kh = known_hosts_file()
    os.makedirs(os.path.dirname(kh), exist_ok=True)
    c = paramiko.SSHClient()
    if os.path.isfile(kh):
        c.load_host_keys(kh)
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())    # first contact: remember it
    try:
        c.connect(host, port=port, username=user, password=password, timeout=15,
                  allow_agent=False, look_for_keys=False, banner_timeout=20, auth_timeout=20)
    except paramiko.BadHostKeyException as e:
        raise InstallError(
            f"{host} is not the computer this laptop installed before (its SSH key changed). If you "
            f"wrote its memory card again, that is expected: delete the line for {host} in {kh} "
            "and try again.") from e
    except paramiko.AuthenticationException as e:
        raise InstallError("the Pi refused the user name or password (the ones you chose in "
                           "Raspberry Pi Imager)") from e
    except (OSError, paramiko.SSHException) as e:
        raise InstallError(
            f"cannot reach {host}:{port} ({e}). Is the Pi powered for two minutes, on the same "
            "network as this laptop, and was SSH enabled in Raspberry Pi Imager? Try its address "
            "instead of the name (your router's list of devices shows it).") from e
    c.save_host_keys(kh)
    return c


class PiInstall:
    """The install as a background job, polled by the page."""

    def __init__(self, connect=_connect, package=pisoftware.package):
        self._connect, self._package = connect, package
        self._lock = threading.Lock()
        self._thread = None
        self._cancel = threading.Event()
        self._chan = None
        self._secrets: list[str] = []
        self.state = {"state": "idle"}

    # ------------------------------------------------------------ page API
    def status(self) -> dict:
        with self._lock:
            return dict(self.state, log=list(self.state.get("log", []))[-300:])

    def start(self, host: str, user: str, password: str, hotspot_password: str = "", ssid: str = "",
              country: str = "EE", hotspot: bool = True, camera_net: bool = True, reboot: bool = True,
              port: int = 22, dry_run: bool = False) -> dict:
        host, user, ssid, country = (host or "").strip(), (user or "").strip(), (ssid or "").strip(), \
            (country or "EE").strip().upper()
        if re.fullmatch(r"[^:\s]+:\d{1,5}", host):          # name:port
            host, port = host.rsplit(":", 1)
        if self._thread and self._thread.is_alive():
            return {"ok": False, "message": "an install is already running"}
        if not host or not user or not password:
            return {"ok": False, "message": "enter the Pi's name (silm.local), its user name and password"}
        if hotspot and not dry_run and not 8 <= len(hotspot_password or "") <= 63:
            return {"ok": False, "message": "choose a password for the aircraft's Wi-Fi: 8 to 63 characters"}
        if ssid and not NAME_OK.match(ssid):
            return {"ok": False, "message": "the Wi-Fi name may use letters, digits, spaces and . _ - (up to 32)"}
        if not re.fullmatch(r"[A-Z]{2}", country):
            return {"ok": False, "message": "the country is two letters, e.g. EE"}
        args = ["--country", country]
        if hotspot:
            args += ["--password", hotspot_password] if hotspot_password else []
            args += ["--ssid", ssid] if ssid else []
        else:
            args.append("--no-hotspot")
        if not camera_net:
            args.append("--no-camera-net")
        if dry_run:
            args.append("--dry-run")
        self._secrets = [s for s in (password, hotspot_password) if s]
        self._cancel.clear()
        self.state = {"state": "running", "step": "Connecting", "progress": 0, "log": [], "result": "",
                      "host": host, "started": time.time()}
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        args=(host, user, password, args, reboot and not dry_run, int(port)))
        self._thread.start()
        return {"ok": True, "message": f"installing on {host}"}

    def cancel(self) -> dict:
        if not (self._thread and self._thread.is_alive()):
            return {"ok": False, "message": "no install running"}
        self._cancel.set()
        ch = self._chan
        if ch is not None:
            ch.close()
        return {"ok": True, "message": "stopping"}

    # ------------------------------------------------------------ the job
    def _mask(self, text: str) -> str:
        for s in self._secrets:
            text = text.replace(s, "••••••")
        return text

    def _log(self, line: str) -> None:
        line = self._mask(ANSI.sub("", line)).rstrip()
        if not line.strip():
            return
        with self._lock:
            self.state["log"].append(line)
            m = STEP.search(line)
            if m:
                n, total = int(m.group(1)), int(m.group(2))
                self.state["step"] = f"Step {n} of {total}: {m.group(3).strip()}"
                self.state["progress"] = 15 + int(80 * (n - 1) / total)

    def _set(self, step: str, progress: int) -> None:
        with self._lock:
            self.state.update(step=step, progress=progress)

    def _exec(self, client, cmd: str, password: str, timeout: float = 3600) -> int:
        """Run on the Pi with a terminal, answering sudo's password prompts, streaming lines."""
        chan = client.get_transport().open_session()
        self._chan = chan
        chan.get_pty(term="dumb", width=160)
        chan.settimeout(1.0)
        chan.exec_command(cmd)
        buf, t0, answered = "", time.time(), 0
        while True:
            if self._cancel.is_set():
                chan.close()
                raise InstallError("stopped")
            try:
                data = chan.recv(4096)
            except OSError:          # socket.timeout
                data = None
            if data == b"":
                break
            if data:
                buf += data.decode("utf-8", "replace").replace("\r\n", "\n").replace("\r", "\n")
                if SUDO_PROMPT.search(buf):
                    answered += 1
                    if answered > 3:
                        chan.close()
                        raise InstallError("sudo did not accept the password")
                    chan.send(password + "\n")
                    buf = SUDO_PROMPT.sub("", buf)
                *lines, buf = buf.split("\n")
                for ln in lines:
                    self._log(ln)
            if time.time() - t0 > timeout:
                chan.close()
                raise InstallError(f"no end after {timeout / 60:.0f} minutes")
        if buf:
            self._log(buf)
        code = chan.recv_exit_status()
        self._chan = None
        return code

    def _run(self, host, user, password, args, reboot, port):
        client = None
        try:
            self._log(f"Connecting to {user}@{host} …")
            client = self._connect(host, user, password, port)
            self._log("connected")
            self._set("Checking the Pi", 4)
            if self._exec(client, "sudo -p '[sudo] password for %u:' -v && echo 'sudo OK'", password, 60) != 0:
                raise InstallError(f"the user {user} may not use sudo on the Pi (use the user made by "
                                   "Raspberry Pi Imager)")
            if "--dry-run" not in args and self._exec(client, INTERNET, password, 60) != 0:
                raise InstallError("the Pi has no internet. It needs it once for the install: connect it "
                                   "to your router with a cable, or set your home Wi-Fi in Raspberry Pi "
                                   "Imager, then try again.")
            self._set("Copying the aircraft software", 8)
            data, version = self._package()
            sftp = client.open_sftp()
            with sftp.open(REMOTE_PKG, "wb") as fh:
                fh.write(data)
            sftp.close()
            self._log(f"copied software {version} ({len(data) / 1e6:.1f} MB)")
            if self._exec(client, "python3 -c " + shlex.quote(UNPACK) + f" {REMOTE_PKG} ~/uav-silm/onboard",
                          password, 120) != 0:
                raise InstallError("unpacking the software on the Pi failed (see the log)")
            self._set("Installing (about 15 minutes)", 15)
            cmd = "cd ~/uav-silm/onboard && bash pi/install.sh " + " ".join(shlex.quote(a) for a in args)
            code = self._exec(client, cmd, password)
            if code != 0:
                with self._lock:
                    last = [ln.strip() for ln in self.state["log"][-4:]]
                raise InstallError(f"the installer stopped (code {code}): " + " / ".join(last))
            if reboot:
                self._set("Restarting the Pi", 97)
                try:
                    self._exec(client, "sudo -p '[sudo] password for %u:' reboot", password, 30)
                except Exception:       # noqa: BLE001 - the connection drops while it restarts
                    pass
            msg = (f"Installed software {version} on {host}. " +
                   ("The Pi restarts now; in two minutes join the laptop to its Wi-Fi (SILM-…)."
                    if reboot else "Restart the Pi to finish (sudo reboot)."))
            with self._lock:
                self.state.update(state="done", step="Done", progress=100, result=msg)
        except Exception as e:  # noqa: BLE001
            with self._lock:
                self.state.update(state="stopped" if self._cancel.is_set() else "failed",
                                  result=self._mask(str(e)))
        finally:
            self._chan = None
            if client is not None:
                client.close()
