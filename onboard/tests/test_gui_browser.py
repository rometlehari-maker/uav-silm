"""The Ground Station in a real browser (tests/gui/e2e.js, Playwright for Node):
a user's way through every tab, against a real ground agent over HTTP.
Skipped where Node + Playwright aren't installed."""

import json
import os
import shutil
import subprocess
import threading

import pytest
import yaml

from test_groundstation import ONBOARD, jpeg_bytes, make_flight

from groundstation.app import Station, make_server
from silm_recon.agent import Agent
from silm_recon.agent import make_server as agent_server

E2E = os.path.join(os.path.dirname(__file__), "gui", "e2e.js")


def node_with_playwright():
    node = shutil.which("node")
    if not node:
        return None, None
    root = subprocess.run(["npm", "root", "-g"], capture_output=True, text=True).stdout.strip() \
        if shutil.which("npm") else ""
    env = dict(os.environ, NODE_PATH=root)
    ok = subprocess.run([node, "-e", "require('playwright')"], env=env, capture_output=True).returncode == 0
    return (node, env) if ok else (None, None)


NODE, NODE_ENV = node_with_playwright()


@pytest.mark.skipif(NODE is None, reason="Node.js + Playwright not installed")
def test_ground_station_in_a_browser(tmp_path):
    # the aircraft's Pi: a real agent
    base = yaml.safe_load(open(os.path.join(ONBOARD, "config.yaml")))
    base["settings_file"] = str(tmp_path / "pi" / "settings.yaml")
    base["status"] = {"path": str(tmp_path / "pi" / "status.json")}
    base["storage"]["output_dir"] = str(tmp_path / "pi" / "captures")
    base["agent"]["usb_device"] = ""
    (tmp_path / "pi").mkdir()
    (tmp_path / "pi" / "config.yaml").write_text(yaml.safe_dump(base))
    agent = Agent(str(tmp_path / "pi" / "config.yaml"), runner=lambda c, timeout=15: (True, ""))

    def preflight_tool(cmd, timeout=15):                 # the hardware check, without hardware
        with open(cmd[cmd.index("--json") + 1], "w") as fh:
            json.dump({"results": [{"part": "camera", "status": "PASS", "message": "1920x1080 at 30 fps"},
                                   {"part": "fc", "status": "WARN", "message": "no GPS fix (indoors)"}],
                       "fails": 0, "warns": 1}, fh)
        return True, ""
    agent._run_tool = preflight_tool
    stop_camera = threading.Event()

    def camera():                                        # the flight program's preview, once a second
        while not stop_camera.is_set():
            (tmp_path / "pi" / "preview.jpg").write_bytes(jpeg_bytes(640, 360, 90))
            stop_camera.wait(1.0)
    threading.Thread(target=camera, daemon=True).start()
    asrv = agent_server(agent, host="127.0.0.1", port=0)
    threading.Thread(target=asrv.serve_forever, daemon=True).start()
    # the laptop: Ground Station with one flight in its library
    st = Station(library=str(tmp_path / "lap" / "flights"), projects=str(tmp_path / "lap" / "projects"))
    make_flight(st.library)
    gsrv = make_server(0, station=st)
    threading.Thread(target=gsrv.serve_forever, daemon=True).start()
    stick = tmp_path / "stick"
    stick.mkdir()
    img = tmp_path / "field.jpg"
    img.write_bytes(jpeg_bytes(640, 480, 120))
    env = dict(NODE_ENV, GS_URL=f"http://127.0.0.1:{gsrv.server_address[1]}",
               PI_ADDR=f"127.0.0.1:{asrv.server_address[1]}", STICK=str(stick), TRAIN_IMG=str(img),
               SHOTS=os.environ.get("SILM_E2E_SHOTS", ""))
    try:
        r = subprocess.run([NODE, E2E], env=env, capture_output=True, text=True, timeout=240)
    finally:
        stop_camera.set()
        gsrv.shutdown()
        asrv.shutdown()
    assert "E2E OK" in r.stdout, r.stdout + r.stderr
    # what the browser sent really arrived on the "aircraft"
    s = yaml.safe_load(open(agent.settings_file))
    assert s["targets"] == ["person", "car", "bus"] and s["storage"]["save_all_frames"] is True
    assert yaml.safe_load(open(stick / "SILM_TRANSFER" / "to_aircraft" / "settings.yaml"))["targets"] \
        == ["person", "car", "bus"]
    assert st.dataset.stats()["per_class"]["tractor"]["boxes"] == 1
    assert {p["name"] for p in st.list_projects()} >= {"New mission", "Field B"}
