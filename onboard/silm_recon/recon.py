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
from .confirm import ConfirmationFilter
from .detector import ObjectDetector
from .failsafe import GpsHealthMonitor, OpticalFlowAid
from .fire import FireDetector
from .georef import GeoReferencer
from .health import HealthMonitor
from .siyi import SiyiGimbal
from .store import DetectionStore
from .telemetry import TelemetryLink
from .tiles import OfflineBasemap


class Recon:
    def __init__(self, cfg: dict, link=None, camera=None):
        """`link` and `camera` can be injected (tests, desk simulation);
        by default they are built from the config."""
        self.cfg = cfg
        self._stop = False

        # GPS-health monitor: logs degradation / suspected jamming. The real
        # GPS-loss failsafe is in ArduPilot (ardupilot/gps_denied_failsafe.param).
        self.gps_monitor = GpsHealthMonitor(log_dir=None)  # log_dir set after store

        if link is None:
            tcfg = cfg["telemetry"]
            link = TelemetryLink(
                connection=tcfg["connection"],
                baud=int(tcfg.get("baud", 115200)),
                source_system=int(tcfg.get("source_system", 200)),
                msg_callback=self.gps_monitor.ingest,
            )
        self.link = link

        self.camera = camera if camera is not None else Camera(cfg["camera"])

        dcfg = cfg["detector"]
        self.detector = None
        if dcfg.get("enabled", True):
            self.detector = ObjectDetector(
                model_path=dcfg["model"],
                labels_path=dcfg["labels"],
                score_threshold=float(dcfg.get("score_threshold", 0.5)),
                fmt=dcfg.get("format", "auto"),
                iou_threshold=float(dcfg.get("iou_threshold", 0.45)),
                class_thresholds=dcfg.get("class_thresholds"),
            )
        self.targets = set(cfg.get("targets", []))
        self._tiled = bool(dcfg.get("tiled", False))
        g = dcfg.get("tile_grid", [2, 2])
        self._tile_grid = (int(g[0]), int(g[1]))
        roi = dcfg.get("roi")
        self._roi = tuple(float(v) for v in roi) if roi else None

        # Camera latency: the frame was captured this long before it arrived,
        # so the aircraft state is looked up for (arrival - latency).
        self._latency_s = float(cfg.get("camera", {}).get("latency_ms", 150)) / 1000.0

        # SIYI pitch gimbal: point it down and use its reported angle.
        gcfg = cfg.get("gimbal", {}) or {}
        self.gimbal = SiyiGimbal(gcfg) if gcfg.get("enabled", False) else None
        # "earth": the gimbal holds its angle relative to the horizon (it
        # cancels the aircraft's pitch); "body": relative to the airframe.
        self._gimbal_earth = gcfg.get("angle_frame", "earth") == "earth"
        self._gimbal_warned = False

        self.fire = FireDetector(cfg.get("fire", {}))
        self.geo = GeoReferencer(cfg.get("georef", {}),
                                 image_size=self.camera.frame_size)
        self.store = DetectionStore(cfg.get("storage", {}))
        self.basemap = OfflineBasemap(cfg.get("tiles", {}))

        # Now that the mission folder exists, log GPS-health events into it.
        self.gps_monitor._log_path = os.path.join(self.store.dir, "gps_health.ndjson")

        # Multi-frame confirmation and companion health / storage monitoring.
        self.confirm = ConfirmationFilter(cfg.get("confirm", {}))
        self.health = HealthMonitor(cfg.get("health", {}), storage_dir=self.store.dir,
                                    log_dir=self.store.dir)

        # Optical-flow velocity aid (backup reference; logs only unless the
        # config opts in to sending it to the flight controller).
        self.flow = OpticalFlowAid(
            cfg.get("failsafe", {}),
            fx_over_width=self.geo.fx / self.geo.W,
        )
        self._flow_log = open(os.path.join(self.store.dir, "optical_flow.ndjson"), "a", buffering=1)
        # 1 Hz aircraft track for the post-flight review page.
        self._track_log = open(os.path.join(self.store.dir, "track.ndjson"), "a", buffering=1)
        self._last_track = 0.0

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
        self._started = time.time()

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

    def _georef_box(self, box, st, cam_tilt_deg=None):
        cx = 0.5 * (box[0] + box[2])
        cy = 0.5 * (box[1] + box[3])
        pitch = 0.0 if (cam_tilt_deg is not None and self._gimbal_earth) else st.pitch
        return self.geo.project(cx, cy, st.lat, st.lon, st.rel_alt, st.roll, pitch, st.yaw,
                                cam_tilt_deg=cam_tilt_deg)

    def camera_tilt(self, now: float | None = None):
        """Camera tilt from the gimbal (90 = straight down), or None to use
        the fixed georef.forward_tilt_deg."""
        if self.gimbal is None:
            return None
        att = self.gimbal.attitude()
        if att is None:
            now = time.time() if now is None else now
            if not self._gimbal_warned and now - self._started > 10:
                print("[recon] WARNING: gimbal not answering - using the fixed "
                      "georef.forward_tilt_deg for geo-referencing")
                self._gimbal_warned = True
            return None
        return -att.pitch

    def run(self) -> None:
        signal.signal(signal.SIGINT, self._on_signal)
        signal.signal(signal.SIGTERM, self._on_signal)

        self.link.start()
        if self.gimbal is not None:
            self.gimbal.start()
        print("[recon] started; waiting for telemetry + active flight state")
        last_status = 0.0

        try:
            while not self._stop:
                st = self.link.state.snapshot()
                self.log_track(st)

                if not self._active(st):
                    # Idle: don't burn the Pi 3's CPU on inference pre-launch.
                    self.health.maybe_sample()
                    now = time.time()
                    if now - last_status > 5.0:
                        print(f"[recon] idle mode={st.mode} armed={st.armed} "
                              f"alt={st.rel_alt:.0f}m fix={st.have_fix} "
                              f"gps={self.gps_monitor.state}")
                        last_status = now
                    time.sleep(0.2)
                    continue

                frame, t_arrived = self.camera.read_with_time()
                if frame is None:
                    time.sleep(0.01)
                    continue
                # aircraft state at the moment the frame was captured
                st_frame = (self.link.state_at(t_arrived - self._latency_s)
                            if hasattr(self.link, "state_at") else st)
                self.process_frame(frame, st_frame, cam_tilt_deg=self.camera_tilt())
                self.health.maybe_sample()
        finally:
            self._shutdown()

    def log_track(self, st, now: float | None = None) -> None:
        now = time.time() if now is None else now
        if not st.have_fix or now - self._last_track < 1.0:
            return
        self._last_track = now
        self._track_log.write(json.dumps({
            "t": round(now, 1), "lat": round(st.lat, 7), "lon": round(st.lon, 7),
            "alt": round(st.rel_alt, 1), "mode": st.mode, "armed": st.armed,
            "gps": self.gps_monitor.state,
        }) + "\n")

    def process_frame(self, frame, st, now: float | None = None,
                      cam_tilt_deg: float | None = None) -> list:
        """Everything done with one camera frame while active. Returns the
        records saved for this frame (empty list if nothing was saved)."""
        now = time.time() if now is None else now
        self._frame_no += 1
        self.health.frame_done()

        # Optionally archive raw frames (rate-limited) for retraining; shed
        # this first when storage runs low.
        if self._save_all and not self.health.low_disk:
            if now - self._last_frame_save >= self._frame_interval:
                import cv2
                fp = os.path.join(self._frames_dir, f"frame_{int(now)}_{self._frame_no}.jpg")
                cv2.imwrite(fp, frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
                self._last_frame_save = now

        # Optical-flow velocity aid (rate-limited internally). Logs always;
        # forwards to the FC only if opted in.
        flow_pitch = 0.0 if (cam_tilt_deg is not None and self._gimbal_earth) else st.pitch
        fe = self.flow.update(frame, st.rel_alt, st.roll, flow_pitch, st.yaw, now=now)
        if fe.valid:
            self.flow.send(getattr(self.link, "master", None), fe)
            self._flow_log.write(json.dumps({
                "t": round(fe.t, 3), "vn": round(fe.vx_ned, 2),
                "ve": round(fe.vy_ned, 2), "q": round(fe.quality, 2),
                "gps": self.gps_monitor.state,
            }) + "\n")

        if self._frame_no % self.detect_every != 0:
            return []

        events = []  # list of {label, score, box, ground}
        if self.detector is not None:
            dets = self.detector.detect_roi(frame, self._roi,
                                            self._tile_grid if self._tiled else (1, 1))
            for d in dets:
                if self.targets and d.label not in self.targets:
                    continue
                events.append({"label": d.label, "score": d.score, "box": d.box,
                               "ground": self._georef_box(d.box, st, cam_tilt_deg)})
        for fh in self.fire.detect(frame):
            events.append({"label": fh.label, "score": fh.score, "box": fh.box,
                           "ground": self._georef_box(fh.box, st, cam_tilt_deg)})

        # Only objects seen in several frames at the same place go further.
        events = self.confirm.update(events, now=now)
        if not events or self.health.critical_disk:
            return []

        # De-dup: keep only events whose class isn't a recent nearby save.
        keep = []
        for ev in events:
            g = ev["ground"]
            lat = g.lat if (g and g.valid) else None
            lon = g.lon if (g and g.valid) else None
            if not self.store.is_duplicate(ev["label"], lat, lon):
                keep.append(ev)
        if not keep:
            return []

        rec = self.store.save(frame, keep, st, None)
        labels = ", ".join(sorted({e["label"] for e in keep}))
        gp = keep[0]["ground"]
        where = (f"{gp.lat:.6f},{gp.lon:.6f}" if gp and gp.valid
                 else f"ac {st.lat:.6f},{st.lon:.6f}")
        print(f"[recon] SAVED #{rec.seq} [{labels}] @ {where} "
              f"alt={st.rel_alt:.0f}m -> {rec.path_jpg}")
        return [rec]

    # ------------------------------------------------------------------ #

    def _on_signal(self, *_):
        self._stop = True

    def _shutdown(self) -> None:
        print("[recon] shutting down")
        if self.link is not None:
            self.link.stop()
        if self.gimbal is not None:
            self.gimbal.stop()
        self.camera.release()
        self.store.close()
        self.basemap.close()
        for fh in (self._flow_log, self._track_log):
            try:
                fh.close()
            except Exception:
                pass
