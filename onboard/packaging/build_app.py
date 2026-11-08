#!/usr/bin/env python3
"""Build the UAV SILM Ground Station desktop app for the computer it runs on.

    python3 -m pip install -r groundstation/requirements.txt -r packaging/requirements.txt
    python3 packaging/build_app.py            # from onboard/
    python3 packaging/build_app.py --smoke    # also start the built app's self-test

Output in packaging/out/:
  Windows  UAV-SILM-Ground-Station-Setup-<version>.exe   installer (Inno Setup;
           desktop and Start-menu icons, uninstaller, no admin rights needed)
           UAV-SILM-Ground-Station-<version>-windows.zip  the same, without installing
  macOS    UAV-SILM-Ground-Station-<version>-macos.zip    the app: unzip, drag to Applications
  Linux    UAV-SILM-Ground-Station-<version>-linux.tar.gz unpack, run ./install.sh

GitHub builds all three on every change (.github/workflows/ground-station-app.yml)
and attaches them to a release for each tag gs-v*.
"""

from __future__ import annotations

import argparse
import glob
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ONBOARD = os.path.dirname(HERE)
NAME = "UAV-SILM-Ground-Station"
TITLE = "UAV SILM Ground Station"
OUT = os.path.join(HERE, "out")
SYSTEM = {"Windows": "windows", "Darwin": "macos"}.get(platform.system(), "linux")


def version() -> str:
    return open(os.path.join(ONBOARD, "VERSION")).read().strip()


def data_files() -> list[tuple[str, str]]:
    """(source relative to onboard/, folder inside the app) - everything the
    Ground Station reads at run time, nothing it doesn't."""
    d = [("groundstation/static", "groundstation/static"), ("ardupilot", "ardupilot")]
    for f in ("config.yaml", "VERSION", "LICENSE", "THIRD_PARTY_NOTICES.md", "README.md",
              "GROUND_STATION.md", "FLIGHT_READY.md", "SITL_HOWTO.md"):
        d.append((f, "."))
    for f in ("silm_oi.tflite", "silm_oi_labels.txt", "silm_oi.json", "LICENSE-silm_oi-AGPL-3.0.txt",
              "coco_labels.txt", "README.md"):
        d.append((os.path.join("models", f), "models"))
    d.append(("tools/sitl_failsafe_test.py", "tools"))           # run by a system Python
    for f in sorted(glob.glob(os.path.join(ONBOARD, "training", "*"))):
        if os.path.isfile(f) and f.endswith((".py", ".yaml", ".md")):
            d.append((os.path.relpath(f, ONBOARD), "training"))
    missing = [s for s, _ in d if not os.path.exists(os.path.join(ONBOARD, s))]
    if missing:
        raise SystemExit(f"missing files: {missing}")
    return d


def pyinstaller_args() -> list[str]:
    sep = os.pathsep
    args = [os.path.join(HERE, "app_entry.py"), "--name", NAME, "--noconfirm", "--clean",
            "--distpath", os.path.join(OUT, "dist"), "--workpath", os.path.join(OUT, "build"),
            "--specpath", OUT, "--paths", ONBOARD, "--paths", os.path.join(ONBOARD, "planner")]
    for m in ("groundstation.app", "groundstation.desktop", "groundstation.fc", "groundstation.flights",
              "groundstation.pilink", "groundstation.project", "groundstation.sim",
              "groundstation.training", "groundstation.runtime", "survey", "silm_recon.mavmission",
              "silm_recon.config", "pymavlink.mavutil", "pymavlink.mavftp",
              "pymavlink.dialects.v20.ardupilotmega", "pymavlink.dialects.v10.ardupilotmega",
              "serial.tools.list_ports"):
        args += ["--hidden-import", m]
    for m in ("torch", "torchvision", "ultralytics", "cv2", "matplotlib", "scipy", "pandas",
              "IPython", "pytest", "numpy", "lxml"):           # pymavlink's generator only
        args += ["--exclude-module", m]
    for src, dst in data_files():
        args += ["--add-data", f"{os.path.join(ONBOARD, src)}{sep}{dst}"]
    if SYSTEM == "windows":
        args += ["--windowed", "--icon", os.path.join(HERE, "icons", "icon.ico")]
    elif SYSTEM == "macos":
        args += ["--windowed", "--icon", os.path.join(HERE, "icons", "icon.icns"),
                 "--osx-bundle-identifier", "eu.uavsilm.groundstation"]
    return args


def app_executable() -> str:
    dist = os.path.join(OUT, "dist")
    if SYSTEM == "macos":
        return os.path.join(dist, NAME + ".app", "Contents", "MacOS", NAME)
    return os.path.join(dist, NAME, NAME + (".exe" if SYSTEM == "windows" else ""))


def package() -> list[str]:
    v, dist, made = version(), os.path.join(OUT, "dist"), []
    if SYSTEM == "windows":
        z = os.path.join(OUT, f"{NAME}-{v}-windows.zip")
        _zip_dir(os.path.join(dist, NAME), z, NAME)
        made.append(z)
        iscc = shutil.which("iscc") or next(iter(glob.glob(r"C:\Program Files*\Inno Setup *\ISCC.exe")), None)
        if iscc:
            subprocess.run([iscc, f"/DAppVersion={v}", f"/DSourceDir={os.path.join(dist, NAME)}",
                            f"/DOutDir={OUT}", f"/DIconFile={os.path.join(HERE, 'icons', 'icon.ico')}",
                            os.path.join(HERE, "windows", "installer.iss")], check=True)
            made.append(os.path.join(OUT, f"{NAME}-Setup-{v}.exe"))
        else:
            print("Inno Setup (iscc) not found: made the zip only")
    elif SYSTEM == "macos":
        z = os.path.join(OUT, f"{NAME}-{v}-macos.zip")
        subprocess.run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent",
                        os.path.join(dist, NAME + ".app"), z], check=True)
        made.append(z)
    else:
        folder = os.path.join(dist, NAME)
        shutil.copy(os.path.join(HERE, "linux", "install.sh"), os.path.join(folder, "install.sh"))
        os.chmod(os.path.join(folder, "install.sh"), 0o755)
        shutil.copy(os.path.join(HERE, "icons", "icon_512.png"), os.path.join(folder, "icon.png"))
        t = os.path.join(OUT, f"{NAME}-{v}-linux.tar.gz")
        with tarfile.open(t, "w:gz") as tf:
            tf.add(folder, arcname=NAME)
        made.append(t)
    return made


def _zip_dir(folder: str, dest: str, arc: str) -> None:
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _, files in os.walk(folder):
            for f in files:
                p = os.path.join(root, f)
                z.write(p, os.path.join(arc, os.path.relpath(p, folder)))


def smoke() -> int:
    exe = app_executable()
    print(f"self-test: {exe} --selftest")
    r = subprocess.run([exe, "--selftest"], capture_output=True, text=True, timeout=180)
    print(r.stdout + r.stderr)
    return 0 if r.returncode == 0 and "SELFTEST OK" in r.stdout else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--smoke", action="store_true", help="run the built app's self-test afterwards")
    ap.add_argument("--smoke-only", action="store_true", help="only run the self-test of a previous build")
    a = ap.parse_args(argv)
    if not a.smoke_only:
        os.makedirs(OUT, exist_ok=True)
        import PyInstaller.__main__
        PyInstaller.__main__.run(pyinstaller_args())
        for f in package():
            print(f"made {f} ({os.path.getsize(f) / 1e6:.1f} MB)")
    return smoke() if (a.smoke or a.smoke_only) else 0


if __name__ == "__main__":
    sys.exit(main())
