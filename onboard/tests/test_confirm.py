from silm_recon.confirm import ConfirmationFilter
from silm_recon.georef import GroundPoint


def ev(label, lat=None, lon=None, box=(0.4, 0.4, 0.5, 0.5), score=0.6):
    g = GroundPoint(lat, lon, 100.0, True) if lat is not None else None
    return {"label": label, "score": score, "box": box, "ground": g}


def test_needs_n_sightings_then_reports_once():
    f = ConfirmationFilter({"frames": 3, "window_s": 3})
    assert f.update([ev("combine", 58.0, 24.0)], now=0.0) == []
    assert f.update([ev("combine", 58.00005, 24.00005)], now=0.5) == []   # ~7 m away
    out = f.update([ev("combine", 58.0001, 24.0)], now=1.0)
    assert len(out) == 1 and out[0]["label"] == "combine" and out[0]["hits"] == 3
    assert f.update([ev("combine", 58.0, 24.0)], now=1.5) == []           # not again


def test_one_off_false_alarm_never_confirms():
    f = ConfirmationFilter({"frames": 2})
    assert f.update([ev("fire", 58.0, 24.0)], now=0.0) == []
    assert f.update([], now=0.5) == []
    assert f.update([ev("fire", 58.01, 24.0)], now=1.0) == []  # 1.1 km away: different


def test_track_expires_after_window():
    f = ConfirmationFilter({"frames": 2, "window_s": 2})
    f.update([ev("car", 58.0, 24.0)], now=0.0)
    assert f.update([ev("car", 58.0, 24.0)], now=5.0) == []   # first sighting expired


def test_labels_do_not_mix():
    f = ConfirmationFilter({"frames": 2})
    f.update([ev("car", 58.0, 24.0)], now=0.0)
    assert f.update([ev("truck", 58.0, 24.0)], now=0.5) == []


def test_two_objects_same_class_tracked_separately():
    f = ConfirmationFilter({"frames": 2, "radius_m": 30})
    f.update([ev("hay_bale", 58.0, 24.0), ev("hay_bale", 58.001, 24.0)], now=0.0)  # 111 m apart
    out = f.update([ev("hay_bale", 58.0, 24.0), ev("hay_bale", 58.001, 24.0)], now=0.5)
    assert len(out) == 2


def test_pixel_fallback_without_georef():
    f = ConfirmationFilter({"frames": 2})
    f.update([ev("smoke", box=(0.2, 0.2, 0.3, 0.3))], now=0.0)
    assert len(f.update([ev("smoke", box=(0.22, 0.21, 0.32, 0.31))], now=0.4)) == 1


def test_frames_1_passes_everything_through():
    f = ConfirmationFilter({"frames": 1})
    assert len(f.update([ev("car", 58.0, 24.0)], now=0.0)) == 1
