from __future__ import annotations

import threading
from io import BytesIO
from typing import Callable

from PIL import Image, ImageChops, ImageStat


class MotionDetector:
    """Lightweight frame-difference motion detector."""

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

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, name='localcam-motion', daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=2)
        self.thread = None
        self.previous = None

    def _run(self) -> None:
        while not self.stop_event.is_set():
            frame = self.get_frame()
            try:
                current = self._decode(frame) if frame else None
                if current is not None:
                    self.on_state(self._compare(current), frame)
            except Exception:
                # Motion detection must never be able to stop live streaming.
                pass
            self.stop_event.wait(self.interval)

    @staticmethod
    def _decode(data: bytes) -> Image.Image:
        return Image.open(BytesIO(data)).convert('L').resize((320, 180))

    def _compare(self, current: Image.Image) -> bool:
        previous = self.previous
        self.previous = current.copy()
        if previous is None:
            return False

        diff = ImageChops.difference(previous, current)
        mean = ImageStat.Stat(diff).mean[0]
        histogram = diff.histogram()
        changed = sum(histogram[12:]) / (320 * 180)
        return mean >= self.threshold and changed >= self.min_changed_fraction
