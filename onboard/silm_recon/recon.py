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

import json
import os
import signal
import time

from .camera import Camera
from .detector import ObjectDetector
from .failsafe import GpsHealthMonitor, OpticalFlowAid
from .fire import FireDetector
from .georef import GeoReferencer
from .store import DetectionStore
from .telemetry import TelemetryLink
from .tiles import OfflineBasemap


class Recon:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self._stop = False

        # GPS-health monitor: logs degradation / suspected jamming. The real
        # GPS-loss failsafe is in ArduPilot (ardupilot/gps_denied_failsafe.param).
        self.gps_monitor = GpsHealthMonitor(log_dir=None)  # log_dir set after store

        tcfg = cfg["telemetry"]
        self.link = TelemetryLink(
            connection=tcfg["connection"],
            baud=int(tcfg.get("baud", 115200)),
            source_system=int(tcfg.get("source_system", 200)),
            msg_callback=self.gps_monitor.ingest,
        )

        self.camera = Camera(cfg["camera"])

        dcfg = cfg["detector"]
        self.detector = None
        if dcfg.get("enabled", True):
            self.detector = ObjectDetector(
                model_path=dcfg["model"],
                labels_path=dcfg["labels"],
                score_threshold=float(dcfg.get("score_threshold", 0.5)),
                fmt=dcfg.get("format", "auto"),
                iou_threshold=float(dcfg.get("iou_threshold", 0.45)),
            )
        self.targets = set(cfg.get("targets", []))
        self._tiled = bool(dcfg.get("tiled", False))
        g = dcfg.get("tile_grid", [2, 2])
        self._tile_grid = (int(g[0]), int(g[1]))

        self.fire = FireDetector(cfg.get("fire", {}))
        self.geo = GeoReferencer(cfg.get("georef", {}))
        self.store = DetectionStore(cfg.get("storage", {}))
        self.basemap = OfflineBasemap(cfg.get("tiles", {}))

        # Now that the mission folder exists, log GPS-health events into it.
        self.gps_monitor._log_path = os.path.join(self.store.dir, "gps_health.ndjson")

        # Optical-flow velocity aid (backup reference; logs only unless the
        # config opts in to sending it to the flight controller).
        self.flow = OpticalFlowAid(
            cfg.get("failsafe", {}),
            hfov_deg=float(cfg.get("georef", {}).get("hfov_deg", 86.0)),
        )
        self._flow_log = open(os.path.join(self.store.dir, "optical_flow.ndjson"), "a", buffering=1)

        # Optional: periodically save raw frames for later labelling/retraining.
        scfg = cfg.get("storage", {})
        self._save_all = bool(scfg.get("save_all_frames", False))
        self._frame_interval = float(scfg.get("frame_interval_s", 2.0))
        self._frames_dir = os.path.join(self.store.dir, "frames")
        self._last_frame_save = 0.0
        if self._save_all:
            os.makedirs(self._frames_dir, exist_ok=True)

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
                              f"alt={st.rel_alt:.0f}m fix={st.have_fix} "
                              f"gps={self.gps_monitor.state}")
                        last_status = now
                    time.sleep(0.2)
                    continue

                frame = self.camera.read()
                if frame is None:
                    time.sleep(0.02)
                    continue

                self._frame_no += 1

                # Optionally archive raw frames (rate-limited) for retraining.
                if self._save_all:
                    import cv2
                    now_f = time.time()
                    if now_f - self._last_frame_save >= self._frame_interval:
                        fp = os.path.join(
                            self._frames_dir,
                            f"frame_{int(now_f)}_{self._frame_no}.jpg")
                        cv2.imwrite(fp, frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
                        self._last_frame_save = now_f

                # Optical-flow velocity aid runs every frame (rate-limited
                # internally). Logs always; forwards to the FC only if opted in.
                fe = self.flow.update(frame, st.rel_alt, st.roll, st.pitch, st.yaw)
                if fe.valid:
                    self.flow.send(self.link.master, fe)
                    self._flow_log.write(json.dumps({
                        "t": round(fe.t, 3), "vn": round(fe.vx_ned, 2),
                        "ve": round(fe.vy_ned, 2), "q": round(fe.quality, 2),
                        "gps": self.gps_monitor.state,
                    }) + "\n")

                if self._frame_no % self.detect_every != 0:
                    continue

                events = []  # list of {label, score, box, ground}

                if self.detector is not None:
                    dets = (self.detector.detect_tiled(frame, grid=self._tile_grid)
                            if self._tiled else self.detector.detect(frame))
                    for d in dets:
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
        try:
            self._flow_log.close()
        except Exception:
            pass
