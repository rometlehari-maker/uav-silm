"""Companion-computer health: temperature, throttling, power, memory, storage, rate.

A Raspberry Pi 4 sealed in a fuselage next to the ESC can overheat and throttle
(it starts slowing itself down at 80 C; the project's own notes: LW-PLA
softens at 55-60 C), a sagging 5 V supply (the Pi 4 needs 5.1 V / 3 A) causes
under-voltage throttling, the 2 GB model can run short of memory, and a full SD
card silently ends data capture. This samples all of that, logs it to
health.ndjson in the mission folder, warns on the console/journal, and tells
the main loop to shed load when storage runs low (first stop archiving raw
frames, then stop saving entirely).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time

# vcgencmd get_throttled bits
THROTTLE_BITS = {
    0: "under-voltage now",
    1: "arm frequency capped now",
    2: "throttled now",
    3: "soft temperature limit now",
    16: "under-voltage occurred",
    17: "frequency capping occurred",
    18: "throttling occurred",
    19: "soft temperature limit occurred",
}


def read_cpu_temp_c(path="/sys/class/thermal/thermal_zone0/temp") -> float | None:
    try:
        with open(path) as fh:
            return int(fh.read().strip()) / 1000.0
    except Exception:
        return None


def read_throttled() -> int | None:
    try:
        out = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True,
                             text=True, timeout=2).stdout
        return int(out.strip().split("=")[1], 16)
    except Exception:
        return None


def read_mem_available_mb(path="/proc/meminfo") -> float | None:
    """MemAvailable from the kernel (what can be used without swapping)."""
    try:
        with open(path) as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) / 1024.0
    except Exception:
        pass
    return None


def decode_throttled(flags: int | None) -> list[str]:
    if not flags:
        return []
    return [name for bit, name in THROTTLE_BITS.items() if flags & (1 << bit)]


class HealthMonitor:
    def __init__(self, cfg: dict | None, storage_dir: str, log_dir: str | None = None,
                 temp_reader=read_cpu_temp_c, throttle_reader=read_throttled,
                 disk_reader=None, mem_reader=read_mem_available_mb):
        cfg = cfg or {}
        self.interval_s = float(cfg.get("interval_s", 10.0))
        self.warn_temp_c = float(cfg.get("warn_temp_c", 75.0))
        self.low_mem_mb = float(cfg.get("low_mem_mb", 200.0))
        self._mem = mem_reader
        self.low_disk_mb = float(cfg.get("low_disk_mb", 500.0))        # stop raw frames
        self.critical_disk_mb = float(cfg.get("critical_disk_mb", 100.0))  # stop saving
        self.storage_dir = storage_dir
        self._log = os.path.join(log_dir, "health.ndjson") if log_dir else None
        self._temp = temp_reader
        self._thr = throttle_reader
        self._disk = disk_reader or (lambda: shutil.disk_usage(self.storage_dir).free / 1e6)
        self._last = 0.0
        self._frames = 0
        self._last_frames_t = time.time()
        self.low_disk = False
        self.critical_disk = False
        self.last_sample: dict = {}

    def frame_done(self) -> None:
        self._frames += 1

    def maybe_sample(self, now: float | None = None) -> dict | None:
        now = time.time() if now is None else now
        if now - self._last < self.interval_s:
            return None
        self._last = now
        dt = max(1e-3, now - self._last_frames_t)
        fps = self._frames / dt
        self._frames, self._last_frames_t = 0, now

        temp = self._temp()
        thr = self._thr()
        try:
            free_mb = self._disk()
        except Exception:
            free_mb = None            # storage unplugged / unreadable
        mem_mb = self._mem() if self._mem else None
        self.low_disk = free_mb is not None and free_mb < self.low_disk_mb
        self.critical_disk = free_mb is not None and free_mb < self.critical_disk_mb

        warnings = []
        if temp is not None and temp >= self.warn_temp_c:
            warnings.append(f"CPU {temp:.0f}C")
        now_flags = decode_throttled(thr & 0xF if thr is not None else None)
        warnings += now_flags
        if mem_mb is not None and mem_mb < self.low_mem_mb:
            warnings.append(f"memory low ({mem_mb:.0f} MB available)")
        if self.critical_disk:
            warnings.append(f"storage critical ({free_mb:.0f} MB): saving stopped")
        elif self.low_disk:
            warnings.append(f"storage low ({free_mb:.0f} MB): raw frame archive stopped")

        sample = {
            "t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
            "cpu_temp_c": None if temp is None else round(temp, 1),
            "throttled_hex": None if thr is None else hex(thr),
            "throttle_flags": decode_throttled(thr),
            "free_mb": None if free_mb is None else round(free_mb, 1),
            "mem_available_mb": None if mem_mb is None else round(mem_mb, 1),
            "fps": round(fps, 2),
            "warnings": warnings,
        }
        self.last_sample = sample
        if warnings:
            print("[health] WARNING: " + "; ".join(warnings))
        if self._log:
            try:
                with open(self._log, "a") as fh:
                    fh.write(json.dumps(sample) + "\n")
            except Exception:
                pass
        return sample
