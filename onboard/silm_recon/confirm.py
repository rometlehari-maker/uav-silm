"""Multi-frame confirmation: only report an object once it has been seen in
several frames at (roughly) the same place on the ground.

Single-frame false alarms (a sun glint flagged as fire, a shadow called a car)
rarely repeat at the same ground position in consecutive frames, while real
objects do. At 100 m AGL a target stays in view ~5 s at cruise speed, i.e. many
frames even at Pi-3 frame rates, so asking for 2-3 sightings costs nothing in
coverage and removes most one-off errors.

Association: same label AND within `radius_m` on the ground (when both have a
valid geo-reference), else within `pixel_radius` in normalised image units.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field


def _ground_dist_m(lat1, lon1, lat2, lon2) -> float:
    dn = (lat2 - lat1) * 111320.0
    de = (lon2 - lon1) * 111320.0 * math.cos(math.radians(0.5 * (lat1 + lat2)))
    return math.hypot(dn, de)


@dataclass
class _Track:
    label: str
    lat: float | None
    lon: float | None
    cx: float
    cy: float
    hits: int = 1
    best_score: float = 0.0
    first_t: float = field(default_factory=time.time)
    last_t: float = field(default_factory=time.time)
    confirmed: bool = False


class ConfirmationFilter:
    def __init__(self, cfg: dict | None = None):
        cfg = cfg or {}
        self.frames = max(1, int(cfg.get("frames", 2)))
        self.window_s = float(cfg.get("window_s", 3.0))
        self.radius_m = float(cfg.get("radius_m", 30.0))
        self.pixel_radius = float(cfg.get("pixel_radius", 0.15))
        self._tracks: list[_Track] = []

    def update(self, events: list[dict], now: float | None = None) -> list[dict]:
        """Feed this frame's events ({label, score, box, ground}). Returns the
        events that just became confirmed (each track is returned once)."""
        now = time.time() if now is None else now
        # drop stale tracks
        self._tracks = [t for t in self._tracks if now - t.last_t <= self.window_s]
        if self.frames <= 1:
            return list(events)

        out = []
        used: set[int] = set()
        for ev in events:
            g = ev.get("ground")
            lat = g.lat if (g is not None and g.valid) else None
            lon = g.lon if (g is not None and g.valid) else None
            x0, y0, x1, y1 = ev["box"]
            cx, cy = 0.5 * (x0 + x1), 0.5 * (y0 + y1)

            best, best_d = None, None
            for i, t in enumerate(self._tracks):
                if i in used or t.label != ev["label"]:
                    continue
                if lat is not None and t.lat is not None:
                    d = _ground_dist_m(lat, lon, t.lat, t.lon)
                    ok = d <= self.radius_m
                else:
                    d = math.hypot(cx - t.cx, cy - t.cy) * 100.0
                    ok = d <= self.pixel_radius * 100.0
                if ok and (best_d is None or d < best_d):
                    best, best_d = i, d

            if best is None:
                self._tracks.append(_Track(ev["label"], lat, lon, cx, cy,
                                           best_score=ev["score"], first_t=now, last_t=now))
                used.add(len(self._tracks) - 1)
                continue

            t = self._tracks[best]
            used.add(best)
            t.hits += 1
            t.last_t = now
            t.best_score = max(t.best_score, ev["score"])
            if lat is not None:
                if t.lat is None:
                    t.lat, t.lon = lat, lon
                else:  # running average of the ground position
                    t.lat += (lat - t.lat) / t.hits
                    t.lon += (lon - t.lon) / t.hits
            t.cx, t.cy = cx, cy
            if not t.confirmed and t.hits >= self.frames:
                t.confirmed = True
                out.append(dict(ev, hits=t.hits))
        return out
