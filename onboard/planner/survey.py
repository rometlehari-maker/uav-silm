"""Survey mission geometry for the SILM planner (laptop side).

Turns "this field, at this altitude, looking for this object" into a complete
mission for an aircraft with NO RC link:

    0  home (launch point)
    1  TAKEOFF            hand launch in AUTO, climb at TAKEOFF_PITCH
    2  DO_CHANGE_SPEED    survey airspeed
    3+ WAYPOINTs          survey lines (lawnmower), spaced from the camera's
                          DETECTION swath (fisheye + detection region aware)
    .. DO_LAND_START      start of the landing sequence (also where every
                          failsafe goes, RTL_AUTOLAND=2)
    .. WAYPOINT           approach start, into the landing direction
    .. LAND               touchdown point (belly landing, auto-disarm)

Turns: a fixed wing can't turn tighter than r = v^2 / (g tan(bank)). When the
line spacing is less than 2r, lines are flown in an interleaved order (every
k-th line, then back through the gaps) so consecutive lines are at least a
turn's width apart.
"""

from __future__ import annotations

import math
import os
import sys
from dataclasses import asdict, dataclass, field

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "tools"))

from silm_recon import mavmission as mm  # noqa: E402

G = 9.80665
R_EARTH = 6378137.0


# ---------------------------------------------------------------- inputs

@dataclass
class Camera:
    width: int = 1920
    height: int = 1080
    lens: str = "fisheye"         # fisheye | pinhole
    hfov_deg: float = 160.0
    fx: float | None = None       # pixels/radian from a calibration (overrides hfov)
    roi: tuple = (0.25, 0.25, 0.75, 0.75)   # detection region (x0, y0, x1, y1)
    tiles: int = 2                # tile grid side used inside the region
    imgsz: int = 320              # model input

    def focal(self) -> float:
        if self.fx:
            return float(self.fx)
        if self.lens == "fisheye":
            return (self.width / 2.0) / math.radians(self.hfov_deg / 2.0)
        return (self.width / 2.0) / math.tan(math.radians(self.hfov_deg / 2.0))

    def _ground_halfwidth(self, alt_m: float, px_half: float) -> float:
        a = px_half / self.focal()
        if self.lens == "fisheye":
            return alt_m * math.tan(min(a, math.radians(85)))
        return alt_m * a

    def swath_m(self, alt_m: float) -> float:
        """Across-track width of the DETECTION region on flat ground."""
        rw = self.roi[2] - self.roi[0]
        return 2.0 * self._ground_halfwidth(alt_m, self.width * rw / 2.0)

    def along_m(self, alt_m: float) -> float:
        rh = self.roi[3] - self.roi[1]
        return 2.0 * self._ground_halfwidth(alt_m, self.height * rh / 2.0)

    def pixels_at_model(self, size_m: float, alt_m: float) -> float:
        """Object size in pixels at the model input (never more than the
        camera itself recorded)."""
        px = size_m * self.focal() / alt_m
        rw = self.roi[2] - self.roi[0]
        return min(px, px * self.imgsz / (self.width * rw / self.tiles))

    def max_alt_for(self, size_m: float, min_px: float = 10.0) -> float:
        rw = self.roi[2] - self.roi[0]
        scale = min(1.0, self.imgsz / (self.width * rw / self.tiles))
        return size_m * self.focal() * scale / min_px


@dataclass
class PlanInput:
    launch: tuple                              # (lat, lon)
    area: list                                 # [(lat, lon), ...] polygon
    landing: tuple | None = None               # touchdown point (default: launch)
    landing_heading_deg: float = 0.0           # direction of travel at touchdown
    alt_m: float = 100.0                       # survey altitude above home
    speed_mps: float = 19.0                    # survey airspeed
    overlap: float = 0.2                       # fraction of the swath shared by neighbours
    line_angle_deg: float | None = None        # line direction; None = along the longest edge
    max_bank_deg: float = 45.0                 # planned bank limit (camera stability)
    edge_extend_m: float = 40.0                # fly this far past the area at each line end
    takeoff_alt_m: float = 50.0
    takeoff_pitch_deg: float = 15.0
    approach_len_m: float = 300.0              # straight final approach length
    approach_alt_m: float = 30.0               # height at the start of the final approach
    fence_radius_m: float = 1500.0             # FENCE_RADIUS on the aircraft
    alt_limit_m: float = 120.0                 # legal / FENCE_ALT_MAX
    endurance_min: float = 100.0               # design estimate - not yet flown!
    reserve_frac: float = 0.25                 # BATT_LOW_MAH share, plus margin
    target_size_m: float | None = None         # smaller side of the target, seen from above
    camera: Camera = field(default_factory=Camera)


# ---------------------------------------------------------------- local frame

class Local:
    """Flat-earth metres east/north of an origin (fine over a few km)."""

    def __init__(self, lat0, lon0):
        self.lat0, self.lon0 = lat0, lon0
        self.kx = math.radians(1) * R_EARTH * math.cos(math.radians(lat0))
        self.ky = math.radians(1) * R_EARTH

    def to_xy(self, lat, lon):
        return ((lon - self.lon0) * self.kx, (lat - self.lat0) * self.ky)

    def to_ll(self, x, y):
        return (self.lat0 + y / self.ky, self.lon0 + x / self.kx)


def _rot(x, y, a):
    c, s = math.cos(a), math.sin(a)
    return (x * c - y * s, x * s + y * c)


def _longest_edge_angle(poly_xy):
    best, ang = -1.0, 0.0
    for (x0, y0), (x1, y1) in zip(poly_xy, poly_xy[1:] + poly_xy[:1]):
        d = math.hypot(x1 - x0, y1 - y0)
        if d > best:
            best, ang = d, math.atan2(y1 - y0, x1 - x0)
    return ang


def _scanline_segments(poly, y):
    """x-intervals where the horizontal line at y is inside the polygon."""
    xs = []
    n = len(poly)
    for i in range(n):
        (x0, y0), (x1, y1) = poly[i], poly[(i + 1) % n]
        if (y0 <= y < y1) or (y1 <= y < y0):
            xs.append(x0 + (y - y0) * (x1 - x0) / (y1 - y0))
    xs.sort()
    return [(xs[i], xs[i + 1]) for i in range(0, len(xs) - 1, 2)]


def line_order(n: int, spacing: float, turn_diameter: float) -> list[int]:
    """Visiting order of n parallel lines so consecutive lines are at least
    one turn diameter apart where possible."""
    k = max(1, math.ceil(turn_diameter / spacing - 1e-9))
    if k == 1:
        return list(range(n))
    order = []
    for off in range(k):
        seq = list(range(off, n, k))
        order += seq if off % 2 == 0 else seq[::-1]
    return order


def turn_radius(speed_mps: float, bank_deg: float) -> float:
    return speed_mps ** 2 / (G * math.tan(math.radians(bank_deg)))


# ---------------------------------------------------------------- planning

def plan(p: PlanInput) -> dict:
    loc = Local(*p.launch)
    poly = [loc.to_xy(*ll) for ll in p.area]
    warnings: list[str] = []
    if len(poly) < 3:
        raise ValueError("the survey area needs at least 3 corners")

    cam = p.camera
    swath = cam.swath_m(p.alt_m)
    spacing = swath * (1.0 - p.overlap)
    r_turn = turn_radius(p.speed_mps, p.max_bank_deg)
    ang = math.radians(p.line_angle_deg) if p.line_angle_deg is not None else _longest_edge_angle(poly)
    # rotate the area so survey lines are horizontal
    rp = [_rot(x, y, -ang) for x, y in poly]
    ys = [y for _, y in rp]
    lines = []
    y = min(ys) + spacing / 2.0
    while y < max(ys):
        for x0, x1 in _scanline_segments(rp, y):
            lines.append((x0 - p.edge_extend_m, x1 + p.edge_extend_m, y))
        y += spacing
    if not lines:     # area narrower than one spacing: one line through the middle
        y = (min(ys) + max(ys)) / 2.0
        for x0, x1 in _scanline_segments(rp, y) or [(min(x for x, _ in rp), max(x for x, _ in rp))]:
            lines.append((x0 - p.edge_extend_m, x1 + p.edge_extend_m, y))

    order = line_order(len(lines), spacing, 2 * r_turn)
    survey_xy = []
    for j, li in enumerate(order):
        x0, x1, yy = lines[li]
        a, b = (x0, x1) if j % 2 == 0 else (x1, x0)
        survey_xy += [_rot(a, yy, ang), _rot(b, yy, ang)]

    # landing: approach start is approach_len behind the touchdown point
    land_ll = p.landing or p.launch
    lx, ly = loc.to_xy(*land_ll)
    hd = math.radians(p.landing_heading_deg)        # compass heading: 0 = north
    ax, ay = lx - p.approach_len_m * math.sin(hd), ly - p.approach_len_m * math.cos(hd)

    items = [mm.Item(mm.NAV_WAYPOINT, p.launch[0], p.launch[1], 0.0)]           # home
    items.append(mm.Item(mm.NAV_TAKEOFF, p.launch[0], p.launch[1], p.takeoff_alt_m,
                         params=[p.takeoff_pitch_deg, 0, 0, 0]))
    items.append(mm.Item(mm.DO_CHANGE_SPEED, params=[0, p.speed_mps, -1, 0]))     # 0 = airspeed
    for x, yv in survey_xy:
        la, lo = loc.to_ll(x, yv)
        items.append(mm.Item(mm.NAV_WAYPOINT, la, lo, p.alt_m))
    items.append(mm.Item(mm.DO_LAND_START, *land_ll, 0.0))
    la, lo = loc.to_ll(ax, ay)
    items.append(mm.Item(mm.NAV_WAYPOINT, la, lo, p.approach_alt_m))
    items.append(mm.Item(mm.NAV_LAND, land_ll[0], land_ll[1], 0.0))

    # ---- statistics
    path = [(0.0, 0.0)] + survey_xy + [(ax, ay), (lx, ly)]
    dist = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(path, path[1:]))
    n_turns = max(0, len(order) - 1)
    dist += n_turns * math.pi * r_turn * 0.5          # rough allowance for turn arcs
    t_min = dist / p.speed_mps / 60.0 + 3.0           # + climb-out and landing pattern
    usable_min = p.endurance_min * (1.0 - p.reserve_frac)
    batt_frac = t_min / p.endurance_min
    far = max(math.hypot(x, yv) for x, yv in path)
    fence_needed = math.ceil((far + 100.0) / 50.0) * 50.0

    if t_min > usable_min:
        warnings.append(f"estimated flight {t_min:.0f} min exceeds the usable {usable_min:.0f} min "
                        f"({p.endurance_min:.0f} min endurance is a design estimate)")
    if p.alt_m > p.alt_limit_m:
        warnings.append(f"survey altitude {p.alt_m:.0f} m is above the {p.alt_limit_m:.0f} m limit")
    if fence_needed > p.fence_radius_m:
        warnings.append(f"mission reaches {far:.0f} m from home: less than 100 m margin to the "
                        f"{p.fence_radius_m:.0f} m fence (wind, turns). Set FENCE_RADIUS to "
                        f"{fence_needed:.0f} m - tick 'Also set FENCE_RADIUS' when uploading")
    if far > 500:
        warnings.append(f"the aircraft goes {far:.0f} m from the launch point: check that this "
                        f"is allowed for your operation (visual line of sight, permissions)")
    if spacing < 2 * r_turn:
        warnings.append(f"line spacing {spacing:.0f} m < turn diameter {2 * r_turn:.0f} m: lines "
                        f"are flown in an interleaved order")
    glide = math.degrees(math.atan2(p.approach_alt_m, p.approach_len_m))
    if glide > 8:
        warnings.append(f"final approach glide slope {glide:.1f} deg is steep: lengthen the "
                        f"approach or lower its start")

    det = None
    if p.target_size_m:
        px = cam.pixels_at_model(p.target_size_m, p.alt_m)
        t_in = cam.along_m(p.alt_m) / p.speed_mps
        det = {"target_size_m": p.target_size_m, "px_at_model": round(px, 1),
               "verdict": "good" if px >= 10 else ("marginal" if px >= 5 else "too small"),
               "max_alt_for_10px": round(cam.max_alt_for(p.target_size_m), 0),
               "seconds_in_region": round(t_in, 1),
               "fps_for_2_sightings": round(2.0 / t_in, 2)}
        if px < 10:
            warnings.append(f"target is only {px:.1f} px at the model input at {p.alt_m:.0f} m: "
                            f"fly at or below {det['max_alt_for_10px']:.0f} m for ~10 px")

    errors, w2 = mm.check_mission(items, p.fence_radius_m, p.alt_limit_m)
    warnings += w2
    return {
        "items": items,
        "swath_m": round(swath, 1), "spacing_m": round(spacing, 1),
        "turn_radius_m": round(r_turn, 1), "lines": len(lines),
        "line_angle_deg": round(math.degrees(ang) % 180, 1),
        "distance_km": round(dist / 1000.0, 2), "flight_min": round(t_min, 1),
        "battery_frac": round(batt_frac, 2), "max_dist_m": round(far, 0),
        "fence_needed_m": fence_needed, "glide_deg": round(glide, 1),
        "detection": det, "warnings": warnings, "errors": errors,
        "approach_start": loc.to_ll(ax, ay),
    }


def plan_from_dict(d: dict) -> dict:
    cam = Camera(**{k: (tuple(v) if k == "roi" else v) for k, v in (d.get("camera") or {}).items()})
    kw = {k: v for k, v in d.items() if k != "camera"}
    for k in ("launch", "landing"):
        if kw.get(k) is not None:
            kw[k] = tuple(kw[k])
    kw["area"] = [tuple(pt) for pt in kw.get("area", [])]
    return plan(PlanInput(camera=cam, **kw))


def defaults_dict() -> dict:
    d = asdict(PlanInput(launch=(0, 0), area=[]))
    d.pop("launch"), d.pop("area")
    return d
