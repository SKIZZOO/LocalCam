from __future__ import annotations

import subprocess
import threading
import time
from urllib.parse import urlsplit

from .rtsp import RTSP_AUTO_TRANSPORT, RTSP_USER_AGENT, with_credentials


class FastPreviewWorker:
    """Low-latency MJPEG preview worker tuned for RTSP cameras that work in VLC."""

    QUALITY_PRESETS = {
        'low': {'width': 640, 'fps': 10, 'quality': 6},
        'medium': {'width': 1280, 'fps': 15, 'quality': 4},
        'high': {'width': 1920, 'fps': 15, 'quality': 2},
        'ultra': {'width': 2560, 'fps': 15, 'quality': 1},
    }

    def __init__(self, ffmpeg, url, username, password, width, fps, on_frame, quality='high', logger=None):
        preset = self.QUALITY_PRESETS.get(str(quality).lower(), self.QUALITY_PRESETS['high'])
        # The dashboard quality selector is the source of truth for live
        # output size/FPS. The camera's own stream is never replaced by a
        # lower-quality substream.
        fps = max(1, min(int(preset['fps']), 30))
        width = max(320, min(int(preset['width']), 2560))
        target = with_credentials(url, username, password)
        self.cmd = [
            ffmpeg,
            '-hide_banner',
            '-loglevel',
            'error',
            '-rtsp_transport',
            RTSP_AUTO_TRANSPORT,
            '-user_agent',
            RTSP_USER_AGENT,
            '-allowed_media_types',
            'video',
            '-fflags',
            'nobuffer+discardcorrupt',
            '-flags',
            'low_delay',
            '-avioflags',
            'direct',
            '-max_delay',
            '0',
            '-reorder_queue_size',
            '0',
            '-use_wallclock_as_timestamps',
            '1',
            '-probesize',
            '500000',
            '-analyzeduration',
            '150000',
            '-i',
            target,
            '-an',
            '-vf',
            f"scale=w='min(iw,{width})':h=-2:flags=lanczos,fps={fps}:round=near",
            '-q:v',
            str(preset['quality']),
            '-pix_fmt',
            'yuvj420p',
            '-f',
            'mjpeg',
            'pipe:1',
        ]
        self.logger = logger
        self._listener_lock = threading.RLock()
        self.listeners = []
        if on_frame:
            self.listeners.append(on_frame)
        self.on_frame = on_frame
        self.proc = None
        self.thread = None
        self.stop_event = threading.Event()
        self.last_frame_at = 0.0
        self.frames = 0
        self.restart_count = 0
        self.consecutive_failures = 0
        self.last_error = ''

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, daemon=True, name='preview')
        self.thread.start()

    def add_listener(self, callback):
        with self._listener_lock:
            if callback not in self.listeners:
                self.listeners.append(callback)

    def remove_listener(self, callback):
        with self._listener_lock:
            self.listeners = [listener for listener in self.listeners if listener != callback]
            return not self.listeners

    def status(self):
        with self._listener_lock:
            listeners = len(self.listeners)
        return {
            'alive': bool(self.thread and self.thread.is_alive() and not self.stop_event.is_set()),
            'frames': int(self.frames),
            'restarts': int(self.restart_count),
            'consecutive_failures': int(self.consecutive_failures),
            'last_frame_at': float(self.last_frame_at),
            'last_error': self.last_error,
            'listeners': listeners,
        }

    def stop(self):
        self.stop_event.set()
        if self.proc:
            try:
                self.proc.kill()
            except OSError:
                pass
        thread = self.thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=2)
        self.thread = None

    @staticmethod
    def _read_jpeg(stream):
        buf = b''
        while True:
            chunk = stream.read(8192)
            if not chunk:
                return None
            buf += chunk
            start = buf.find(b'\xff\xd8')
            if start >= 0:
                buf = buf[start:]
                break
            if len(buf) > 4_000_000:
                return None
        while True:
            chunk = stream.read(8192)
            if not chunk:
                return None
            buf += chunk
            end = buf.find(b'\xff\xd9')
            if end >= 0:
                return buf[:end + 2]
            if len(buf) > 4_000_000:
                return None

    def _run(self):
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        while not self.stop_event.is_set():
            try:
                self.proc = subprocess.Popen(
                    self.cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    bufsize=0,
                    creationflags=flags,
                )
                self.restart_count += 1
            except OSError as exc:
                self.consecutive_failures += 1
                self.last_error = str(exc)
                if self.logger:
                    self.logger(f'Preview worker failed to start for {self._safe_target()}: {exc}')
                self.stop_event.wait(min(15.0, 1.0 + self.consecutive_failures * 1.5))
                continue

            got_frame = False
            try:
                while not self.stop_event.is_set() and self.proc.stdout:
                    frame = self._read_jpeg(self.proc.stdout)
                    if not frame:
                        break
                    got_frame = True
                    self.consecutive_failures = 0
                    self.last_error = ''
                    self.last_frame_at = time.time()
                    self.frames += 1
                    with self._listener_lock:
                        listeners = list(self.listeners)
                    for listener in listeners:
                        try:
                            listener(frame)
                        except Exception:
                            pass
            finally:
                try:
                    self.proc.kill()
                except OSError:
                    pass
                self.proc = None

            if not got_frame and not self.stop_event.is_set():
                self.consecutive_failures += 1
                self.last_error = 'FFmpeg preview ended before a frame was received.'
                if self.logger:
                    self.logger(
                        f'Preview worker lost stream for {self._safe_target()} '
                        f'(restart #{self.restart_count}, consecutive failures={self.consecutive_failures})'
                    )
            delay = min(15.0, 0.5 * (2 ** min(self.consecutive_failures, 5)))
            self.stop_event.wait(delay)

    def _safe_target(self):
        try:
            from urllib.parse import urlsplit
            parts = urlsplit(self.cmd[self.cmd.index('-i') + 1])
            return f'{parts.scheme}://{parts.hostname}:{parts.port or ""}{parts.path}'
        except Exception:
            return 'RTSP target'


def install_preview_tuning() -> None:
    """Replace the legacy preview worker without changing the NVR/recording code."""
    from . import nvr

    if getattr(nvr, 'PreviewWorker', None) is FastPreviewWorker:
        return
    nvr.PreviewWorker = FastPreviewWorker
