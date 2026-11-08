"""UAV SILM Ground Station as a desktop app.

Opens the Ground Station in its own window (pywebview: Edge WebView2 on
Windows, WebKit on macOS). Where no window can be made (or with --browser) it
opens in the web browser instead and shows a small "running" window to quit.
This is the program the installers start; `start_ground_station.*` still work.

    python -m groundstation.desktop             # window
    python -m groundstation.desktop --browser   # browser
    python -m groundstation.desktop --selftest  # check the build, then exit
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ONBOARD = os.path.dirname(HERE)
if ONBOARD not in sys.path:
    sys.path.insert(0, ONBOARD)

from groundstation.app import Station, make_server, software_version  # noqa: E402

TITLE = "UAV SILM Ground Station"


def _get(url: str, body: dict | None = None, timeout: float = 10.0):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data, {"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        return json.loads(raw) if "json" in r.headers.get("Content-Type", "") else raw


def _is_ground_station(url: str) -> bool:
    try:
        return "questions" in _get(url + "api/meta", timeout=3)
    except Exception:
        return False


def start_server(port: int):
    """Start the server in the background. Returns (server or None, url). If the
    Ground Station is already running on that port, just returns its url."""
    try:
        srv = make_server(port, station=Station())
    except OSError:
        url = f"http://127.0.0.1:{port}/"
        if _is_ground_station(url):
            return None, url                        # already open: show that one
        srv = make_server(0, station=Station())     # something else has the port
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/"


def _mavlink_loopback() -> None:
    """Send an ArduPilot heartbeat to ourselves over UDP: proves the MAVLink
    library and the ArduPilot message set are inside the app."""
    import socket
    from pymavlink import mavutil
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    rx = mavutil.mavlink_connection(f"udpin:127.0.0.1:{port}")
    tx = mavutil.mavlink_connection(f"udpout:127.0.0.1:{port}", source_system=1)
    try:
        msg = None
        for _ in range(10):
            tx.mav.heartbeat_send(mavutil.mavlink.MAV_TYPE_FIXED_WING,
                                  mavutil.mavlink.MAV_AUTOPILOT_ARDUPILOTMEGA, 0, 0, 0)
            msg = rx.recv_match(type="HEARTBEAT", blocking=True, timeout=0.5)
            if msg:
                break
        assert msg is not None and msg.type == mavutil.mavlink.MAV_TYPE_FIXED_WING, "MAVLink"
    finally:
        rx.close()
        tx.close()


def window_support() -> str:
    """'ok' if this build can open its own window here, else the reason."""
    try:
        import webview  # noqa: F401
        if sys.platform == "win32":
            import webview.platforms.edgechromium  # noqa: F401  (Edge WebView2 via pythonnet)
        elif sys.platform == "darwin":
            import webview.platforms.cocoa  # noqa: F401
        else:
            return "browser mode on Linux"
        return "ok"
    except Exception as exc:  # noqa: BLE001
        return f"unavailable ({exc!r})"


def selftest() -> int:
    """Start the server on a free port and check the pages and the main
    functions answer, as the installers' build checks do."""
    srv, url = start_server(0)
    try:
        meta = _get(url + "api/meta")
        assert meta["version"] == software_version() and meta["questions"], "meta"
        html = _get(url).decode()
        assert TITLE.split()[-2] in html and "wizard.js" in html, "page"
        for f in ("app.js", "wizard.js", "style.css", "fonts/plex-mono-400.woff2", "logo.jpg",
                  "vendor/leaflet/leaflet.js"):
            assert len(_get(url + "static/" + f)) > 100, f
        project = meta["default_project"]
        project["aircraft"]["cg_mm"] = round(0.29 * float(project["aircraft"]["chord_mm"]))
        launch = [58.38, 26.72]                      # a small test mission near Tartu
        project["mission"].update(launch=launch, landing=launch, landing_heading_deg=250, area=[
            [58.385, 26.725], [58.388, 26.735], [58.384, 26.740], [58.3815, 26.730]])
        project["wind"] = {"speed_mps": 5, "from_deg": 250}
        check = _get(url + "api/check", {"project": project})
        assert "errors" in check and "params" in check, "check"
        params = _get(url + "api/files/params", {"project": project}).decode()
        assert "INITIAL_MODE" in params and "SERIAL2_BAUD" in params, "parameter file"
        assert len(_get(url + "api/setup")["steps"]) == 10, "setup"
        assert isinstance(_get(url + "api/ports"), list), "ports"
        assert any(m["name"] == "silm_oi" for m in meta["models"]), "ready-made model"
        _mavlink_loopback()
        lua = os.path.join(ONBOARD, "ardupilot", "scripts", "silm_gps_loss.lua")
        assert os.path.getsize(lua) > 1000, "GPS-loss script"
        sw = _get(url + "api/pi/software", {"how": "info"})
        assert sw.get("ok") and sw["app_version"] == software_version() and sw["size"] > 100_000, "Pi software"
    except Exception as exc:  # noqa: BLE001
        print(f"SELFTEST FAILED: {exc!r}")
        return 1
    finally:
        if srv:
            srv.shutdown()
    win = window_support()
    print(f"app window: {win}")
    if os.environ.get("SILM_REQUIRE_WINDOW") == "1" and win != "ok":
        print("SELFTEST FAILED: this build cannot open its own window")
        return 1
    print(f"SELFTEST OK - {TITLE} {software_version()}")
    return 0


def _window(url: str) -> bool:
    """Show the app in its own window until it is closed. False if not possible."""
    try:
        import webview
    except Exception:
        return False
    try:
        storage = os.path.join(os.path.expanduser("~"), "SILM", ".app")
        os.makedirs(storage, exist_ok=True)
        try:
            webview.settings["ALLOW_DOWNLOADS"] = True       # parameter / mission files
        except Exception:
            pass
        webview.create_window(TITLE, url, width=1440, height=920, min_size=(980, 640),
                              background_color="#3a494d", text_select=True)
        webview.start(private_mode=False, storage_path=storage)
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"(no app window here: {exc}; opening the browser instead)")
        return False


def _browser(url: str, srv) -> None:
    import webbrowser
    webbrowser.open(url)
    if srv is None:
        return
    try:                                              # a small window to stop it
        import tkinter as tk
        root = tk.Tk()
        root.title(TITLE)
        root.configure(bg="#3a494d")
        root.geometry("420x150")
        tk.Label(root, text="The Ground Station is running in your browser.", bg="#3a494d",
                 fg="#f1f9fb", font=("Helvetica", 12)).pack(pady=(22, 10))
        row = tk.Frame(root, bg="#3a494d")
        row.pack()
        tk.Button(row, text="Open it again", command=lambda: webbrowser.open(url)).pack(side="left", padx=6)
        tk.Button(row, text="Quit", command=root.destroy).pack(side="left", padx=6)
        root.mainloop()
    except Exception:
        print(f"{TITLE} is running at {url} - press Ctrl+C to stop.")
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--browser", action="store_true", help="open in the web browser, not a window")
    ap.add_argument("--selftest", action="store_true", help="check this installation and exit")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    srv, url = start_server(a.port)
    if a.browser or not _window(url):
        _browser(url, srv)
    if srv:
        srv.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
