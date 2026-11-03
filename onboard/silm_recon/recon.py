"""Main reconnaissance loop.

Ties telemetry + camera + detector + fire + geo-ref + storage together into the
link-free onboard behaviour:

  1. On boot (systemd), connect to the flight controller and open the camera.
  2. Wait until the aircraft is ARMED, in an active flight mode (default AUTO),
     and above a minimum altitude -- so nothing is logged on the bench or during
     the hand-launch run-up.
  3. While active, grab frames, run the object detector and the fire heuristic,
     keep only the configured target classes, geo-reference each hit, de-dup,
     and save a boxed JPEG + metadata to the SD card.
  4. When the pilot takes over on ExpressLRS (mode leaves the active set) the
     loop simply stops saving; nothing else changes, so manual control is
     unaffected. All captures are already on the card for post-flight review.

Nothing here ever commands the aircraft.
"""

from __future__ import annotations

import signal
import time

from .camera import Camera
from .detector import ObjectDetector
from .fire import FireDetector
from .georef import GeoReferencer
from .store import DetectionStore
from .telemetry import TelemetryLink
from .tiles import OfflineBasemap


class Recon:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self._stop = False

        tcfg = cfg["telemetry"]
        self.link = TelemetryLink(
            connection=tcfg["connection"],
            baud=int(tcfg.get("baud", 115200)),
            source_system=int(tcfg.get("source_system", 200)),
        )

        self.camera = Camera(cfg["camera"])

        dcfg = cfg["detector"]
        self.detector = None
        if dcfg.get("enabled", True):
            self.detector = ObjectDetector(
                model_path=dcfg["model"],
                labels_path=dcfg["labels"],
                score_threshold=float(dcfg.get("score_threshold", 0.5)),
            )
        self.targets = set(cfg.get("targets", []))

        self.fire = FireDetector(cfg.get("fire", {}))
        self.geo = GeoReferencer(cfg.get("georef", {}))
        self.store = DetectionStore(cfg.get("storage", {}))
        self.basemap = OfflineBasemap(cfg.get("tiles", {}))

        acfg = cfg.get("activation", {})
        self.active_modes = set(m.upper() for m in acfg.get("active_modes", ["AUTO", "GUIDED"]))
        self.min_alt = float(acfg.get("min_alt_m", 20.0))
        self.require_armed = bool(acfg.get("require_armed", True))
        self.always_on = bool(acfg.get("always_on", False))
        self.detect_every = max(1, int(acfg.get("detect_every_n_frames", 1)))
        self.max_telemetry_age = float(acfg.get("max_telemetry_age_s", 2.0))

        self._frame_no = 0

    # ------------------------------------------------------------------ #

    def _active(self, st) -> bool:
        if self.always_on:
            return True
        if self.require_armed and not st.armed:
            return False
        if st.mode.upper() not in self.active_modes:
            return False
        if st.rel_alt < self.min_alt:
            return False
        return st.telemetry_ok(self.max_telemetry_age)

    def _georef_box(self, box, st):
        cx = 0.5 * (box[0] + box[2])
        cy = 0.5 * (box[1] + box[3])
        return self.geo.project(
            cx, cy, st.lat, st.lon, st.rel_alt, st.roll, st.pitch, st.yaw
        )

    def run(self) -> None:
        signal.signal(signal.SIGINT, self._on_signal)
        signal.signal(signal.SIGTERM, self._on_signal)

        self.link.start()
        print("[recon] started; waiting for telemetry + active flight state")
        last_status = 0.0

        try:
            while not self._stop:
                st = self.link.state.snapshot()

                if not self._active(st):
                    # Idle: don't burn the Pi 3's CPU on inference pre-launch.
                    now = time.time()
                    if now - last_status > 5.0:
                        print(f"[recon] idle mode={st.mode} armed={st.armed} "
                              f"alt={st.rel_alt:.0f}m fix={st.have_fix}")
                        last_status = now
                    time.sleep(0.2)
                    continue

                frame = self.camera.read()
                if frame is None:
                    time.sleep(0.02)
                    continue

                self._frame_no += 1
                if self._frame_no % self.detect_every != 0:
                    continue

                events = []  # list of {label, score, box, ground}

                if self.detector is not None:
                    for d in self.detector.detect(frame):
                        if self.targets and d.label not in self.targets:
                            continue
                        events.append({
                            "label": d.label,
                            "score": d.score,
                            "box": d.box,
                            "ground": self._georef_box(d.box, st),
                        })

                for fh in self.fire.detect(frame):
                    events.append({
                        "label": fh.label,
                        "score": fh.score,
                        "box": fh.box,
                        "ground": self._georef_box(fh.box, st),
                    })

                if not events:
                    continue

                # De-dup: keep only events whose class isn't a recent nearby save.
                keep = []
                for ev in events:
                    g = ev["ground"]
                    lat = g.lat if (g and g.valid) else None
                    lon = g.lon if (g and g.valid) else None
                    if not self.store.is_duplicate(ev["label"], lat, lon):
                        keep.append(ev)

                if keep:
                    rec = self.store.save(frame, keep, st, None)
                    labels = ", ".join(sorted({e["label"] for e in keep}))
                    gp = keep[0]["ground"]
                    where = (f"{gp.lat:.6f},{gp.lon:.6f}" if gp and gp.valid
                             else f"ac {st.lat:.6f},{st.lon:.6f}")
                    print(f"[recon] SAVED #{rec.seq} [{labels}] @ {where} "
                          f"alt={st.rel_alt:.0f}m -> {rec.path_jpg}")
        finally:
            self._shutdown()

    # ------------------------------------------------------------------ #

    def _on_signal(self, *_):
        self._stop = True

    def _shutdown(self) -> None:
        print("[recon] shutting down")
        self.link.stop()
        self.camera.release()
        self.store.close()
        self.basemap.close()
