"""config.yaml + settings.yaml (from the Ground Station): merging, validation,
and that a bad settings file falls back to the defaults instead of stopping."""

import os

import pytest
import yaml

import run
from silm_recon.config import deep_merge, load_config, validate_config

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "..", "config.yaml")


def write(p, data):
    p.write_text(yaml.safe_dump(data))
    return str(p)


def test_shipped_config_is_valid():
    cfg = load_config(CONFIG, settings="/nonexistent.yaml")
    assert validate_config(cfg) == []
    assert cfg["_settings_loaded"] is False
    assert cfg["program"]["mode"] == "flight"
    assert cfg["health"]["warn_temp_c"] == 75 and cfg["health"]["low_mem_mb"] == 200


def test_deep_merge_merges_sections_and_replaces_lists():
    base = {"a": {"x": 1, "y": 2}, "targets": ["car", "truck"], "k": 1}
    out = deep_merge(base, {"a": {"y": 5}, "targets": ["fire"]})
    assert out == {"a": {"x": 1, "y": 5}, "targets": ["fire"], "k": 1}
    assert base["a"]["y"] == 2                     # the input is not modified


def test_settings_overlay_is_applied(tmp_path):
    s = write(tmp_path / "settings.yaml", {"targets": ["tractor"], "activation": {"min_alt_m": 35},
                                           "camera": {"url": "rtsp://10.0.0.9:554/x"}})
    cfg = load_config(CONFIG, settings=s)
    assert cfg["_settings_loaded"] and cfg["targets"] == ["tractor"]
    assert cfg["activation"]["min_alt_m"] == 35
    assert cfg["activation"]["require_armed"] is True          # untouched defaults stay
    assert cfg["camera"]["url"] == "rtsp://10.0.0.9:554/x" and cfg["camera"]["source"] == "rtsp"
    assert validate_config(cfg) == []


def test_settings_path_from_environment(tmp_path, monkeypatch):
    s = write(tmp_path / "env.yaml", {"targets": ["car"]})
    monkeypatch.setenv("SILM_SETTINGS", s)
    assert load_config(CONFIG)["targets"] == ["car"]


@pytest.mark.parametrize("over, problem", [
    ({"camera": {"source": "webcam"}}, "camera.source"),
    ({"camera": {"url": "http://x"}}, "camera.url"),
    ({"telemetry": {"connection": ""}}, "telemetry.connection"),
    ({"telemetry": {"baud": "fast"}}, "telemetry.baud must be a number"),
    ({"detector": {"score_threshold": 1.5}}, "score_threshold"),
    ({"detector": {"roi": [0.5, 0.2, 0.4, 0.9]}}, "detector.roi"),
    ({"detector": {"tile_grid": [2, 9]}}, "tile_grid"),
    ({"detector": {"format": "onnx"}}, "detector.format"),
    ({"targets": "car"}, "targets"),
    ({"storage": {"output_dir": ""}}, "storage.output_dir"),
    ({"activation": {"min_alt_m": -5}}, "min_alt_m"),
    ({"activation": {"active_modes": []}}, "active_modes"),
])
def test_validation_catches_mistakes(over, problem):
    cfg = deep_merge(load_config(CONFIG, settings="/nonexistent.yaml"), over)
    errors = validate_config(cfg)
    assert any(problem in e for e in errors), errors


def test_missing_section_is_reported():
    assert "section 'camera' is missing" in validate_config({"telemetry": {}, "detector": {},
                                                             "storage": {}, "activation": {}})


def test_bad_settings_file_falls_back_to_defaults(tmp_path, capsys):
    s = write(tmp_path / "settings.yaml", {"camera": {"source": "webcam"}, "targets": ["car"]})
    cfg = run.load_checked(CONFIG, s)
    assert cfg["camera"]["source"] == "rtsp"              # defaults, not the bad file
    assert cfg["targets"] != ["car"] and cfg["_settings_rejected"]
    assert "IGNORED" in capsys.readouterr().out


def test_settings_file_that_is_not_a_mapping(tmp_path):
    p = tmp_path / "settings.yaml"
    p.write_text("- just\n- a list\n")
    with pytest.raises(ValueError):
        load_config(CONFIG, settings=str(p))


def test_bench_mode_records_on_the_ground_and_paths_are_resolved(tmp_path):
    s = write(tmp_path / "settings.yaml", {"program": {"mode": "bench"}})
    cfg = run.load_checked(CONFIG, s)
    assert cfg["activation"]["always_on"] is True
    assert os.path.isabs(cfg["detector"]["model"]) and cfg["detector"]["model"].endswith(
        os.path.join("models", "coco_ssd_mobilenet_v1_quant.tflite"))


def test_flight_mode_never_records_on_the_ground(tmp_path):
    cfg = run.load_checked(CONFIG, write(tmp_path / "s.yaml", {"program": {"mode": "flight"}}))
    assert cfg["activation"]["always_on"] is False and cfg["activation"]["require_armed"] is True


def test_check_command_line(tmp_path, capsys, monkeypatch):
    good = write(tmp_path / "good.yaml", {"targets": ["car"]})
    monkeypatch.setattr("sys.argv", ["run.py", "--check", "--settings", good])
    assert run.main() == 0
    bad = write(tmp_path / "bad.yaml", {"camera": {"source": "x"}})
    monkeypatch.setattr("sys.argv", ["run.py", "--check", "--settings", bad])
    assert run.main() == 1
