"""The live-camera reader must hand out only the NEWEST frame (never a backlog),
with its arrival time, and reconnect when the stream drops."""

import time

import numpy as np

from silm_recon.camera import _LatestFrameReader, gstreamer_pipeline


class FakeCap:
    def __init__(self, fail_after=None, period=0.002):
        self.n = 0
        self.fail_after = fail_after
        self.period = period
        self.released = False

    def isOpened(self):
        return True

    def read(self):
        time.sleep(self.period)
        self.n += 1
        if self.fail_after is not None and self.n > self.fail_after:
            return False, None
        f = np.zeros((4, 6, 3), np.uint8)
        f[0, 0, 0] = self.n % 256
        return True, f

    def release(self):
        self.released = True


def test_latest_frame_only_and_timestamps():
    caps = []

    def open_fn():
        caps.append(FakeCap())
        return caps[-1]

    r = _LatestFrameReader(open_fn)
    time.sleep(0.2)                       # camera produces ~100 frames meanwhile
    got = r.latest(0)
    assert got is not None
    frame, t, seq = got
    assert seq > 20                        # it skipped the backlog
    assert abs(time.time() - t) < 0.05     # the timestamp is the arrival time
    assert r.latest(seq) is None or r.latest(seq)[2] > seq   # never the same frame twice
    r.stop()


def test_reconnects_after_stream_drop():
    opened = []

    def open_fn():
        opened.append(FakeCap(fail_after=5))
        return opened[-1]

    r = _LatestFrameReader(open_fn)
    time.sleep(0.3)
    r.stop()
    assert len(opened) >= 3 and all(c.released for c in opened[:-1])


def test_open_failures_back_off_and_retry():
    calls = []

    def open_fn():
        calls.append(time.time())
        raise RuntimeError("camera not up yet")

    r = _LatestFrameReader(open_fn)
    time.sleep(1.5)
    r.stop()
    assert 1 <= len(calls) <= 3 and r.latest(0) is None


def test_gstreamer_pipeline_uses_hw_decoder_and_drops_stale_frames():
    p = gstreamer_pipeline("rtsp://192.168.144.25:8554/main.264")
    assert "v4l2h264dec" in p and "appsink drop=true max-buffers=1" in p and "latency=0" in p
