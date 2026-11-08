"""Camera capture.

Frame sources:

  * ``rtsp``     - an IP camera's video stream over Ethernet. This is how the
                   SIYI A2 mini delivers video: plug its Ethernet into the Pi's
                   Ethernet port, give the Pi an address on the camera's subnet
                   (camera default 192.168.144.25), and read
                   rtsp://192.168.144.25:8554/main.264. No capture hardware.
                   Decoding: ``gstreamer`` uses the Pi's hardware H.264 decoder
                   (v4l2h264dec): on the Pi 4 that leaves the CPU free for
                   the detector - software-decoding 1080p would take one to
                   two of its four cores. ``ffmpeg`` is the fallback
                   (software decode).
  * ``usb``      - any UVC/V4L2 device via OpenCV.
  * ``picamera`` - a Raspberry Pi CSI camera via picamera2.
  * ``file``     - a video file or image folder, for bench testing.

Live sources (rtsp, usb) are read on a background thread that keeps only the
NEWEST frame and the time it arrived. Without this, a slow detector would
process frames that are seconds old while pairing them with the aircraft's
CURRENT position and attitude, putting every geotag in the wrong place. The
arrival time lets the main loop look up the aircraft state for that moment.
"""

from __future__ import annotations

import glob
import os
import threading
import time
from typing import Optional

import numpy as np

DEFAULT_RTSP = "rtsp://192.168.144.25:8554/main.264"


class CameraError(RuntimeError):
    pass


def gstreamer_pipeline(url: str, decoder: str = "v4l2h264dec", latency_ms: int = 0) -> str:
    """Low-latency RTSP -> BGR appsink pipeline. `decoder` = v4l2h264dec
    (Pi 3/4 hardware) or avdec_h264 (software, any machine)."""
    return (f"rtspsrc location={url} latency={latency_ms} drop-on-latency=true ! "
            f"rtph264depay ! h264parse ! {decoder} ! videoconvert ! "
            f"video/x-raw,format=BGR ! appsink drop=true max-buffers=1 sync=false")


class _LatestFrameReader:
    """Continuously reads an OpenCV capture, keeping only the newest frame."""

    def __init__(self, open_fn, name="camera"):
        self._open_fn = open_fn
        self._cap = None
        self._lock = threading.Lock()
        self._frame = None
        self._t = 0.0
        self._seq = 0
        self._stop = threading.Event()
        self.frames_read = 0
        self.reconnects = 0
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)
        self._thread.start()

    def _run(self):
        backoff = 1.0
        while not self._stop.is_set():
            if self._cap is None:
                try:
                    cap = self._open_fn()
                    if cap is None or not cap.isOpened():
                        raise CameraError("could not open")
                    self._cap = cap
                    backoff = 1.0
                except Exception:
                    self._stop.wait(backoff)
                    backoff = min(backoff * 2, 10.0)
                    self.reconnects += 1
                    continue
            ok, frame = self._cap.read()
            if not ok or frame is None:
                # stream dropped (camera rebooted, cable): reopen
                try:
                    self._cap.release()
                except Exception:
                    pass
                self._cap = None
                continue
            with self._lock:
                self._frame, self._t = frame, time.time()
                self._seq += 1
            self.frames_read += 1

    def latest(self, after_seq: int):
        """(frame, t, seq) if a frame newer than after_seq exists, else None."""
        with self._lock:
            if self._frame is None or self._seq == after_seq:
                return None
            return self._frame, self._t, self._seq

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=3)
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass


class Camera:
    def __init__(self, cfg: dict, strict: bool = True):
        """`strict=False` (the flight program): a live camera that sends no
        frame within open_timeout_s is NOT an error - the background reader
        keeps reconnecting, and frames appear whenever the camera comes up
        (a camera still booting at power-on, a loose cable re-seated).
        `strict=True` (preflight, self-test) raises CameraError instead."""
        self.strict = strict
        self.source = cfg.get("source", "rtsp")
        self.width = int(cfg.get("width", 1920))
        self.height = int(cfg.get("height", 1080))
        self.device = cfg.get("device", 0)
        self.fps = int(cfg.get("fps", 30))
        self.url = cfg.get("url", DEFAULT_RTSP)
        self.backend = cfg.get("backend", "auto")          # auto | gstreamer | ffmpeg
        self.decoder = cfg.get("decoder", "v4l2h264dec")
        self.pipeline = cfg.get("pipeline")                 # full custom GStreamer pipeline
        self.open_timeout_s = float(cfg.get("open_timeout_s", 30))
        self._impl = None
        self._reader: _LatestFrameReader | None = None
        self._last_seq = 0
        self.last_frame_time = 0.0
        self._files: list[str] = []
        self._idx = 0
        self._open()

    # ------------------------------------------------------------------ #

    def _rtsp_open_fn(self):
        import cv2

        def gst():
            pipe = self.pipeline or gstreamer_pipeline(self.url, self.decoder)
            return cv2.VideoCapture(pipe, cv2.CAP_GSTREAMER)

        def ffmpeg():
            os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS",
                                  "rtsp_transport;udp|fflags;nobuffer|flags;low_delay")
            cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)
            try:
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            except Exception:
                pass
            return cap

        if self.backend == "gstreamer":
            return gst
        if self.backend == "ffmpeg":
            return ffmpeg

        def auto():
            cap = gst()
            if cap is not None and cap.isOpened():
                return cap
            return ffmpeg()
        return auto

    def _usb_open_fn(self):
        import cv2

        def open_usb():
            cap = cv2.VideoCapture(self.device)
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
            cap.set(cv2.CAP_PROP_FPS, self.fps)
            try:
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            except Exception:
                pass
            return cap
        return open_usb

    def _open(self) -> None:
        if self.source in ("rtsp", "usb"):
            fn = self._rtsp_open_fn() if self.source == "rtsp" else self._usb_open_fn()
            self._reader = _LatestFrameReader(fn, name=f"camera-{self.source}")
            # Learn the real frame size from the first frame (an RTSP stream's
            # size is set by the camera, not by us).
            t0 = time.time()
            while time.time() - t0 < self.open_timeout_s:
                got = self._reader.latest(0)
                if got is not None:
                    h, w = got[0].shape[:2]
                    if (w, h) != (self.width, self.height):
                        print(f"[camera] stream is {w}x{h} (config said {self.width}x{self.height})")
                    self.width, self.height = w, h
                    return
                time.sleep(0.05)
            where = self.url if self.source == "rtsp" else self.device
            if not self.strict:
                print(f"[camera] WARNING: no frames from {self.source} source {where!r} yet "
                      f"after {self.open_timeout_s:.0f} s - still trying in the background "
                      f"(assuming {self.width}x{self.height} until the first frame)")
                return
            self._reader.stop()
            raise CameraError(f"no frames from {self.source} source {where!r} within "
                              f"{self.open_timeout_s:.0f} s")

        if self.source == "file":
            import cv2
            self._cv2 = cv2
            if isinstance(self.device, str) and os.path.isdir(self.device):
                self._files = sorted(glob.glob(os.path.join(self.device, "*.jpg"))
                                     + glob.glob(os.path.join(self.device, "*.png")))
                if not self._files:
                    raise CameraError(f"no images in folder {self.device}")
                first = cv2.imread(self._files[0])
                if first is not None:
                    self.height, self.width = first.shape[:2]
                return
            cap = cv2.VideoCapture(self.device)
            if not cap.isOpened():
                raise CameraError(f"could not open video file {self.device!r}")
            self.width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or self.width)
            self.height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or self.height)
            self._impl = cap
            return

        if self.source == "picamera":
            from picamera2 import Picamera2  # type: ignore
            picam = Picamera2()
            picam.configure(picam.create_video_configuration(
                main={"size": (self.width, self.height), "format": "RGB888"}))
            picam.start()
            time.sleep(0.5)
            self._impl = picam
            return

        raise CameraError(f"unknown camera source {self.source!r}")

    # ------------------------------------------------------------------ #

    def read_with_time(self) -> tuple[Optional[np.ndarray], float]:
        """(newest frame, time it arrived), or (None, 0) if no new frame yet."""
        if self._reader is not None:
            got = self._reader.latest(self._last_seq)
            if got is None:
                return None, 0.0
            frame, t, self._last_seq = got
            self.last_frame_time = t
            return frame, t
        if self._files:
            path = self._files[self._idx % len(self._files)]
            self._idx += 1
            return self._cv2.imread(path), time.time()
        if self.source == "file":
            ok, frame = self._impl.read()
            return (frame if ok else None), time.time()
        if self.source == "picamera":
            rgb = self._impl.capture_array()
            return rgb[:, :, ::-1].copy(), time.time()
        return None, 0.0

    def read(self) -> Optional[np.ndarray]:
        return self.read_with_time()[0]

    @property
    def frame_size(self) -> tuple[int, int]:
        return self.width, self.height

    @property
    def stats(self) -> dict:
        r = self._reader
        return {} if r is None else {"frames_read": r.frames_read, "reconnects": r.reconnects}

    def seconds_since_frame(self, now: float | None = None) -> float | None:
        """Age of the newest frame from a live camera (None if none yet, or
        not a live source)."""
        r = self._reader
        if r is None:
            return None
        with r._lock:
            t = r._t if r._frame is not None else None
        if t is None:
            return None
        return (time.time() if now is None else now) - t

    def release(self) -> None:
        try:
            if self._reader is not None:
                self._reader.stop()
            elif self.source == "file" and self._impl is not None:
                self._impl.release()
            elif self.source == "picamera" and self._impl is not None:
                self._impl.stop()
        except Exception:
            pass
