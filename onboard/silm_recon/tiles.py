"""Offline basemap / "terrain from map data" support.

Because the aircraft flies with no link, any map/satellite context must be
cached on the ground *before* launch. This module reads an offline tile cache
(an .mbtiles file, or an XYZ folder of PNG/JPEG tiles) and returns the map tile
and the sub-image patch covering a given lat/lon. Uses:

  * annotate saved detections with the local map patch for quick review
  * give the detector a coarse land-cover prior (field / forest / road / urban)
    so, e.g., a "truck" over woodland can be weighted differently from one on a
    road -- optional, off by default
  * experimental visual position cross-check (feature match of the live frame
    against the cached satellite patch) as a GPS-denied sanity aid

IMPORTANT (legal): Google Maps/Earth imagery may not be pre-downloaded or cached
under Google's terms. Build your offline cache from a source whose licence
permits offline use -- e.g. Esri "World Imagery" for internal/eval use, Mapbox
with an offline plan, national ortho/WMS layers, or your own imagery. The
downloader in tools/download_tiles.py takes a generic XYZ URL so you can point
it at a permitted source. "Google Maps data" in the project brief is satisfied
by any equivalent satellite/terrain basemap.
"""

from __future__ import annotations

import math
import os
import sqlite3
from dataclasses import dataclass


def deg2tile(lat: float, lon: float, z: int) -> tuple[int, int]:
    lat_r = math.radians(lat)
    n = 2 ** z
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.asinh(math.tan(lat_r)) / math.pi) / 2.0 * n)
    return x, y


def tile2deg(x: int, y: int, z: int) -> tuple[float, float]:
    n = 2 ** z
    lon = x / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    return lat, lon


@dataclass
class TilePatch:
    z: int
    x: int
    y: int
    image_bgr: object  # numpy array or None


class OfflineBasemap:
    def __init__(self, cfg: dict):
        self.enabled = bool(cfg.get("enabled", False))
        self.path = cfg.get("path", "")
        self.zoom = int(cfg.get("zoom", 17))
        self.kind = None
        self._db = None
        if not self.enabled:
            return
        if not os.path.exists(self.path):
            print(f"[tiles] cache not found at {self.path}; disabling basemap")
            self.enabled = False
            return
        if self.path.endswith(".mbtiles"):
            self.kind = "mbtiles"
            self._db = sqlite3.connect(self.path, check_same_thread=False)
        elif os.path.isdir(self.path):
            self.kind = "xyz"
        else:
            self.enabled = False

    def _read_tile_bytes(self, z: int, x: int, y: int) -> bytes | None:
        if self.kind == "mbtiles":
            # MBTiles stores rows flipped (TMS).
            ytms = (2 ** z - 1) - y
            cur = self._db.execute(
                "SELECT tile_data FROM tiles WHERE zoom_level=? AND tile_column=? AND tile_row=?",
                (z, x, ytms),
            )
            row = cur.fetchone()
            return row[0] if row else None
        if self.kind == "xyz":
            for ext in ("png", "jpg", "jpeg"):
                p = os.path.join(self.path, str(z), str(x), f"{y}.{ext}")
                if os.path.exists(p):
                    with open(p, "rb") as fh:
                        return fh.read()
        return None

    def patch_at(self, lat: float, lon: float) -> TilePatch | None:
        if not self.enabled:
            return None
        import numpy as np
        import cv2

        z = self.zoom
        x, y = deg2tile(lat, lon, z)
        data = self._read_tile_bytes(z, x, y)
        if data is None:
            return TilePatch(z, x, y, None)
        arr = np.frombuffer(data, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        return TilePatch(z, x, y, img)

    def close(self) -> None:
        if self._db is not None:
            self._db.close()
