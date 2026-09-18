from __future__ import annotations

import io
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import threading
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from core.config import load_config, save_config
from core.db import EventStore
from core.motion import MotionDetector
from core.ptz import PTZController
from core.recorder import Recorder
from core.webrtc import WebRTCManager
from core.rtsp import RTSP_AUTO_TRANSPORT, RTSP_USER_AGENT, with_credentials

try:
    import psutil
except ImportError:
    psutil = None

APP_VERSION = '0.9.3'
BASE_DIR = Path(__file__).resolve().parent.parent


def safe_name(value: str) -> str:
    return ''.join(c if c.isalnum() or c in ' -_' else '_' for c in str(value)).strip() or 'camera'


def human_bytes(value: int | float) -> str:
    n = float(value)
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if n < 1024 or unit == 'TB':
            return f'{n:.1f} {unit}'
        n /= 1024
    return f'{value} B'


def quality_rtsp_url(url: str, quality: str) -> str:
    """Keep the explicitly selected camera feed at every browser quality level."""
    # Live quality changes control the browser-side output resolution/FPS and
    # JPEG quality. They must not silently replace a selected main feed with a
    # lower-quality camera substream.
    return str(url or '').strip()
def motion_selected_for_camera(camera_id: str, motion_config: dict[str, Any], selected_ids: list[Any] | set[Any] | tuple[Any, ...]) -> bool:
    """Return whether motion detection should run for one camera."""
    if not bool(motion_config.get('enabled')):
        return False
    selected = {str(value).strip() for value in selected_ids if str(value).strip()}
    # An empty selection keeps legacy behavior: all cameras are monitored.
    return not selected or str(camera_id) in selected


def lan_ip() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(('192.0.2.1', 80))
        return sock.getsockname()[0]
    except OSError:
        return '127.0.0.1'
    finally:
        sock.close()


class PreviewWorker:
    QUALITY_PRESETS = {
        'low': {'width': 640, 'fps': 10, 'quality': 7},
        'medium': {'width': 1280, 'fps': 15, 'quality': 5},
        'high': {'width': 1920, 'fps': 20, 'quality': 3},
        'ultra': {'width': 2560, 'fps': 25, 'quality': 2},
    }

    def __init__(self, ffmpeg, url, username, password, width, fps, on_frame, quality='high'):
        preset = self.QUALITY_PRESETS.get(str(quality).lower(), self.QUALITY_PRESETS['high'])
        target_width = min(int(width), preset['width']) if width else preset['width']
        target_fps = min(int(fps), preset['fps']) if fps else preset['fps']
        # The mobile apps keep the camera's native RTSP stream intact and let
        # the device decoder handle the high-quality picture. Browser playback
        # cannot consume raw RTSP, so we decode once and expose a clean MJPEG
        # preview at a controllable resolution/FPS instead of using an overly
        # small fixed 1280x8 stream.
        self.cmd = [
            ffmpeg,
            '-hide_banner',
            '-loglevel',
            'warning',
            '-rtsp_transport',
            RTSP_AUTO_TRANSPORT,
            '-user_agent',
            RTSP_USER_AGENT,
            '-allowed_media_types',
            'video',
            '-timeout',
            '15000000',
            '-probesize',
            '5000000',
            '-analyzeduration',
            '2000000',
            '-i',
            with_credentials(url, username, password),
            '-an',
            '-vf',
            f'scale={target_width}:-2,fps={target_fps}:round=near',
            '-q:v',
            str(preset['quality']),
            '-f',
            'mjpeg',
            'pipe:1',
        ]
        self._listener_lock = threading.RLock()
        self.listeners = []
        if on_frame:
            self.listeners.append(on_frame)
        self.proc = None
        self.thread = None
        self.stop_event = threading.Event()

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
            chunk = stream.read(4096)
            if not chunk:
                return None
            buf += chunk
            s = buf.find(b'\xff\xd8')
            if s >= 0:
                buf = buf[s:]
                break
            if len(buf) > 4_000_000:
                return None
        while True:
            chunk = stream.read(4096)
            if not chunk:
                return None
            buf += chunk
            e = buf.find(b'\xff\xd9')
            if e >= 0:
                return buf[:e + 2]
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
                time.sleep(3)
                continue
            try:
                while not self.stop_event.is_set() and self.proc.stdout:
                    frame = self._read_jpeg(self.proc.stdout)
                    if not frame:
                        break
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
            time.sleep(1)


class StreamState:
    def __init__(self, camera: dict[str, Any], cfg: dict[str, Any], store: EventStore, logger, server=None):
        self.camera = dict(camera)
        self.cfg = cfg
        self.store = store
        self.logger = logger
        self.server = server
        self.preview_key = None
        self.lock = threading.Condition()
        self._frame_callback = self._frame
        self.frame = None
        self.seq = 0
        self.last_frame = 0.0
        self.preview = None
        self.preview_quality = str(self.cfg.get('live_quality', 'high'))
        self.recorder = None
        self.motion = None
        self.motion_active = False
        self.event_id = None
        self.last_motion = 0.0
        self.last_error = ''
        self.last_motion_error = ''
        self.record_retry_at = 0.0
        self._start()
        m = self.cfg.get('motion', {})
        selected_motion_cameras = self.cfg.get('motion_cameras', [])
        if not isinstance(selected_motion_cameras, list):
            selected_motion_cameras = []
        motion_for_camera = motion_selected_for_camera(self.id, m, selected_motion_cameras)
        if motion_for_camera:
            self.motion = MotionDetector(
                self.get_frame,
                self._on_motion,
                m.get('interval_seconds', .5),
                m.get('threshold', 6),
                m.get('min_changed_fraction', .008),
            )
            self.motion.start()

    @property
    def id(self):
        return str(self.camera.get('id', ''))

    @property
    def name(self):
        return str(self.camera.get('name', self.id))

    def _start(self, quality=None):
        self.preview_quality = str(quality or self.preview_quality or self.cfg.get('live_quality', 'high')).lower()
        self.preview_url = quality_rtsp_url(self.camera['url'], self.preview_quality)
        if self.server:
            self.preview, self.preview_key = self.server.acquire_preview(
                self.preview_url,
                self.camera.get('username', ''),
                self.camera.get('password', ''),
                self.preview_quality,
                self._frame_callback,
            )
        else:
            self.preview_key = None
            self.preview = PreviewWorker(
                self.cfg['ffmpeg_path'],
                self.preview_url,
                self.camera.get('username', ''),
                self.camera.get('password', ''),
                int(self.cfg['web_live_width']),
                int(self.cfg['web_live_fps']),
                self._frame,
                self.preview_quality,
            )
            self.preview.start()

    def set_preview_quality(self, quality):
        quality = str(quality or 'high').lower()
        if quality not in PreviewWorker.QUALITY_PRESETS:
            quality = 'high'
        if quality == self.preview_quality and self.preview:
            return
        if self.preview:
            if self.server and self.preview_key:
                self.server.release_preview(self.preview_key, self._frame_callback)
            else:
                self.preview.stop()
        self.preview = None
        self.preview_key = None
        self._start(quality)
    def _frame(self, frame):
        with self.lock:
            self.frame = frame
            self.seq += 1
            self.last_frame = time.time()
            self.last_error = ''
            self.lock.notify_all()

    def get_frame(self):
        with self.lock:
            return self.frame

    def live_audio(self, handler):
        """Stream camera audio as Ogg/Opus, which browsers can play directly."""
        target = with_credentials(
            self.camera['url'],
            str(self.camera.get('username', '')),
            str(self.camera.get('password', '')),
        )
        cmd = [
            self.cfg['ffmpeg_path'],
            '-hide_banner',
            '-loglevel', 'error',
            '-rtsp_transport', RTSP_AUTO_TRANSPORT,
            '-user_agent', RTSP_USER_AGENT,
            '-timeout', '15000000',
            '-i', target,
            '-map', '0:a:0?',
            '-vn',
            '-c:a', 'libopus',
            '-ar', '48000',
            '-ac', '1',
            '-b:a', '96k',
            '-f', 'ogg',
            'pipe:1',
        ]
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                bufsize=64 * 1024,
            )
        except OSError:
            return self.server_app_error(handler, 'Live audio could not be started.')
        handler.send_response(200)
        handler.send_header('Content-Type', 'audio/ogg; codecs=opus')
        handler.send_header('Cache-Control', 'no-store')
        handler.send_header('X-Content-Type-Options', 'nosniff')
        handler.end_headers()
        try:
            while True:
                chunk = proc.stdout.read(64 * 1024) if proc.stdout else b''
                if not chunk:
                    break
                handler.wfile.write(chunk)
                handler.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass
        finally:
            try:
                if proc.poll() is None:
                    proc.terminate()
            except OSError:
                pass
            try:
                proc.wait(timeout=3)
            except Exception:
                try: proc.kill()
                except OSError: pass

    @staticmethod
    def server_app_error(handler, message):
        try:
            handler.send_response(500)
            handler.send_header('Content-Type', 'application/json; charset=utf-8')
            body = json.dumps({'error': message}).encode('utf-8')
            handler.send_header('Content-Length', str(len(body)))
            handler.end_headers()
            handler.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass

    def mjpeg(self, handler, quality='high', sync_at=0.0):
        quality = str(quality or 'high').lower()
        if quality not in PreviewWorker.QUALITY_PRESETS:
            quality = 'high'
        if quality != self.preview_quality:
            self.set_preview_quality(quality)

        with self.lock:
            last = self.seq
            frame = self.frame
        try:
            target = float(sync_at or 0.0)
        except (TypeError, ValueError):
            target = 0.0
        if target > time.time():
            time.sleep(min(1.5, target - time.time()))
        handler.send_response(200)
        handler.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=frame')
        handler.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
        handler.send_header('Pragma', 'no-cache')
        handler.end_headers()
        try:
            # Push the most recent frame immediately. Previously the browser
            # had to wait for another encoder frame before receiving anything.
            if frame:
                handler.wfile.write(
                    b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: '
                    + str(len(frame)).encode() + b'\r\n\r\n' + frame + b'\r\n'
                )
                handler.wfile.flush()

            while self.preview:
                with self.lock:
                    self.lock.wait_for(lambda: self.seq != last or self.preview is None, 4)
                    frame = self.frame
                    last = self.seq
                if frame:
                    handler.wfile.write(
                        b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: '
                        + str(len(frame)).encode() + b'\r\n\r\n' + frame + b'\r\n'
                    )
                    handler.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def _on_motion(self, active, frame):
        now = time.time()
        m = self.cfg.get('motion', {})
        if active:
            self.last_motion = now
            if not self.motion_active:
                self.motion_active = True
                snap = self.save_snapshot(frame) if m.get('save_event_snapshots', True) else ''
                self.event_id = self.store.start_event(self.id, self.name, snap)
                self.logger(f'{self.name}: motion detected (mean={self.motion.last_mean:.2f}, changed={self.motion.last_changed_fraction:.3f})')
                if self.cfg.get('record_mode') == 'motion':
                    self.start_recording()
            return
        if self.motion_active and now - self.last_motion >= float(m.get('cooldown_seconds', 15)):
            self.motion_active = False
            if self.event_id:
                self.store.end_event(self.event_id)
                self.event_id = None
            if self.cfg.get('record_mode') == 'motion':
                self.stop_recording()

    def save_snapshot(self, frame):
        if not frame:
            return ''
        root = Path(self.cfg.get('snapshot_root', str(BASE_DIR / 'snapshots'))) / safe_name(self.name) / datetime.now().strftime('%Y-%m-%d')
        root.mkdir(parents=True, exist_ok=True)
        path = root / f'{datetime.now():%Y-%m-%d_%H-%M-%S}.jpg'
        path.write_bytes(frame)
        base = Path(self.cfg.get('snapshot_root', str(BASE_DIR / 'snapshots'))).resolve()
        try:
            return str(path.resolve().relative_to(base)).replace('\\', '/')
        except ValueError:
            return str(path)

    def refresh_motion(self):
        if self.motion:
            self.motion.stop()
            self.motion = None
        m = self.cfg.get('motion', {})
        selected_motion_cameras = self.cfg.get('motion_cameras', [])
        if not isinstance(selected_motion_cameras, list):
            selected_motion_cameras = []
        if motion_selected_for_camera(self.id, m, selected_motion_cameras):
            self.motion = MotionDetector(
                self.get_frame,
                self._on_motion,
                m.get('interval_seconds', .5),
                m.get('threshold', 6),
                m.get('min_changed_fraction', .008),
            )
            self.motion.start()

    def start_recording(self, force=False):
        if self.recorder and self.recorder.running:
            return True
        if not force and time.time() < self.record_retry_at:
            return False
        self.record_retry_at = time.time() + 15
        self.recorder = Recorder(
            self.cfg['ffmpeg_path'],
            Path(self.cfg['record_root']),
            int(self.cfg['segment_minutes']),
            int(self.cfg['min_free_gb']),
            int(self.cfg['max_retention_days']),
            str(self.camera.get('username', '')),
            str(self.camera.get('password', '')),
            self.logger,
        )
        ok = self.recorder.start(self.id, self.name, self.camera['url'])
        if not ok:
            self.last_error = 'Recording could not be started'
            self.record_retry_at = time.time() + 15
        else:
            self.record_retry_at = 0.0
        return ok

    def stop_recording(self):
        self.record_retry_at = 0.0
        if self.recorder:
            self.recorder.stop()

    def status(self):
        return {
            'id': self.id,
            'name': self.name,
            'online': bool(self.frame and time.time() - self.last_frame < 6),
            'recording': bool(self.recorder and self.recorder.running),
            'motion': self.motion_active,
            'last_error': self.last_error,
            'quality': self.preview_quality,
            'motion_enabled': bool(self.motion),
            'motion_diagnostics': self.motion.diagnostics() if self.motion else {
                'enabled': False,
                'active': False,
                'raw_detected': False,
                'mean_difference': 0.0,
                'changed_fraction': 0.0,
                'threshold': 0.0,
                'min_changed_fraction': 0.0,
                'last_check_at': 0.0,
                'last_error': 'Motion detection is disabled.',
            },
        }

    def stop(self):
        if self.motion:
            self.motion.stop()
        if self.preview:
            if self.server and self.preview_key:
                self.server.release_preview(self.preview_key, self._frame)
            else:
                self.preview.stop()
        if self.recorder:
            self.recorder.stop()
        self.preview = None
        self.preview_key = None


class LocalCamServer:
    def __init__(self, base_dir: Path = BASE_DIR):
        self.base_dir = Path(base_dir)
        self.lock = threading.RLock()
        self.log_lock = threading.RLock()
        self.log_path = self.base_dir / 'localcam.log'
        self.httpd = None
        self.thread = None
        self.started_at = time.time()
        self.sessions = {}
        self.failures = {}
        self.rebuild_lock = threading.Lock()
        self.rebuild_pending = False
        self.camera_discovery_lock = threading.Lock()
        self.camera_discovery_job = None
        self.camera_discovery_cache = {}
        self.store = EventStore(self.base_dir / 'localcam.sqlite3')
        cfg = load_config()
        self.store.ensure_legacy_admin(str(cfg.get('web_password_hash', '')))
        self.ptz = PTZController(self.log)
        self.webrtc = WebRTCManager(self.log)
        self.streams = {}
        self.preview_workers = {}
        self.rebuild_streams()

    def log(self, message):
        line = f'[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}'
        print(line)
        try:
            with self.log_lock:
                if self.log_path.exists() and self.log_path.stat().st_size > 5 * 1024 * 1024:
                    rotated = self.log_path.with_suffix('.log.1')
                    try:
                        if rotated.exists():
                            rotated.unlink()
                    except OSError:
                        pass
                    try:
                        self.log_path.replace(rotated)
                    except OSError:
                        pass
                with self.log_path.open('a', encoding='utf-8') as handle:
                    handle.write(line + '\n')
        except OSError:
            pass

    def cfg(self):
        return load_config()

    def _preview_key(self, url, username, password, quality):
        cfg = self.cfg()
        return (
            str(url or '').strip(),
            str(username or ''),
            str(password or ''),
            str(quality or 'high').lower(),
            int(cfg.get('web_live_width', 1280)),
            int(cfg.get('web_live_fps', 8)),
        )

    def acquire_preview(self, url, username, password, quality, callback):
        key = self._preview_key(url, username, password, quality)
        with self.lock:
            worker = self.preview_workers.get(key)
            if worker is None:
                cfg = self.cfg()
                worker = PreviewWorker(
                    cfg['ffmpeg_path'],
                    url,
                    username,
                    password,
                    int(cfg['web_live_width']),
                    int(cfg['web_live_fps']),
                    callback,
                    quality,
                    self.log,
                )
                self.preview_workers[key] = worker
                worker.start()
            else:
                worker.add_listener(callback)
            return worker, key

    def release_preview(self, key, callback):
        with self.lock:
            worker = self.preview_workers.get(key)
            if worker is None:
                return
            remover = getattr(worker, 'remove_listener', None)
            empty = bool(remover(callback)) if remover else True
            if empty:
                self.preview_workers.pop(key, None)
        if empty:
            worker.stop()

    def diagnostics(self):
        with self.lock:
            workers = []
            for key, worker in self.preview_workers.items():
                status_fn = getattr(worker, 'status', None)
                status = status_fn() if status_fn else {
                    'alive': bool(worker.thread and worker.thread.is_alive()),
                    'frames': 0,
                    'restarts': 0,
                    'consecutive_failures': 0,
                    'last_error': '',
                }
                source = key[0] if isinstance(key, tuple) and key else str(key)
                try:
                    from urllib.parse import urlsplit
                    parsed = urlsplit(source)
                    source = f'{parsed.scheme}://{parsed.hostname}:{parsed.port or ""}{parsed.path}'
                except Exception:
                    pass
                workers.append({
                    'source': source,
                    'listeners': len(getattr(worker, 'listeners', [])),
                    **status,
                })
            return {
                'threads': threading.active_count(),
                'streams': len(self.streams),
                'preview_workers': workers,
                'log_file': str(self.log_path),
            }

    def request_rebuild(self, reason='settings changed'):
        with self.rebuild_lock:
            if self.rebuild_pending:
                self.log(f'Rebuild already queued; coalescing request ({reason}).')
                return
            self.rebuild_pending = True
        self.log(f'Queuing stream rebuild: {reason}')
        threading.Thread(
            target=self.rebuild_streams,
            daemon=True,
            name='stream-rebuild',
        ).start()

    def rebuild_streams(self):
        with self.rebuild_lock:
            try:
                cfg = self.cfg()
                cameras = [dict(item) for item in cfg.get('cameras', []) if isinstance(item, dict)]
                changed = False
                used_ids = set()

                def slug(value):
                    return re.sub(r'[^a-z0-9]+', '-', str(value).lower()).strip('-')

                for i, c in enumerate(cameras):
                    c.setdefault('id', f'camera-{i + 1}')
                    c.setdefault('name', f'Camera {i + 1}')
                    c.setdefault('username', 'admin')
                    c.setdefault('password', '')
                    c.setdefault('ptz', {})
                    camera_id = str(c.get('id', '')).strip() or f'camera-{i + 1}'
                    if camera_id in used_ids:
                        base = slug(c.get('name')) or f'camera-{i + 1}'
                        candidate = base
                        suffix = 2
                        while candidate in used_ids:
                            candidate = f'{base}-{suffix}'
                            suffix += 1
                        camera_id = candidate
                        c['id'] = camera_id
                        changed = True
                    else:
                        c['id'] = camera_id
                    used_ids.add(camera_id)

                if changed:
                    cfg['cameras'] = cameras
                    selected = cfg.get('motion_cameras', [])
                    if isinstance(selected, list):
                        cfg['motion_cameras'] = [str(value) for value in selected if str(value) in used_ids]
                    save_config(cfg)

                with self.lock:
                    old_streams = list(self.streams.values())
                    self.streams = {}

                self.log(f'Stream rebuild stopping {len(old_streams)} existing stream objects.')
                for stream in old_streams:
                    try:
                        stream.stop()
                    except Exception as exc:
                        self.log(f'Stream stop during rebuild failed for {stream.name}: {exc}')

                new_streams = {}
                for camera in cameras:
                    if not camera.get('url') or 'CAMERA_IP' in str(camera.get('url')):
                        continue
                    try:
                        new_streams[camera['id']] = StreamState(
                            camera, cfg, self.store, self.log, self
                        )
                    except Exception as exc:
                        self.log(f'Failed to rebuild stream {camera.get("id")}: {exc}')

                with self.lock:
                    self.streams = new_streams
                self.log(f'Stream rebuild complete: {len(new_streams)} stream objects active.')
            finally:
                with self.rebuild_lock:
                    self.rebuild_pending = False

    def start(self):
        from core.web import LocalCamHandler
        cfg = self.cfg()
        bind = str(cfg.get('web_bind', '0.0.0.0'))
        port = max(1024, min(65535, int(cfg.get('web_port', 8765))))
        app = self

        class Handler(LocalCamHandler):
            server_app = app

        self.httpd = __import__('http.server', fromlist=['ThreadingHTTPServer']).ThreadingHTTPServer((bind, port), Handler)
        self.httpd.daemon_threads = True
        self.httpd.allow_reuse_address = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True, name='localcam-http')
        self.thread.start()
        self.log(f'Web server listening on {self.url()}')
        threading.Thread(target=self._controller, daemon=True, name='recording-controller').start()
        threading.Thread(target=self._maintenance, daemon=True, name='maintenance').start()

    def _controller(self):
        while self.httpd:
            mode = self.cfg().get('record_mode')
            for s in list(self.streams.values()):
                # Browser navigation/refresh must never be able to start recording.
                # Only the explicit continuous mode is allowed to auto-start it;
                # manual mode is the safe default.
                if mode == 'continuous':
                    s.start_recording()
                elif mode == 'motion':
                    if s.motion_active:
                        s.start_recording()
                    else:
                        s.stop_recording()
                # Manual recording is controlled exclusively by the dashboard
                # toggle; never stop it from the background controller.
            time.sleep(5)

    def _maintenance(self):
        while self.httpd:
            self.cleanup()
            self.expire_sessions()
            time.sleep(60)

    def cleanup(self):
        cfg = self.cfg()
        days = int(cfg.get('max_retention_days', 30))
        root = Path(cfg.get('snapshot_root', str(self.base_dir / 'snapshots')))
        if days > 0 and root.exists():
            cutoff = time.time() - days * 86400
            for p in root.rglob('*.jpg'):
                try:
                    if p.stat().st_mtime < cutoff:
                        p.unlink()
                except OSError:
                    pass
        rec = Path(cfg.get('record_root', str(self.base_dir / 'recordings')))
        if rec.exists():
            try:
                u = shutil.disk_usage(Path(rec.anchor or rec))
                minimum = int(cfg.get('min_free_gb', 20)) * 1024**3
            except OSError:
                return
            while u.free < minimum:
                files = sorted((p for p in rec.rglob('*.mkv') if p.is_file()), key=lambda p: p.stat().st_mtime)
                if not files:
                    break
                try:
                    files[0].unlink()
                except OSError:
                    break
                u = shutil.disk_usage(Path(rec.anchor or rec))
        for s in self.streams.values():
            if not s.status()['online']:
                s.last_error = 'Stream offline; waiting for reconnect'

    def expire_sessions(self):
        now = time.time()
        with self.lock:
            for sid in list(self.sessions):
                if self.sessions[sid]['expires'] <= now:
                    self.sessions.pop(sid, None)

    def stop(self):
        if self.httpd:
            try:
                self.httpd.shutdown()
                self.httpd.server_close()
            except OSError:
                pass
        self.httpd = None
        for s in list(self.streams.values()):
            s.stop()
        self.streams.clear()
        if getattr(self, 'webrtc', None):
            self.webrtc.close()

    def url(self):
        cfg = self.cfg()
        host = '127.0.0.1' if cfg.get('web_bind') == '127.0.0.1' else lan_ip()
        return f'http://{host}:{int(cfg.get("web_port", 8765))}/'

    def is_first_run(self):
        return self.store.user_count() == 0

    def session(self, handler):
        if not self.cfg().get('web_auth_enabled', True):
            return {'user_id': 0, 'username': 'system', 'role': 'admin', 'expires': float('inf')}
        c = __import__('http.cookies', fromlist=['SimpleCookie']).SimpleCookie(handler.headers.get('Cookie', ''))
        sid = c.get('localcam_session')
        data = self.sessions.get(sid.value) if sid else None
        return data if data and data['expires'] > time.time() else None

    def role(self, handler, *roles):
        u = self.session(handler)
        return bool(u and u['role'] in roles)

    def authenticate(self, username, password, ip):
        now = time.time()
        recent = [t for t in self.failures.get(ip, []) if now - t < 300]
        if len(recent) >= 8:
            raise ValueError('Too many login attempts. Try again later.')
        user = self.store.get_user_by_username(username)
        from core.config import verify_password
        if not user or not user['enabled'] or not verify_password(password, user['password_hash']):
            recent.append(now)
            self.failures[ip] = recent
            return None
        self.failures.pop(ip, None)
        self.store.mark_login(user['id'])
        return {'user_id': int(user['id']), 'username': user['username'], 'role': user['role']}

    def new_session(self, user):
        sid = secrets.token_urlsafe(32)
        self.sessions[sid] = {
            **user,
            'expires': time.time() + max(1, int(self.cfg().get('web_session_hours', 12))) * 3600,
        }
        return sid

    def safe_settings(self):
        cfg = json.loads(json.dumps(self.cfg()))
        cfg['web_password_hash'] = ''
        for c in cfg.get('cameras', []):
            c['password'] = '********' if c.get('password') else ''
            if c.get('ptz', {}).get('password'):
                c['ptz']['password'] = '********'
        cfg['users'] = self.store.list_users()
        return cfg

    def save_settings(self, payload):
        cfg = self.cfg()
        keys = (
            'ffmpeg_path', 'record_root', 'snapshot_root', 'record_mode', 'segment_minutes',
            'min_free_gb', 'max_retention_days', 'web_bind', 'web_port', 'web_live_fps',
            'web_live_width', 'web_enabled', 'web_auto_open', 'web_auth_enabled',
            'notifications_enabled', 'web_session_hours', 'motion', 'motion_cameras'
        )
        for k in keys:
            if k in payload:
                cfg[k] = payload[k]
        cfg['segment_minutes'] = max(1, int(cfg.get('segment_minutes', 10)))
        cfg['min_free_gb'] = max(1, int(cfg.get('min_free_gb', 20)))
        cfg['max_retention_days'] = max(0, int(cfg.get('max_retention_days', 30)))
        cfg['web_port'] = max(1024, min(65535, int(cfg.get('web_port', 8765))))
        cfg['web_live_fps'] = max(1, min(15, int(cfg.get('web_live_fps', 8))))
        cfg['web_live_width'] = max(320, min(2560, int(cfg.get('web_live_width', 1280))))
        cfg['web_session_hours'] = max(1, min(168, int(cfg.get('web_session_hours', 12))))

        selected_motion = cfg.get('motion_cameras', [])
        if not isinstance(selected_motion, list):
            selected_motion = []
        cfg['motion_cameras'] = list(dict.fromkeys(
            str(value).strip() for value in selected_motion if str(value).strip()
        ))

        motion = cfg.get('motion') if isinstance(cfg.get('motion'), dict) else {}
        cfg['motion'] = {
            **motion,
            'enabled': bool(motion.get('enabled', True)),
            'interval_seconds': max(0.25, min(5.0, float(motion.get('interval_seconds', 0.5)))),
            'threshold': max(0.1, min(50.0, float(motion.get('threshold', 6.0)))),
            'min_changed_fraction': max(0.001, min(0.5, float(motion.get('min_changed_fraction', 0.008)))),
            'cooldown_seconds': max(0.0, min(300.0, float(motion.get('cooldown_seconds', 15.0)))),
            'save_event_snapshots': bool(motion.get('save_event_snapshots', True)),
        }

        def normalize_storage_path(value, fallback):
            raw = os.path.expandvars(os.path.expanduser(str(value or fallback).strip()))
            path = Path(raw)
            if not path.is_absolute():
                path = self.base_dir / path
            return str(path.resolve(strict=False))

        cfg['record_root'] = normalize_storage_path(
            cfg.get('record_root'), self.base_dir / 'recordings'
        )
        cfg['snapshot_root'] = normalize_storage_path(
            cfg.get('snapshot_root'), self.base_dir / 'snapshots'
        )
        cfg['record_mode'] = cfg.get('record_mode') if cfg.get('record_mode') in ('continuous', 'motion', 'manual') else 'continuous'

        old = cfg.get('cameras', [])
        cameras = []
        used_ids = set()
        for i, item in enumerate(payload.get('cameras', old)):
            if not isinstance(item, dict) or not str(item.get('url', '')).strip():
                continue
            prev = old[i] if i < len(old) else {}
            pp = item.get('password', '')
            pp = prev.get('password', '') if pp == '********' else str(pp)
            ptz = item.get('ptz') or {}
            prevptz = prev.get('ptz') or {}
            ppass = ptz.get('password', '')
            ppass = prevptz.get('password', '') if ppass == '********' else str(ppass)

            camera_id = str(item.get('id') or f'camera-{i + 1}').strip() or f'camera-{i + 1}'
            if camera_id in used_ids:
                base = re.sub(r'[^a-z0-9]+', '-', str(item.get('name') or f'camera-{i + 1}').lower()).strip('-') or f'camera-{i + 1}'
                camera_id = base
                suffix = 2
                while camera_id in used_ids:
                    camera_id = f'{base}-{suffix}'
                    suffix += 1
            used_ids.add(camera_id)

            cameras.append({
                'id': camera_id,
                'name': str(item.get('name') or f'Camera {i + 1}'),
                'url': str(item['url']).strip(),
                'username': str(item.get('username', 'admin')),
                'password': pp,
                'ptz': {
                    'enabled': bool(ptz.get('enabled', False)),
                    'host': str(ptz.get('host', '')),
                    'port': int(ptz.get('port', 80) or 80),
                    'username': str(ptz.get('username', '')),
                    'password': ppass,
                },
            })
        # An explicitly submitted camera list is authoritative. An empty
        # list means the user intentionally removed the last camera.
        cfg['cameras'] = cameras

        camera_ids = {str(camera['id']) for camera in cameras}
        cfg['motion_cameras'] = [
            camera_id for camera_id in cfg.get('motion_cameras', [])
            if camera_id in camera_ids
        ]

        previous_cameras = old
        camera_changed = cameras != previous_cameras
        previous_runtime = {
            'ffmpeg_path': load_config().get('ffmpeg_path'),
            'web_live_fps': self.cfg().get('web_live_fps'),
            'web_live_width': self.cfg().get('web_live_width'),
            'live_quality': self.cfg().get('live_quality'),
        }
        runtime_changed = (
            camera_changed
            or previous_runtime['ffmpeg_path'] != cfg.get('ffmpeg_path')
            or previous_runtime['web_live_fps'] != cfg.get('web_live_fps')
            or previous_runtime['web_live_width'] != cfg.get('web_live_width')
            or previous_runtime['live_quality'] != cfg.get('live_quality')
        )

        started = time.monotonic()
        save_config(cfg)
        self.log(f'Settings saved in {(time.monotonic() - started):.3f}s; camera_changed={camera_changed}, runtime_changed={runtime_changed}')

        if runtime_changed:
            self.request_rebuild('camera/preview runtime settings changed')
        else:
            with self.lock:
                for stream in self.streams.values():
                    stream.cfg = cfg
                    stream.refresh_motion()

        return self.safe_settings()

    def save_camera_layout(self, payload):
        """Persist dashboard camera order/names without touching stream credentials."""
        requested = payload.get('cameras') if isinstance(payload, dict) else None
        if not isinstance(requested, list):
            raise ValueError('Camera layout must contain a cameras list.')

        cfg = self.cfg()
        existing = {str(camera.get('id')): dict(camera) for camera in cfg.get('cameras', [])}
        seen = set()
        ordered = []

        for item in requested:
            if not isinstance(item, dict):
                continue
            camera_id = str(item.get('id', '')).strip()
            if not camera_id or camera_id in seen or camera_id not in existing:
                continue
            camera = existing[camera_id]
            name = str(item.get('name', camera.get('name', camera_id))).strip()
            camera['name'] = name or camera_id
            ordered.append(camera)
            seen.add(camera_id)

        # Preserve any camera that was not included by the browser, rather than
        # allowing a stale tab to accidentally delete it.
        for camera in cfg.get('cameras', []):
            camera_id = str(camera.get('id', '')).strip()
            if camera_id and camera_id not in seen:
                ordered.append(dict(camera))

        cfg['cameras'] = ordered
        save_config(cfg)

        with self.lock:
            current = self.streams
            self.streams = {camera['id']: current[camera['id']] for camera in ordered if camera['id'] in current}
            for camera in ordered:
                stream = current.get(camera['id'])
                if stream:
                    stream.camera['name'] = camera.get('name', stream.camera.get('name', stream.id))

        return self.safe_settings()

    def talk(self, stream: StreamState, audio_path: str, volume: float = 0.05):
        """Send a short microphone clip through an ONVIF audio backchannel."""
        settings = stream.camera.get('ptz') or {}
        host = str(settings.get('host') or '').strip()
        if not host:
            try:
                host = __import__('urllib.parse', fromlist=['urlsplit']).urlsplit(
                    stream.camera['url']
                ).hostname or ''
            except Exception:
                host = ''
        if not host:
            raise RuntimeError('Camera ONVIF host is not configured.')

        try:
            from rtsp_backchannel import play_file
        except Exception as exc:
            raise RuntimeError(
                'Two-way talk support is not installed. Restart run.bat so it can install the audio backchannel package.'
            ) from exc

        ffmpeg_path = str(self.cfg().get('ffmpeg_path', 'ffmpeg'))
        ffmpeg_file = Path(ffmpeg_path)
        if ffmpeg_file.is_file():
            ffmpeg_dir = str(ffmpeg_file.parent.resolve())
            os.environ['PATH'] = ffmpeg_dir + os.pathsep + os.environ.get('PATH', '')

        result = play_file(
            host=host,
            user=str(stream.camera.get('username', '')),
            password=str(stream.camera.get('password', '')),
            file=audio_path,
            volume=max(0.0, min(1.0, float(volume))),
            codec='auto',
        )
        return {
            'codec': getattr(result, 'codec', ''),
            'packets_sent': getattr(result, 'packets_sent', 0),
            'duration_seconds': getattr(result, 'duration_seconds', 0),
        }

    def info(self):
        cfg = self.cfg()
        root = Path(cfg.get('record_root', str(self.base_dir / 'recordings')))
        drive = Path(root.anchor or root)
        try:
            u = shutil.disk_usage(drive)
            storage = {
                'path': str(root),
                'free': u.free,
                'total': u.total,
                'free_human': human_bytes(u.free),
                'total_human': human_bytes(u.total),
                'used_percent': round((u.total - u.free) / u.total * 100, 1) if u.total else 0,
            }
        except OSError:
            storage = {'path': str(root), 'free': 0, 'total': 0, 'free_human': '—', 'total_human': '—', 'used_percent': 0}
        system = {
            'cpu_percent': psutil.cpu_percent(interval=None) if psutil else None,
            'memory_percent': psutil.virtual_memory().percent if psutil else None,
            'uptime_seconds': time.time() - self.started_at,
        }
        return {
            'app': 'LocalCam', 'version': APP_VERSION, 'hostname': socket.gethostname(),
            'url': self.url(), 'lan_ip': lan_ip(), 'storage': storage,
            'streams': [s.status() for s in self.streams.values()],
            'record_mode': cfg.get('record_mode'), 'system': system,
        }

    def backup(self):
        out = io.BytesIO()
        with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
            for name in ('config.json', 'localcam.sqlite3'):
                p = self.base_dir / name
                if p.exists():
                    z.writestr(name, p.read_bytes())
        out.seek(0)
        return out.read()

    def restore(self, raw):
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            names = set(z.namelist())
            if 'config.json' in names:
                (self.base_dir / 'config.json').write_bytes(z.read('config.json'))
            if 'localcam.sqlite3' in names:
                (self.base_dir / 'localcam.sqlite3').write_bytes(z.read('localcam.sqlite3'))
