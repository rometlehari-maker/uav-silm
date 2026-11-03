#!/usr/bin/env python3
"""Convert a mission's detections.ndjson into a GeoJSON for mapping.

Run after a flight, on the ground:
    python3 tools/make_geojson.py /path/to/mission_YYYYmmdd_HHMMSS/detections.ndjson

Produces detections.geojson next to it. Each detection with a valid ground fix
becomes a Point feature (class, score, time, image filename); features can be
dropped straight onto QGIS, Mission Planner's map, geojson.io, etc.
"""

from __future__ import annotations

import json
import os
import sys


def main(path: str) -> int:
    features = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            ac = rec.get("aircraft", {})
            for d in rec.get("detections", []):
                lat = d.get("ground_lat")
                lon = d.get("ground_lon")
                # Fall back to the aircraft position if no ground projection.
                if lat is None or lon is None:
                    lat, lon = ac.get("lat"), ac.get("lon")
                    geom_note = "aircraft_position"
                else:
                    geom_note = "ground_projection"
                if lat is None or lon is None:
                    continue
                features.append({
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [lon, lat]},
                    "properties": {
                        "seq": rec.get("seq"),
                        "time": rec.get("time"),
                        "label": d.get("label"),
                        "score": d.get("score"),
                        "image": rec.get("jpg"),
                        "position_source": geom_note,
                        "aircraft_alt_m": ac.get("rel_alt_m"),
                    },
                })
    out = os.path.join(os.path.dirname(path), "detections.geojson")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"type": "FeatureCollection", "features": features}, fh, indent=2)
    print(f"wrote {len(features)} features -> {out}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
