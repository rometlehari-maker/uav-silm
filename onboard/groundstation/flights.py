"""The flight library on the laptop, and the data the Image analysis tab shows.

A flight is the folder the aircraft wrote (mission_YYYYmmdd_HHMMSS/):
    det_00001_<t>.jpg + .json   a picture of something it recognised, with the
                                ground position of the object (geo-referenced)
    detections.ndjson           the same records, one per line
    frames/frame_<t>_<n>.jpg    ordinary pictures every few seconds (optional)
    track.ndjson                the aircraft's position once a second
    gps_health.ndjson, health.ndjson, optical_flow.ndjson

Every picture gets coordinates: detections carry the object's ground position
(or the aircraft's, if geo-referencing wasn't possible); ordinary frames get
the aircraft's position at the moment they were taken, interpolated from the
track by their timestamp.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import time
from bisect import bisect_left

FLIGHT_RE = re.compile(r"^mission_[0-9A-Za-z_\-]+$")
FRAME_RE = re.compile(r"^frame_(\d+(?:\.\d+)?)_(\d+)\.jpe?g$", re.I)
DET_RE = re.compile(r"^det_\d+_\d+\.jpe?g$", re.I)


def default_library() -> str:
    return os.path.join(os.path.expanduser("~"), "SILM", "flights")


def read_ndjson(path: str) -> list:
    out = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        pass          # a line cut off by a power loss
    except OSError:
        pass
    return out


def flight_dir(library: str, name: str) -> str | None:
    if not FLIGHT_RE.match(name or "") or ".." in name:
        return None
    p = os.path.join(library, name)
    return p if os.path.isdir(p) else None


def _when(name: str) -> str:
    m = re.match(r"^mission_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})", name)
    return f"{m[1]}-{m[2]}-{m[3]} {m[4]}:{m[5]}" if m else ""


def list_library(library: str) -> list[dict]:
    out = []
    try:
        names = sorted(os.listdir(library), reverse=True)
    except OSError:
        return out
    for n in names:
        d = flight_dir(library, n)
        if not d:
            continue
        dets = read_ndjson(os.path.join(d, "detections.ndjson"))
        frames_dir = os.path.join(d, "frames")
        nframes = len([f for f in os.listdir(frames_dir) if FRAME_RE.match(f)]) \
            if os.path.isdir(frames_dir) else 0
        out.append({"name": n, "when": _when(n), "detections": sum(len(r.get("detections", []))
                                                                      for r in dets),
                    "pictures": len(dets) + nframes, "frames": nframes,
                    "track_points": len(read_ndjson(os.path.join(d, "track.ndjson")))})
    return out


def _interp_track(track: list, t: float) -> dict | None:
    """Aircraft position at time t from the 1 Hz track (None if t is outside it
    by more than 5 s)."""
    if not track:
        return None
    ts = [p["t"] for p in track]
    i = bisect_left(ts, t)
    if i == 0:
        p = track[0]
        return dict(p, interp=False) if abs(p["t"] - t) <= 5 else None
    if i >= len(track):
        p = track[-1]
        return dict(p, interp=False) if abs(p["t"] - t) <= 5 else None
    a, b = track[i - 1], track[i]
    if b["t"] - a["t"] > 10:          # a gap in the track: don't make positions up
        near = a if t - a["t"] < b["t"] - t else b
        return dict(near, interp=False) if abs(near["t"] - t) <= 5 else None
    f = (t - a["t"]) / max(1e-6, b["t"] - a["t"])
    return {"t": t, "lat": a["lat"] + f * (b["lat"] - a["lat"]),
            "lon": a["lon"] + f * (b["lon"] - a["lon"]),
            "alt": (a.get("alt") or 0) + f * ((b.get("alt") or 0) - (a.get("alt") or 0)),
            "gps": a.get("gps"), "interp": True}


def load_flight(library: str, name: str) -> dict:
    d = flight_dir(library, name)
    if d is None:
        raise FileNotFoundError(name)
    track = sorted((p for p in read_ndjson(os.path.join(d, "track.ndjson"))
                    if p.get("lat") is not None and p.get("t") is not None), key=lambda p: p["t"])
    pictures = []
    for r in read_ndjson(os.path.join(d, "detections.ndjson")):
        ac = r.get("aircraft") or {}
        for k, det in enumerate(r.get("detections") or []):
            lat, lon, src = det.get("ground_lat"), det.get("ground_lon"), "object (geo-referenced)"
            if lat is None or lon is None:
                lat, lon, src = ac.get("lat"), ac.get("lon"), "aircraft (no ground position)"
            pictures.append({
                "kind": "detection", "img": r.get("jpg"), "seq": r.get("seq"), "n": k,
                "time": r.get("time"), "label": det.get("label"), "score": det.get("score"),
                "box": det.get("box_norm"), "lat": lat, "lon": lon, "pos_source": src,
                "aircraft_lat": ac.get("lat"), "aircraft_lon": ac.get("lon"),
                "alt_m": ac.get("rel_alt_m"), "heading_deg": ac.get("yaw_deg"),
                "mode": ac.get("mode")})
    frames_dir = os.path.join(d, "frames")
    if os.path.isdir(frames_dir):
        for f in sorted(os.listdir(frames_dir)):
            m = FRAME_RE.match(f)
            if not m:
                continue
            t = float(m.group(1))
            p = _interp_track(track, t)
            pictures.append({
                "kind": "frame", "img": f"frames/{f}", "seq": int(m.group(2)), "n": 0,
                "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t)), "label": None,
                "score": None, "box": None,
                "lat": p["lat"] if p else None, "lon": p["lon"] if p else None,
                "pos_source": ("aircraft (interpolated from the track)" if p and p["interp"]
                               else "aircraft (nearest track point)" if p else "unknown (no track)"),
                "aircraft_lat": p["lat"] if p else None, "aircraft_lon": p["lon"] if p else None,
                "alt_m": p.get("alt") if p else None, "heading_deg": None, "mode": None})
    for i, p in enumerate(pictures):
        p["id"] = i
    gps = read_ndjson(os.path.join(d, "gps_health.ndjson"))
    health = [h for h in read_ndjson(os.path.join(d, "health.ndjson")) if h.get("warnings")]
    labels = sorted({p["label"] for p in pictures if p["label"]})
    dur = round((track[-1]["t"] - track[0]["t"]) / 60.0, 1) if len(track) > 1 else None
    return {"name": name, "when": _when(name), "pictures": pictures, "track": track,
            "labels": labels, "gps_events": gps, "health_warnings": health,
            "summary": {"pictures": len(pictures),
                        "detections": sum(p["kind"] == "detection" for p in pictures),
                        "frames": sum(p["kind"] == "frame" for p in pictures),
                        "with_position": sum(p["lat"] is not None for p in pictures),
                        "minutes_logged": dur,
                        "gps_problems": sum(1 for g in gps if any(
                            k in str(g.get("event", "")) for k in ("LOST", "DEGRADED", "JAM")))}}


def image_path(library: str, name: str, rel: str) -> str | None:
    """Absolute path of a picture inside a flight (None if outside it / not a picture)."""
    d = flight_dir(library, name)
    if d is None or not rel or not rel.lower().endswith((".jpg", ".jpeg", ".png")):
        return None
    root = os.path.realpath(d)
    p = os.path.realpath(os.path.join(d, rel))
    return p if p.startswith(root + os.sep) and os.path.isfile(p) else None


def to_geojson(flight: dict) -> dict:
    feats = [{"type": "Feature",
              "geometry": {"type": "Point", "coordinates": [p["lon"], p["lat"]]},
              "properties": {k: p[k] for k in ("kind", "label", "score", "time", "img",
                                               "pos_source", "alt_m")}}
             for p in flight["pictures"] if p["lat"] is not None]
    if len(flight["track"]) > 1:
        feats.append({"type": "Feature", "properties": {"kind": "track"},
                      "geometry": {"type": "LineString",
                                   "coordinates": [[t["lon"], t["lat"]] for t in flight["track"]]}})
    return {"type": "FeatureCollection", "features": feats}


def to_csv(flight: dict) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["picture", "kind", "label", "score", "time", "latitude", "longitude",
                "position_from", "altitude_m", "heading_deg"])
    for p in flight["pictures"]:
        w.writerow([p["img"], p["kind"], p["label"] or "", "" if p["score"] is None else p["score"],
                    p["time"], p["lat"], p["lon"], p["pos_source"], p["alt_m"], p["heading_deg"]])
    return buf.getvalue()
