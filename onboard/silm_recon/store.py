"""Detection storage + de-duplication.

Everything is written to local storage (the Pi's SD card or a USB stick) so the
mission needs no link. For each saved target we write:

  * a JPEG of the full frame with the detection boxed and labelled
  * a per-detection JSON sidecar with class, score, pixel box, aircraft state
    and the geo-referenced ground lat/lon
  * one appended line in ``detections.ndjson`` (crash-safe, newline-delimited)
  * one appended row in ``detections.csv`` for quick spreadsheet review

``tools/make_geojson.py`` turns the NDJSON into a GeoJSON you can drop straight
onto a map after the flight.

De-duplication stops the same combine being saved 50 times as you fly over it:
a new detection of a class is skipped if a detection of that class was already
saved within ``cooldown_s`` seconds AND within ``min_separation_m`` metres on
the ground.
"""

from __future__ import annotations

import csv
import json
import math
import os
import time
from dataclasses import dataclass


@dataclass
class SavedRecord:
    seq: int
    path_jpg: str


def _haversine_m(lat1, lon1, lat2, lon2) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


class DetectionStore:
    def __init__(self, cfg: dict):
        base = os.path.expanduser(cfg.get("output_dir", "~/silm_captures"))
        stamp = time.strftime("%Y%m%d_%H%M%S")
        self.dir = os.path.join(base, f"mission_{stamp}")
        os.makedirs(self.dir, exist_ok=True)

        self.cooldown_s = float(cfg.get("cooldown_s", 8.0))
        self.min_separation_m = float(cfg.get("min_separation_m", 40.0))
        self.jpeg_quality = int(cfg.get("jpeg_quality", 90))
        self.save_annotated = bool(cfg.get("save_annotated", True))

        self._seq = 0
        # Per-class list of (time, lat, lon) for recent saves.
        self._recent: dict[str, list[tuple[float, float, float]]] = {}

        self._ndjson = open(os.path.join(self.dir, "detections.ndjson"), "a", buffering=1)
        csv_path = os.path.join(self.dir, "detections.csv")
        new_csv = not os.path.exists(csv_path) or os.path.getsize(csv_path) == 0
        self._csv_fh = open(csv_path, "a", newline="")
        self._csv = csv.writer(self._csv_fh)
        if new_csv:
            self._csv.writerow([
                "seq", "iso_time", "label", "score",
                "lat", "lon", "ground_lat", "ground_lon",
                "rel_alt_m", "heading_deg", "jpg",
            ])

    # ------------------------------------------------------------------ #

    def is_duplicate(self, label: str, lat: float | None, lon: float | None) -> bool:
        now = time.time()
        recent = self._recent.get(label, [])
        # Drop entries older than the cooldown window.
        recent = [r for r in recent if now - r[0] <= self.cooldown_s]
        self._recent[label] = recent
        if lat is None or lon is None:
            # No geo fix: fall back to a pure time cooldown.
            return bool(recent)
        for (_, rlat, rlon) in recent:
            if _haversine_m(lat, lon, rlat, rlon) <= self.min_separation_m:
                return True
        return False

    def save(self, frame_bgr, detections, state, ground) -> SavedRecord | None:
        """Persist one event. ``detections`` is a list of dicts:
        {label, score, box, ground: GroundPoint|None}. ``state`` is a StateView.
        """
        import cv2

        self._seq += 1
        seq = self._seq
        iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        fname = f"det_{seq:05d}_{int(time.time())}"
        jpg_path = os.path.join(self.dir, fname + ".jpg")

        img = frame_bgr
        if self.save_annotated:
            img = frame_bgr.copy()
            h, w = img.shape[:2]
            for d in detections:
                x0, y0, x1, y1 = d["box"]
                p0 = (int(x0 * w), int(y0 * h))
                p1 = (int(x1 * w), int(y1 * h))
                cv2.rectangle(img, p0, p1, (0, 255, 255), 2)
                cv2.putText(
                    img, f"{d['label']} {d['score']:.2f}",
                    (p0[0], max(14, p0[1] - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2,
                )
        cv2.imwrite(jpg_path, img, [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality])

        # Primary (highest-score) detection drives the top-level geo fields.
        primary = max(detections, key=lambda d: d["score"])
        g = primary.get("ground")
        glat = g.lat if (g and g.valid) else None
        glon = g.lon if (g and g.valid) else None

        record = {
            "seq": seq,
            "time": iso,
            "jpg": os.path.basename(jpg_path),
            "aircraft": {
                "lat": state.lat, "lon": state.lon,
                "rel_alt_m": state.rel_alt, "alt_amsl_m": state.alt_amsl,
                "roll_deg": math.degrees(state.roll),
                "pitch_deg": math.degrees(state.pitch),
                "yaw_deg": math.degrees(state.yaw) % 360.0,
                "ground_speed_mps": state.ground_speed,
                "mode": state.mode, "armed": state.armed,
            },
            "detections": [
                {
                    "label": d["label"],
                    "score": round(d["score"], 4),
                    "box_norm": [round(v, 5) for v in d["box"]],
                    "ground_lat": (d["ground"].lat if d.get("ground") and d["ground"].valid else None),
                    "ground_lon": (d["ground"].lon if d.get("ground") and d["ground"].valid else None),
                }
                for d in detections
            ],
        }
        self._ndjson.write(json.dumps(record) + "\n")
        self._ndjson.flush()
        os.fsync(self._ndjson.fileno())

        with open(os.path.join(self.dir, fname + ".json"), "w") as fh:
            json.dump(record, fh, indent=2)

        self._csv.writerow([
            seq, iso, primary["label"], round(primary["score"], 4),
            state.lat, state.lon, glat, glon,
            round(state.rel_alt, 1), round(math.degrees(state.yaw) % 360.0, 1),
            os.path.basename(jpg_path),
        ])
        self._csv_fh.flush()

        # Record for de-dup under every class present in this event.
        now = time.time()
        for d in detections:
            gp = d.get("ground")
            lat = gp.lat if (gp and gp.valid) else state.lat
            lon = gp.lon if (gp and gp.valid) else state.lon
            self._recent.setdefault(d["label"], []).append((now, lat, lon))

        return SavedRecord(seq=seq, path_jpg=jpg_path)

    def close(self) -> None:
        for fh in (self._ndjson, self._csv_fh):
            try:
                fh.flush()
                fh.close()
            except Exception:
                pass
