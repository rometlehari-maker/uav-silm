"""The Ground Station's simulator runner without a simulator: what it tells
the page on each kind of computer, the download list, the live feed, and
the test tool's hooks. The real flight is tests/test_sitl_integration.py."""

import os
import sys

import pytest

ONBOARD = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ONBOARD)

from groundstation import sim  # noqa: E402


def test_downloads_cover_windows_and_linux_with_the_windows_dlls():
    files, exe = sim.DOWNLOADS["linux-x86_64"]
    assert exe == "arduplane" and any(n == "arduplane" for _, n in files)
    files, exe = sim.DOWNLOADS["windows-x86_64"]
    names = {n for _, n in files}
    assert exe == "ArduPlane.elf" and "cygwin1.dll" in names and "cygstdc++-6.dll" in names
    assert all(p.startswith(("Plane/", "Tools/")) and "%2B" not in n for p, n in files)
    assert "macos-arm64" not in sim.DOWNLOADS


def test_nothing_installed_says_how_to_get_it(tmp_path, monkeypatch):
    monkeypatch.delenv("SILM_SITL_BIN", raising=False)
    r = sim.SimRunner(ardupilot_dir=str(tmp_path / "no-ardupilot"), sitl_dir=str(tmp_path / "sitl"))
    monkeypatch.setattr(sim, "host_key", lambda: "linux-x86_64")
    a = r.available()
    assert not a["available"] and a["can_install"] and "Install the simulator" in a["why"]
    assert set(a["scenarios"]) == {"jam", "disable", "gusty", "none"}
    assert a["install"]["state"] == "idle"
    monkeypatch.setattr(sim, "host_key", lambda: "macos-arm64")
    a = r.available()
    assert not a["available"] and not a["can_install"] and "Mac" in a["why"]
    assert r.start("", "", (58.0, 26.0, 0.0), 5, 250)["ok"] is False
    assert r.status()["state"] == "idle"


def test_a_downloaded_or_developer_binary_is_found(tmp_path, monkeypatch):
    monkeypatch.setattr(sim, "host_key", lambda: "linux-x86_64")
    monkeypatch.delenv("SILM_SITL_BIN", raising=False)
    sitl = tmp_path / "sitl"
    sitl.mkdir()
    r = sim.SimRunner(ardupilot_dir=str(tmp_path / "ardupilot"), sitl_dir=str(sitl))
    assert r.binary() is None
    dev = tmp_path / "ardupilot" / "build" / "sitl" / "bin"
    dev.mkdir(parents=True)
    (dev / "arduplane").write_bytes(b"x")
    assert r.binary() == str(dev / "arduplane")
    (sitl / "arduplane").write_bytes(b"x")
    (sitl / "git-version.txt").write_text("commit 0123456789abcdef\nAPMVERSION: ArduPlane V4.7.2\n")
    assert r.binary() == str(sitl / "arduplane")
    a = r.available()
    assert a["available"] and a["version"] == "ArduPlane V4.7.2" and not a["can_install"]
    monkeypatch.setenv("SILM_SITL_BIN", str(dev / "arduplane"))
    assert r.binary() == str(dev / "arduplane")


def test_live_feed_is_incremental(tmp_path):
    r = sim.SimRunner(sitl_dir=str(tmp_path))
    assert r.live()["state"] == "idle" and r.live()["rows"] == []
    r.job = {"state": "running", "step": "flying the mission", "launch": [58.0, 26.0]}
    for i in range(3):
        r._on_row({"t": i * 0.2, "true_lat": 58.0 + i * 1e-4, "true_lon": 26.0, "est_lat": 58.0,
                   "est_lon": 26.0, "alt": 50.4, "phase": "survey", "mode": 10, "seq": 3, "armed": True})
    lv = r.live(0)
    assert lv["next"] == 3 and len(lv["rows"]) == 3 and lv["rows"][0][5] == 50 and lv["launch"] == [58.0, 26.0]
    lv2 = r.live(lv["next"])
    assert lv2["rows"] == [] and lv2["next"] == 3


def test_scenarios_are_explained_in_plain_words():
    for k, v in sim.SCENARIOS.items():
        assert v["fault"] in ("jam", "disable", "none") and len(v["what"]) > 40 and v["title"]
        assert "SIM_" not in v["what"] and "param" not in v["what"].lower()
    assert sim.SCENARIOS["gusty"]["wind"] == 10.0 and sim.SCENARIOS["gusty"]["turbulence"] > 0
    assert sim.SPEEDS == {"real": 1, "fast": 4}


def test_test_tool_hooks_route_text_and_rows():
    tool = sim._load_test_tool()
    lines, rows = [], []
    tool.set_hooks(log=lines.append, on_row=rows.append)
    tool.say("hello", 3)
    assert lines == ["hello 3"]
    tool.set_hooks()
    assert tool._log is print and tool._on_row is None
    assert tool.sim_battery_mult(12.6, 1.0, 4) == pytest.approx(16.8 / 12.6)


def test_defaults_file_has_the_plane_model_and_scripting_on():
    text = open(sim.DEFAULTS).read()
    kv = dict(ln.split()[:2] for ln in text.splitlines() if ln.strip() and not ln.startswith("#"))
    assert kv["SCR_ENABLE"] == "1" and kv["INS_ACCOFFS_X"] == "0.001" and kv["RC1_MIN"] == "1000"
    assert "GPL" in text and "SIM_GPS" not in kv


def test_listening_check_never_connects(monkeypatch):
    """The simulator accepts one connection: a probe that connects would take
    it (Windows then refuses the real one). The check reads the system's
    connection table instead."""
    import socket
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    srv.settimeout(0.5)
    try:
        assert sim._listening(port) is True
        with pytest.raises(socket.timeout):
            srv.accept()                       # nothing connected to us
    finally:
        srv.close()
    assert sim._listening(port) is False
