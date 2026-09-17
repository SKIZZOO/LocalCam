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
        'high': {'width': 1920, 'fps': 20, 'quality': 2},
        'ultra': {'width': 2560, 'fps': 25, 'quality': 1},
    }

    def __init__(self, ffmpeg, url, username, password, width, fps, on_frame, quality='high'):
        preset = self.QUALITY_PRESETS.get(str(quality).lower(), self.QUALITY_PRESETS['high'])
        fps = max(1, min(int(fps or preset['fps']), preset['fps']))
        width = max(320, min(int(width or preset['width']), preset['width']))
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
            'nobuffer',
            '-flags',
            'low_delay',
            '-probesize',
            '3000000',
            '-analyzeduration',
            '1000000',
            '-i',
            target,
            '-an',
            '-vf',
            f'scale={width}:-2:flags=lanczos,fps={fps}:round=near',
            '-q:v',
            str(preset['quality']),
            '-pix_fmt',
            'yuvj420p',
            '-f',
            'mjpeg',
            'pipe:1',
        ]
        self.on_frame = on_frame
        self.proc = None
        self.thread = None
        self.stop_event = threading.Event()

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, daemon=True, name='preview')
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.proc:
            try:
                self.proc.kill()
            except OSError:
                pass
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
            except OSError:
                time.sleep(2)
                continue
            try:
                while not self.stop_event.is_set() and self.proc.stdout:
                    frame = self._read_jpeg(self.proc.stdout)
                    if not frame:
                        break
                    self.on_frame(frame)
            finally:
                try:
                    self.proc.kill()
                except OSError:
                    pass
                self.proc = None
            time.sleep(0.5)


def install_preview_tuning() -> None:
    """Replace the legacy preview worker without changing the NVR/recording code."""
    from . import nvr

    if getattr(nvr, 'PreviewWorker', None) is FastPreviewWorker:
        return
    nvr.PreviewWorker = FastPreviewWorker
