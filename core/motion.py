from __future__ import annotations

import threading
import time
from io import BytesIO
from typing import Callable

from PIL import Image, ImageChops, ImageFilter, ImageStat


class MotionDetector:
    """Lightweight, debounced frame-difference motion detector.

    The detector works from the same JPEG frames already produced for the live
    preview, so it does not open a second RTSP session per camera.
    """

    WIDTH = 320
    HEIGHT = 180
    PIXEL_THRESHOLD = 18
    REQUIRED_HITS = 2
    REQUIRED_MISSES = 3

    def __init__(
        self,
        get_frame: Callable[[], bytes | None],
        on_state: Callable[[bool, bytes | None], None],
        interval_seconds: float,
        threshold: float,
        min_changed_fraction: float,
    ) -> None:
        self.get_frame = get_frame
        self.on_state = on_state
        self.interval = max(0.25, float(interval_seconds))
        self.threshold = max(0.1, float(threshold))
        self.min_changed_fraction = max(0.001, min(1.0, float(min_changed_fraction)))
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.previous: Image.Image | None = None
        self.active = False
        self.hit_count = 0
        self.miss_count = 0
        self.last_mean = 0.0
        self.last_changed_fraction = 0.0
        self.last_detected = False
        self.last_check_at = 0.0
        self.last_error = ''

    @property
    def enabled(self) -> bool:
        return bool(self.thread and self.thread.is_alive() and not self.stop_event.is_set())

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.previous = None
        self.active = False
        self.hit_count = 0
        self.miss_count = 0
        self.thread = threading.Thread(target=self._run, name='localcam-motion', daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=2)
        self.thread = None
        self.previous = None
        self.active = False
        self.hit_count = 0
        self.miss_count = 0

    def diagnostics(self) -> dict[str, float | bool | str]:
        return {
            'enabled': self.enabled,
            'active': self.active,
            'raw_detected': self.last_detected,
            'mean_difference': round(self.last_mean, 3),
            'changed_fraction': round(self.last_changed_fraction, 4),
            'threshold': self.threshold,
            'min_changed_fraction': self.min_changed_fraction,
            'last_check_at': self.last_check_at,
            'last_error': self.last_error,
        }

    def _run(self) -> None:
        while not self.stop_event.is_set():
            frame = self.get_frame()
            try:
                current = self._decode(frame) if frame else None
                if current is not None:
                    state = self._compare(current)
                    self.last_check_at = time.time()
                    self.on_state(state, frame)
            except Exception as exc:
                # Motion detection must never be able to stop live streaming,
                # but hiding every error made a broken detector look like a
                # detector that simply saw no motion.
                self.last_error = str(exc)
            self.stop_event.wait(self.interval)

    @classmethod
    def _decode(cls, data: bytes) -> Image.Image:
        return (
            Image.open(BytesIO(data))
            .convert('L')
            .resize((cls.WIDTH, cls.HEIGHT))
            .filter(ImageFilter.GaussianBlur(radius=1.2))
        )

    def _compare(self, current: Image.Image) -> bool:
        previous = self.previous
        self.previous = current.copy()
        if previous is None:
            self.last_mean = 0.0
            self.last_changed_fraction = 0.0
            self.last_detected = False
            return False

        diff = ImageChops.difference(previous, current)
        mean = float(ImageStat.Stat(diff).mean[0])
        histogram = diff.histogram()
        changed = sum(histogram[self.PIXEL_THRESHOLD:]) / float(self.WIDTH * self.HEIGHT)
        raw_detected = mean >= self.threshold and changed >= self.min_changed_fraction
        self.last_mean = mean
        self.last_changed_fraction = changed
        self.last_detected = raw_detected
        self.last_error = ''

        # Require consecutive detections to avoid compression/exposure flicker
        # creating one-frame events, and require a few quiet frames before an
        # event closes.
        if raw_detected:
            self.hit_count += 1
            self.miss_count = 0
        else:
            self.miss_count += 1
            self.hit_count = 0

        if not self.active and self.hit_count >= self.REQUIRED_HITS:
            self.active = True
        elif self.active and self.miss_count >= self.REQUIRED_MISSES:
            self.active = False

        return self.active
