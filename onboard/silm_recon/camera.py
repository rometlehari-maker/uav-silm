"""Camera capture.

Supports three frame sources so the current airframe does not have to change
its camera:

  * ``usb``      - any UVC/V4L2 device via OpenCV. This is how the SIYI A2 Mini
                   reaches the Pi: its AHD analog output goes through a small
                   AHD->USB capture dongle, which shows up as a normal
                   /dev/videoN UVC device. (This dongle is the one item the
                   current build may still need.)
  * ``picamera`` - a Raspberry Pi CSI camera via picamera2.
  * ``file``     - a video file or image folder, for bench testing on a laptop.

All sources return BGR numpy frames, so the rest of the pipeline is identical.
"""

from __future__ import annotations

import glob
import os
import time
from typing import Optional

import numpy as np


class CameraError(RuntimeError):
    pass


class Camera:
    def __init__(self, cfg: dict):
        self.source = cfg.get("source", "usb")
        self.width = int(cfg.get("width", 1280))
        self.height = int(cfg.get("height", 720))
        self.device = cfg.get("device", 0)
        self.fps = int(cfg.get("fps", 10))
        self._impl = None
        self._files: list[str] = []
        self._idx = 0
        self._open()

    # ------------------------------------------------------------------ #

    def _open(self) -> None:
        if self.source in ("usb", "file"):
            import cv2  # local import so picamera-only installs still load

            self._cv2 = cv2
            if self.source == "file" and isinstance(self.device, str) and os.path.isdir(self.device):
                self._files = sorted(
                    glob.glob(os.path.join(self.device, "*.jpg"))
                    + glob.glob(os.path.join(self.device, "*.png"))
                )
                if not self._files:
                    raise CameraError(f"no images in folder {self.device}")
                first = cv2.imread(self._files[0])
                if first is not None:
                    self.height, self.width = first.shape[:2]
                return

            cap = cv2.VideoCapture(self.device)
            if self.source == "usb":
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
                cap.set(cv2.CAP_PROP_FPS, self.fps)
                # Keep the queue shallow so we always read a fresh frame.
                try:
                    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                except Exception:
                    pass
            if not cap.isOpened():
                raise CameraError(f"could not open video source {self.device!r}")
            # Capture devices may not honour the requested size (AHD dongles
            # often deliver 720x576 or 1920x1080). Use what was negotiated, so
            # geo-referencing matches the real frames.
            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
            if w > 0 and h > 0:
                if (w, h) != (self.width, self.height):
                    print(f"[camera] requested {self.width}x{self.height}, device gives {w}x{h}")
                self.width, self.height = w, h
            self._impl = cap

        elif self.source == "picamera":
            from picamera2 import Picamera2  # type: ignore

            picam = Picamera2()
            config = picam.create_video_configuration(
                main={"size": (self.width, self.height), "format": "RGB888"}
            )
            picam.configure(config)
            picam.start()
            time.sleep(0.5)
            self._impl = picam
        else:
            raise CameraError(f"unknown camera source {self.source!r}")

    # ------------------------------------------------------------------ #

    def read(self) -> Optional[np.ndarray]:
        """Return a fresh BGR frame, or None on a transient read failure."""
        if self._files:
            path = self._files[self._idx % len(self._files)]
            self._idx += 1
            frame = self._cv2.imread(path)
            return frame

        if self.source in ("usb", "file"):
            ok, frame = self._impl.read()
            if not ok:
                return None
            return frame

        if self.source == "picamera":
            rgb = self._impl.capture_array()
            # picamera2 gives RGB; convert to BGR to match OpenCV pipeline.
            return rgb[:, :, ::-1].copy()

        return None

    @property
    def frame_size(self) -> tuple[int, int]:
        return self.width, self.height

    def release(self) -> None:
        try:
            if self.source in ("usb", "file") and self._impl is not None:
                self._impl.release()
            elif self.source == "picamera" and self._impl is not None:
                self._impl.stop()
        except Exception:
            pass
