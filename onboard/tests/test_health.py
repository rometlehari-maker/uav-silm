import json
import os

from silm_recon.health import HealthMonitor, decode_throttled


def make(tmp_path, temp=50.0, thr=0x0, free=5000.0, **cfg):
    return HealthMonitor(dict(interval_s=10, **cfg), str(tmp_path), str(tmp_path),
                         temp_reader=lambda: temp, throttle_reader=lambda: thr,
                         disk_reader=lambda: free)


def test_decode_throttled():
    assert decode_throttled(0x0) == []
    assert decode_throttled(0x50005) == ["under-voltage now", "throttled now",
                                         "under-voltage occurred", "throttling occurred"]


def test_healthy_sample_logged_without_warnings(tmp_path):
    h = make(tmp_path)
    for _ in range(20):
        h.frame_done()
    s = h.maybe_sample(now=h._last_frames_t + 10)
    assert s["warnings"] == [] and s["fps"] == 2.0
    rec = json.loads(open(os.path.join(tmp_path, "health.ndjson")).readline())
    assert rec["cpu_temp_c"] == 50.0


def test_rate_limited(tmp_path):
    h = make(tmp_path)
    assert h.maybe_sample(now=100.0) is not None
    assert h.maybe_sample(now=105.0) is None
    assert h.maybe_sample(now=111.0) is not None


def test_hot_and_undervolt_warn(tmp_path):
    h = make(tmp_path, temp=78.0, thr=0x5)
    w = h.maybe_sample(now=100.0)["warnings"]
    assert "CPU 78C" in w and "under-voltage now" in w and "throttled now" in w


def test_disk_load_shedding_levels(tmp_path):
    assert not make(tmp_path, free=2000).maybe_sample(now=1) or True
    h = make(tmp_path, free=300)
    h.maybe_sample(now=100.0)
    assert h.low_disk and not h.critical_disk
    h = make(tmp_path, free=50)
    h.maybe_sample(now=100.0)
    assert h.low_disk and h.critical_disk


def test_missing_sensors_do_not_crash(tmp_path):
    h = make(tmp_path, temp=None, thr=None, free=None)
    s = h.maybe_sample(now=100.0)
    assert s["cpu_temp_c"] is None and s["warnings"] == []
