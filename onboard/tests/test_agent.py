"""The Pi's ground agent: settings in (Wi-Fi + USB stick), flights out, Wi-Fi
off while armed - over real HTTP, with temporary folders and recorded commands."""

import io
import json
import os
import threading
import time
import urllib.error
import urllib.request
import zipfile

import pytest
import yaml

from silm_recon.agent import Agent, make_server
from silm_recon.status import write_status

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "..", "config.yaml")


class Recorder:
    def __init__(self, ok=True):
        self.calls, self.ok = [], ok

    def __call__(self, cmd, timeout=15):
        self.calls.append(list(cmd))
        return self.ok, "" if self.ok else "sudo: a password is required"


@pytest.fixture
def env(tmp_path):
    """A config pointing every path into tmp_path."""
    base = yaml.safe_load(open(CONFIG))
    base["settings_file"] = str(tmp_path / "silm-settings" / "settings.yaml")
    base["status"] = {"path": str(tmp_path / "run" / "status.json")}
    base["storage"]["output_dir"] = str(tmp_path / "captures")
    base["agent"]["usb_mount"] = str(tmp_path / "usb")
    base["agent"]["usb_device"] = ""            # no real /dev/disk/by-label in tests
    base["agent"]["wifi_back_on_after_s"] = 5
    cfgp = tmp_path / "config.yaml"
    cfgp.write_text(yaml.safe_dump(base))
    rec = Recorder()
    agent = Agent(str(cfgp), runner=rec)
    return tmp_path, agent, rec


def set_aircraft(tmp_path, **kw):
    write_status(str(tmp_path / "run" / "status.json"), kw)


def make_flight(tmp_path, name="mission_20261108_101500", pics=3):
    d = tmp_path / "captures" / name
    d.mkdir(parents=True)
    for i in range(pics):
        (d / f"det_{i:05d}_1.jpg").write_bytes(b"\xff\xd8JPEG" + bytes(1000 + i))
        (d / f"det_{i:05d}_1.json").write_text(json.dumps({"seq": i}))
    (d / "detections.ndjson").write_text("".join(json.dumps({"seq": i}) + "\n" for i in range(pics)))
    (d / "track.ndjson").write_text(json.dumps({"t": 1, "lat": 58.0, "lon": 24.0}) + "\n")
    return d


GOOD = yaml.safe_dump({"targets": ["tractor", "fire"], "activation": {"min_alt_m": 30}})
WPL = "QGC WPL 110\n0\t1\t0\t16\t0\t0\t0\t0\t58.0\t24.0\t0\t1\n"


# ------------------------------------------------------------ settings

def test_good_settings_are_installed_and_program_restarted(env):
    tmp, a, rec = env
    r = a.accept_settings({"settings_yaml": GOOD, "mission_wpl": WPL,
                           "project": {"name": "field A"}, "sender": "laptop"})
    assert r["ok"] and r["restarted"]
    sdir = tmp / "silm-settings"
    assert yaml.safe_load((sdir / "settings.yaml").read_text())["targets"] == ["tractor", "fire"]
    assert (sdir / "mission.waypoints").read_text() == WPL
    assert json.loads((sdir / "project.json").read_text())["name"] == "field A"
    assert json.loads((sdir / "settings_info.json").read_text())["source"] == "wifi"
    assert rec.calls == [["sudo", "-n", "systemctl", "restart", "silm-recon"]]
    # the merged config the flight program will load
    from silm_recon.config import load_config
    cfg = load_config(str(tmp / "config.yaml"))
    assert cfg["_settings_loaded"] and cfg["activation"]["min_alt_m"] == 30


def test_previous_settings_are_kept_as_backup(env):
    tmp, a, _ = env
    a.accept_settings({"settings_yaml": GOOD})
    a.accept_settings({"settings_yaml": yaml.safe_dump({"targets": ["car"]})})
    prev = yaml.safe_load((tmp / "silm-settings" / "settings.prev.yaml").read_text())
    assert prev["targets"] == ["tractor", "fire"]


@pytest.mark.parametrize("payload, words", [
    ({}, "no settings"),
    ({"settings_yaml": "camera: [unclosed"}, "not valid YAML"),
    ({"settings_yaml": "- a list"}, "must be a mapping"),
    ({"settings_yaml": yaml.safe_dump({"camera": {"source": "webcam"}})}, "camera.source"),
    ({"settings_yaml": GOOD, "mission_wpl": "not a mission"}, "QGC WPL"),
    ({"settings_yaml": GOOD, "project": ["x"]}, "JSON object"),
])
def test_bad_settings_change_nothing(env, payload, words):
    tmp, a, rec = env
    r = a.accept_settings(payload)
    assert not r["ok"] and words in r["message"]
    assert not (tmp / "silm-settings" / "settings.yaml").exists() and rec.calls == []


def test_settings_refused_while_armed_or_airborne(env):
    tmp, a, rec = env
    set_aircraft(tmp, armed=True, rel_alt_m=0.0)
    assert "ARMED" in a.accept_settings({"settings_yaml": GOOD})["message"]
    set_aircraft(tmp, armed=False, rel_alt_m=60.0)            # disarmed but in the air?!
    assert not a.accept_settings({"settings_yaml": GOOD})["ok"]
    set_aircraft(tmp, armed=False, rel_alt_m=0.2)
    assert a.accept_settings({"settings_yaml": GOOD})["ok"]


def test_restart_failure_is_reported_but_settings_kept(tmp_path):
    base = yaml.safe_load(open(CONFIG))
    base["settings_file"] = str(tmp_path / "s" / "settings.yaml")
    base["status"] = {"path": str(tmp_path / "status.json")}
    base["storage"]["output_dir"] = str(tmp_path / "c")
    (tmp_path / "c.yaml").write_text(yaml.safe_dump(base))
    a = Agent(str(tmp_path / "c.yaml"), runner=Recorder(ok=False))
    r = a.accept_settings({"settings_yaml": GOOD})
    assert r["ok"] and not r["restarted"] and "power-cycle" in r["message"]


# ------------------------------------------------------------ flights

def test_flights_are_listed_and_zipped(env):
    tmp, a, _ = env
    make_flight(tmp)
    make_flight(tmp, "mission_20261108_120000", pics=0)
    (tmp / "captures" / "not_a_flight").mkdir()
    fl = {f["name"]: f for f in a.list_flights()}
    assert set(fl) == {"mission_20261108_101500", "mission_20261108_120000"}
    assert fl["mission_20261108_101500"]["pictures"] == 3
    assert fl["mission_20261108_101500"]["detections"] == 3
    assert fl["mission_20261108_120000"]["empty"]
    buf = io.BytesIO()
    a.zip_flight("mission_20261108_101500", buf)
    names = zipfile.ZipFile(io.BytesIO(buf.getvalue())).namelist()
    assert "mission_20261108_101500/detections.ndjson" in names and len(names) == 8


def test_flight_names_cannot_escape_the_capture_folder(env):
    _, a, _ = env
    for bad in ("../../etc", "mission_x", "", "mission_20261108_101500/../../"):
        assert a.flight_dir(bad) is None


# ------------------------------------------------------------ Wi-Fi

def test_wifi_off_when_armed_and_back_on_after_landing(env):
    tmp, a, rec = env
    set_aircraft(tmp, armed=False, rel_alt_m=0)
    a.wifi_tick(now=100)
    assert rec.calls == []                           # on the ground: leave it on
    set_aircraft(tmp, armed=True, rel_alt_m=0)
    a.wifi_tick(now=101)
    a.wifi_tick(now=102)
    assert rec.calls == [["sudo", "-n", "nmcli", "radio", "wifi", "off"]] and a.wifi_state == "off"
    set_aircraft(tmp, armed=False, rel_alt_m=0)       # landed and disarmed
    a.wifi_tick(now=103)
    assert a.wifi_state == "off"                      # not yet: wait wifi_back_on_after_s
    a.wifi_tick(now=108)
    assert rec.calls[-1] == ["sudo", "-n", "nmcli", "radio", "wifi", "on"] and a.wifi_state == "on"


def test_wifi_stays_off_while_airborne_even_if_disarmed_reported(env):
    tmp, a, rec = env
    set_aircraft(tmp, armed=True)
    a.wifi_tick(now=1)
    set_aircraft(tmp, armed=False, rel_alt_m=40)
    a.wifi_tick(now=100)
    assert a.wifi_state == "off"


# ------------------------------------------------------------ USB stick

def usb_settings(tmp, text=GOOD):
    d = tmp / "usb" / "SILM_TRANSFER" / "to_aircraft"
    d.mkdir(parents=True, exist_ok=True)
    (d / "settings.yaml").write_text(text)
    (d / "mission.waypoints").write_text(WPL)
    (d / "project.json").write_text(json.dumps({"name": "via usb"}))
    return d


def test_usb_settings_imported_once(env):
    tmp, a, rec = env
    d = usb_settings(tmp)
    assert a.usb_tick()["imported"]
    assert json.loads((d / "import_result.json").read_text())["ok"]
    assert json.loads((tmp / "silm-settings" / "settings_info.json").read_text())["source"] == "usb"
    assert (tmp / "silm-settings" / "project.json").exists()
    assert not a.usb_tick()["imported"]              # same file: not imported again
    assert len([c for c in rec.calls if "restart" in c]) == 1


def test_bad_usb_settings_rejected_and_reported_on_the_stick(env):
    tmp, a, _ = env
    d = usb_settings(tmp, yaml.safe_dump({"camera": {"source": "webcam"}}))
    assert not a.usb_tick()["imported"]
    res = json.loads((d / "import_result.json").read_text())
    assert not res["ok"] and "camera.source" in res["message"]
    assert not (tmp / "silm-settings" / "settings.yaml").exists()


def test_flights_copied_to_usb_and_only_changes_recopied(env):
    tmp, a, _ = env
    fd = make_flight(tmp)
    (tmp / "usb" / "SILM_TRANSFER").mkdir(parents=True)
    assert a.usb_tick()["copied"] == ["mission_20261108_101500"]
    dst = tmp / "usb" / "SILM_TRANSFER" / "from_aircraft" / "mission_20261108_101500"
    assert sorted(os.listdir(dst)) == sorted(os.listdir(fd))
    assert a.usb_tick()["copied"] == []              # nothing new
    (fd / "det_00009_1.jpg").write_bytes(b"\xff\xd8new")
    assert a.usb_tick()["copied"] == ["mission_20261108_101500"]
    st = json.loads((tmp / "usb" / "SILM_TRANSFER" / "aircraft_status.json").read_text())
    assert st["flights"][0]["name"] == "mission_20261108_101500"


def test_usb_does_nothing_while_flying(env):
    tmp, a, _ = env
    usb_settings(tmp)
    make_flight(tmp)
    set_aircraft(tmp, armed=True)
    assert a.usb_tick() == {"imported": False, "copied": [], "skipped": "flying"}
    assert not (tmp / "silm-settings" / "settings.yaml").exists()


def test_no_usb_stick_is_fine(env):
    _, a, _ = env
    assert not a.usb_present() and a.usb_tick()["skipped"] is None


# ------------------------------------------------------------ HTTP

@pytest.fixture
def http(env):
    tmp, a, rec = env
    srv = make_server(a, host="127.0.0.1", port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield tmp, a, rec, f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def get(url):
    with urllib.request.urlopen(url, timeout=10) as r:
        return r.status, r.read(), r.headers


def post(url, obj):
    req = urllib.request.Request(url, json.dumps(obj).encode(), {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_http_status_settings_flights_and_download(http):
    tmp, a, rec, url = http
    make_flight(tmp)
    set_aircraft(tmp, armed=False, rel_alt_m=0, mode="AUTO", gps="OK")
    code, body, _ = get(url + "/api/status")
    st = json.loads(body)
    assert code == 200 and st["ok"] and st["software"] == "2.0.0" and st["flights"] == 1
    assert st["program_running"] and not st["flying"] and st["aircraft"]["gps"] == "OK"
    code, r = post(url + "/api/settings", {"settings_yaml": GOOD, "mission_wpl": WPL})
    assert code == 200 and r["ok"]
    assert "tractor" in json.loads(get(url + "/api/settings")[1])["settings_yaml"]
    assert json.loads(get(url + "/api/flights")[1])["flights"][0]["pictures"] == 3
    code, data, hdr = get(url + "/api/flights/mission_20261108_101500.zip")
    assert code == 200 and hdr["Content-Type"] == "application/zip"
    assert len(zipfile.ZipFile(io.BytesIO(data)).namelist()) == 8


def test_http_rejections(http):
    tmp, a, rec, url = http
    code, r = post(url + "/api/settings", {"settings_yaml": "camera: {source: x}"})
    assert code == 400 and not r["ok"]
    set_aircraft(tmp, armed=True)
    code, r = post(url + "/api/settings", {"settings_yaml": GOOD})
    assert code == 409 and "ARMED" in r["message"]
    with pytest.raises(urllib.error.HTTPError) as e:
        get(url + "/api/flights/mission_20990101_000000.zip")
    assert e.value.code == 404
    with pytest.raises(urllib.error.HTTPError):
        get(url + "/api/flights/..%2F..%2Fetc.zip")
    req = urllib.request.Request(url + "/api/settings", b"{not json", {"Content-Type": "application/json"})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req, timeout=5)
    assert e.value.code == 400


def test_http_log_endpoint(http):
    _, a, rec, url = http
    assert "log" in json.loads(get(url + "/api/log")[1])
    assert rec.calls[-1][0] == "journalctl"


def test_mount_point_not_touched_without_a_silm_labelled_stick(env):
    tmp, a, _ = env
    usb_settings(tmp)                               # files exist at the mount point...
    a.usb_device = str(tmp / "dev" / "disk" / "by-label" / "SILM")   # ...but no stick
    assert not a.usb_present() and not a.usb_tick()["imported"]
    (tmp / "dev" / "disk" / "by-label").mkdir(parents=True)
    (tmp / "dev" / "disk" / "by-label" / "SILM").write_text("")      # stick plugged in
    assert a.usb_present() and a.usb_tick()["imported"]
