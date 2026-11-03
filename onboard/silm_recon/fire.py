"""Forest-fire / smoke detector.

Fire and smoke are not COCO classes and good aerial datasets are scarce, so this
module gives you something that works on day one with no training: a classical
colour/texture heuristic. It flags:

  * active flame  - bright, saturated red/orange pixels forming a blob
  * smoke plume   - large, low-saturation grey regions with high local variance

It is deliberately conservative and is meant to *trigger a saved photo for later
human review*, not to be a certified fire-detection system. If you later train a
proper fire/smoke CNN, drop it in as a normal detector model and disable this.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class FireHit:
    label: str          # "fire" or "smoke"
    score: float        # 0..1 heuristic confidence
    box: tuple[float, float, float, float]  # normalised xmin,ymin,xmax,ymax


class FireDetector:
    def __init__(self, cfg: dict):
        self.enabled = bool(cfg.get("enabled", True))
        self.min_fire_area_frac = float(cfg.get("min_fire_area_frac", 0.002))
        self.min_smoke_area_frac = float(cfg.get("min_smoke_area_frac", 0.03))
        self.fire_score = float(cfg.get("fire_score", 0.6))
        self.smoke_score = float(cfg.get("smoke_score", 0.5))
        self.detect_smoke = bool(cfg.get("detect_smoke", True))

    def detect(self, frame_bgr: np.ndarray) -> list[FireHit]:
        if not self.enabled:
            return []
        import cv2

        h, w = frame_bgr.shape[:2]
        total = float(h * w)
        hits: list[FireHit] = []

        hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
        H, S, V = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]

        # --- Active flame: hot, saturated red/orange/yellow ----------------
        flame = (
            ((H <= 25) | (H >= 160))
            & (S >= 120)
            & (V >= 180)
        ).astype(np.uint8)
        # also catch near-white hot cores
        flame |= ((V >= 245) & (S >= 60)).astype(np.uint8)
        hits += self._blobs(flame, w, h, total, self.min_fire_area_frac,
                            "fire", self.fire_score, cv2)

        # --- Smoke: large low-saturation grey with texture ----------------
        if self.detect_smoke:
            grey = ((S <= 60) & (V >= 90) & (V <= 220)).astype(np.uint8)
            hits += self._blobs(grey, w, h, total, self.min_smoke_area_frac,
                                "smoke", self.smoke_score, cv2,
                                require_texture=True, frame_bgr=frame_bgr)
        return hits

    def _blobs(self, mask, w, h, total, min_area_frac, label, base_score, cv2,
               require_texture=False, frame_bgr=None) -> list[FireHit]:
        kernel = np.ones((5, 5), np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        n, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
        out: list[FireHit] = []
        for i in range(1, n):
            area = stats[i, cv2.CC_STAT_AREA]
            if area / total < min_area_frac:
                continue
            x = stats[i, cv2.CC_STAT_LEFT]
            y = stats[i, cv2.CC_STAT_TOP]
            bw = stats[i, cv2.CC_STAT_WIDTH]
            bh = stats[i, cv2.CC_STAT_HEIGHT]

            if require_texture and frame_bgr is not None:
                roi = frame_bgr[y:y + bh, x:x + bw]
                if roi.size == 0:
                    continue
                grey = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
                if float(grey.std()) < 10.0:  # flat grey = sky/road, not smoke
                    continue

            frac = min(1.0, area / total)
            score = min(0.99, base_score + 0.4 * frac)
            out.append(FireHit(
                label=label,
                score=float(score),
                box=(x / w, y / h, (x + bw) / w, (y + bh) / h),
            ))
        return out
