"""Remaining untested paths: offline map tiles (XYZ + MBTiles), the tile
downloader, the optical-flow MAVLink message, and the GPS-health log."""

import functools
import http.server
import json
import os
import sqlite3
import subprocess
import sys
import threading

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from silm_recon import tiles  # noqa: E402
from silm_recon.failsafe import FlowEstimate, GpsHealthMonitor, OpticalFlowAid  # noqa: E402

TOOLS = os.path.join(os.path.dirname(__file__), "..", "tools")


def tile_png(color):
    img = np.zeros((256, 256, 3), np.uint8)
    img[:] = color
    return cv2.imencode(".png", img)[1].tobytes()


def test_tile_math_round_trip():
    for lat, lon in [(59.40, 24.70), (58.38, 26.72), (-33.9, 151.2)]:
        x, y = tiles.deg2tile(lat, lon, 17)
        la, lo = tiles.tile2deg(x, y, 17)           # NW corner of that tile
        la2, lo2 = tiles.tile2deg(x + 1, y + 1, 17)  # SE corner
        assert la2 <= lat <= la and lo <= lon <= lo2


def test_basemap_xyz_folder(tmp_path):
    z, (x, y) = 17, tiles.deg2tile(59.40, 24.70, 17)
    d = tmp_path / "xyz" / str(z) / str(x)
    d.mkdir(parents=True)
    (d / f"{y}.png").write_bytes(tile_png((10, 200, 30)))
    bm = tiles.OfflineBasemap({"enabled": True, "path": str(tmp_path / "xyz"), "zoom": 17})
    p = bm.patch_at(59.40, 24.70)
    assert (p.x, p.y) == (x, y) and p.image_bgr.shape == (256, 256, 3)
    assert tuple(p.image_bgr[0, 0]) == (10, 200, 30)
    assert bm.patch_at(10.0, 10.0).image_bgr is None    # not cached -> no image, no crash


def test_basemap_mbtiles_uses_tms_rows(tmp_path):
    z, (x, y) = 15, tiles.deg2tile(59.40, 24.70, 15)
    db = tmp_path / "m.mbtiles"
    con = sqlite3.connect(db)
    con.execute("create table tiles (zoom_level int, tile_column int, tile_row int, tile_data blob)")
    con.execute("insert into tiles values (?,?,?,?)", (z, x, (2 ** z - 1) - y, tile_png((1, 2, 3))))
    con.commit()
    con.close()
    bm = tiles.OfflineBasemap({"enabled": True, "path": str(db), "zoom": z})
    assert tuple(bm.patch_at(59.40, 24.70).image_bgr[5, 5]) == (1, 2, 3)
    bm.close()


def test_basemap_missing_file_disables_itself(tmp_path):
    bm = tiles.OfflineBasemap({"enabled": True, "path": str(tmp_path / "none.mbtiles")})
    assert not bm.enabled and bm.patch_at(59.4, 24.7) is None


def test_download_tiles_from_local_server(tmp_path):
    src = tmp_path / "server"
    z = 16
    x0, y0 = tiles.deg2tile(59.402, 24.698, z)
    x1, y1 = tiles.deg2tile(59.398, 24.702, z)
    for x in range(min(x0, x1), max(x0, x1) + 1):
        for y in range(min(y0, y1), max(y0, y1) + 1):
            (src / str(z) / str(x)).mkdir(parents=True, exist_ok=True)
            (src / str(z) / str(x) / f"{y}.png").write_bytes(tile_png((x % 255, y % 255, 7)))
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(src))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{srv.server_address[1]}/{{z}}/{{x}}/{{y}}.png"
        out = tmp_path / "cache"
        env = dict(os.environ, NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost")
        r = subprocess.run([sys.executable, os.path.join(TOOLS, "download_tiles.py"), "--url", url,
                            "--bbox", "24.698", "59.398", "24.702", "59.402", "--zoom", str(z),
                            "--out", str(out), "--delay", "0"], capture_output=True, text=True,
                           env=env, timeout=60)
        assert r.returncode == 0, r.stdout + r.stderr
        got = sorted(p.name for p in out.rglob("*.png"))
        want = sorted(p.name for p in src.rglob("*.png"))
        assert got == want and len(got) >= 1
        bm = tiles.OfflineBasemap({"enabled": True, "path": str(out), "zoom": z})
        assert bm.patch_at(59.40, 24.70).image_bgr is not None
    finally:
        srv.shutdown()


def test_flow_velocity_message_matches_pymavlink():
    mavutil = pytest.importorskip("pymavlink.mavutil")
    sent = []

    class FakeMav:
        def vision_speed_estimate_send(self, *a):
            sent.append(a)
            # the real encoder must accept the same arguments
            mavutil.mavlink.MAVLink(None).vision_speed_estimate_encode(*a)

    class FakeMaster:
        mav = FakeMav()

    aid = OpticalFlowAid({"enabled": True, "send_to_fc": True})
    aid.send(FakeMaster(), FlowEstimate(vx_ned=12.5, vy_ned=-3.0, quality=0.8, valid=True, t=100.0))
    assert sent == [(100_000_000, 12.5, -3.0, 0.0)]
    OpticalFlowAid({"enabled": True, "send_to_fc": False}).send(FakeMaster(), FlowEstimate(valid=True))
    assert len(sent) == 1                    # off by default: nothing sent


def test_gps_health_log_written(tmp_path):
    class M:
        def __init__(self, t, **k):
            self._t = t
            self.__dict__.update(k)

        def get_type(self):
            return self._t

    mon = GpsHealthMonitor(log_dir=str(tmp_path))
    mon.ingest(M("GPS_RAW_INT", fix_type=3, satellites_visible=12, eph=80))
    mon.ingest(M("EKF_STATUS_REPORT", flags=(1 << 4) | (1 << 2)))
    mon.ingest(M("GPS_RAW_INT", fix_type=0, satellites_visible=0, eph=65535))
    lines = [json.loads(ln) for ln in open(tmp_path / "gps_health.ndjson")]
    assert any("LOST" in ln["event"] for ln in lines)
