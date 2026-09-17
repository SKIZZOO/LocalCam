from __future__ import annotations

import shutil
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from .config import load_config
from .rtsp import RTSP_AUTO_TRANSPORT, RTSP_USER_AGENT, with_credentials


class Recorder:
    """One FFmpeg process that records one RTSP stream into rotating MKV segments."""

    def __init__(
        self,
        ffmpeg_path: str,
        root: Path,
        segment_minutes: int,
        min_free_gb: int,
        retention_days: int,
        username: str,
        password: str,
        on_log: Callable[[str], None],
    ) -> None:
        self.ffmpeg_path = ffmpeg_path
        self.root = root
        # Prevent accidental test settings from creating a large number of tiny files.
        self.segment_minutes = max(5, int(segment_minutes))
        self.min_free_gb = max(1, int(min_free_gb))
        self.retention_days = max(0, int(retention_days))
        self.username = username
        self.password = password
        self.on_log = on_log
        self.process: subprocess.Popen[str] | None = None
        self.lock = threading.RLock()
        self.camera_id = ''
        self.camera_name = ''
        self.url = ''
        self.stop_requested = False
        self.started_at = 0.0
        self.attempt_dir: Path | None = None
        self.attempt_started_files: set[Path] = set()
        self.reason = 'Continuous'

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def _reason(self) -> str:
        try:
            mode = str(load_config().get('record_mode', 'continuous')).lower()
        except Exception:
            mode = 'continuous'
        return {
            'continuous': 'Continuous',
            'motion': 'Motion',
            'manual': 'Manual',
        }.get(mode, 'Recording')

    def start(self, camera_id: str, camera_name: str, url: str) -> bool:
        with self.lock:
            if self.running:
                return True

            self.cleanup()
            self.root.mkdir(parents=True, exist_ok=True)
            day_dir = self.root / self.safe_name(camera_name) / datetime.now().strftime('%Y-%m-%d')
            day_dir.mkdir(parents=True, exist_ok=True)
            self.reason = self._reason()
            pattern = str(day_dir / f'{self.safe_name(camera_name)}_%Y-%m-%d_%H-%M-%S_{self.reason}.mkv')
            target = with_credentials(url, self.username, self.password)

            self.attempt_dir = day_dir
            self.attempt_started_files = {p.resolve() for p in day_dir.glob('*.mkv') if p.is_file()}
            self.started_at = time.monotonic()
            self.stop_requested = False
            self.camera_id = camera_id
            self.camera_name = camera_name
            self.url = url

            cmd = [
                self.ffmpeg_path,
                '-hide_banner',
                '-loglevel',
                'error',
                '-rtsp_transport',
                RTSP_AUTO_TRANSPORT,
                '-user_agent',
                RTSP_USER_AGENT,
                '-allowed_media_types',
                'video',
                '-timeout',
                '15000000',
                '-i',
                target,
                '-map',
                '0:v:0?',
                '-map',
                '0:a:0?',
                '-c',
                'copy',
                '-f',
                'segment',
                '-segment_time',
                str(self.segment_minutes * 60),
                '-reset_timestamps',
                '1',
                '-strftime',
                '1',
                '-segment_format',
                'matroska',
                pattern,
            ]

            try:
                proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding='utf-8',
                    errors='replace',
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                )
            except OSError as exc:
                self.on_log(f'{camera_name}: failed to start FFmpeg: {exc}')
                return False

            self.process = proc
            threading.Thread(target=self._read_stderr, args=(proc,), name='localcam-recorder-log', daemon=True).start()
            threading.Thread(target=self._watchdog, args=(proc,), name='localcam-recorder-watchdog', daemon=True).start()
            self.on_log(
                f'{camera_name}: recording started ({self.reason}, {RTSP_AUTO_TRANSPORT.upper()}, User-Agent: VLC-compatible, segment: {self.segment_minutes}m)'
            )
            return True

    def stop(self) -> None:
        with self.lock:
            proc = self.process
            elapsed = time.monotonic() - self.started_at if self.started_at else 0
            self.process = None
            self.stop_requested = True

        if proc is None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except OSError:
                pass
        self._cleanup_failed_attempts(short_attempt=elapsed < 8)

    def _read_stderr(self, proc: subprocess.Popen[str]) -> None:
        if not proc.stderr:
            return
        for line in proc.stderr:
            line = line.strip()
            if line:
                safe_line = line
                if self.username or self.password:
                    safe_line = safe_line.replace(self.username, '***').replace(self.password, '***')
                safe_line = safe_line.replace('rtsp://***:***@', 'rtsp://***@')
                self.on_log(f'{self.camera_name}: {safe_line}')

    def _watchdog(self, proc: subprocess.Popen[str]) -> None:
        code = proc.wait()
        elapsed = time.monotonic() - self.started_at if self.started_at else 0
        with self.lock:
            if self.process is proc:
                self.process = None
        if elapsed < 8:
            self._cleanup_failed_attempts(short_attempt=True)
        if not self.stop_requested and code not in (0, 15, -15):
            self.on_log(f'{self.camera_name}: FFmpeg exited with code {code}; the controller will reconnect.')

    def _cleanup_failed_attempts(self, short_attempt: bool) -> None:
        """Remove empty artifacts and tiny files created by failed RTSP attempts."""
        if not self.attempt_dir or not self.attempt_dir.exists():
            self.attempt_started_files.clear()
            return
        for path in self.attempt_dir.glob('*.mkv'):
            try:
                resolved = path.resolve()
                stat = path.stat()
                if resolved in self.attempt_started_files:
                    continue
                if stat.st_size == 0 or (short_attempt and stat.st_size < 1024 * 1024):
                    path.unlink()
            except OSError:
                pass
        self.attempt_started_files.clear()

    def cleanup(self) -> None:
        if not self.root.exists():
            return

        cutoff = time.time() - self.retention_days * 86400 if self.retention_days else None
        for path in self.root.rglob('*.mkv'):
            try:
                stat = path.stat()
                # Remove zero-byte files and very small failed connection artifacts.
                # A normal NVR segment after several minutes is comfortably larger.
                if stat.st_size < 1024 * 1024 and not cutoff:
                    path.unlink()
                elif cutoff and stat.st_mtime < cutoff:
                    path.unlink()
            except OSError:
                pass

        try:
            usage = shutil.disk_usage(self.root.anchor or self.root)
        except OSError:
            return

        minimum = self.min_free_gb * 1024**3
        while usage.free < minimum:
            files = sorted(
                (p for p in self.root.rglob('*.mkv') if p.is_file()),
                key=lambda p: p.stat().st_mtime,
            )
            if not files:
                break
            try:
                files[0].unlink()
            except OSError:
                break
            usage = shutil.disk_usage(self.root.anchor or self.root)

    @staticmethod
    def safe_name(text: str) -> str:
        return ''.join(ch if ch.isalnum() or ch in ' -_' else '_' for ch in text).strip() or 'camera'
