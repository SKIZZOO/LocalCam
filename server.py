from __future__ import annotations

import http.cookies
import json
import mimetypes
import os
import secrets
import shutil
import socket
import subprocess
import threading
import time
import urllib.parse
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from core.config import hash_password, load_config, save_config, verify_password
from core.db import EventStore
from core.motion import MotionDetector
from core.recorder import Recorder
from core.rtsp import test_rtsp, with_credentials

APP_VERSION = '0.5.0'
WEB_DIR = Path(__file__).resolve().parent / 'web'


def safe_name(text: str) -> str:
    return ''.join(ch if ch.isalnum() or ch in ' -_' else '_' for ch in text).strip() or 'camera'


def human_bytes(value: int | float) -> str:
    n = float(value)
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if n < 1024 or unit == 'TB':
            return f'{n:.1f} {unit}'
        n /= 1024
    return f'{value} B'


def lan_ip() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(('192.0.2.1', 80))
        return sock.getsockname()[0]
    except OSError:
        return '127.0.0.1'
    finally:
        sock.close()


def safe_join(root: Path, rel: str) -> Path | None:
    try:
        path = (root / urllib.parse.unquote(rel)).resolve()
        path.relative_to(root.resolve())
        return path
    except (ValueError, OSError):
        return None


class PreviewWorker:
    def __init__(self, ffmpeg: str, url: str, username: str, password: str, width: int, fps: int, on_frame) -> None:
        self.ffmpeg = ffmpeg
        self.url = with_credentials(url, username, password)
        self.width = width
        self.fps = fps
        self.on_frame = on_frame
        self.proc: subprocess.Popen[bytes] | None = None
        self.thread: threading.Thread | None = None
        self.stop_event = threading.Event()

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, daemon=True, name='preview-worker')
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        proc = self.proc
        if proc:
            try:
                proc.kill()
            except OSError:
                pass
        self.thread = None

    def _run(self) -> None:
        while not self.stop_event.is_set():
            cmd = [
                self.ffmpeg,
                '-hide_banner',
                '-loglevel',
                'error',
                '-rtsp_transport',
                'tcp',
                '-i',
                self.url,
                '-an',
                '-vf',
                f'scale={self.width}:-2,fps={self.fps}',
                '-q:v',
                '6',
                '-f',
                'mjpeg',
                'pipe:1',
            ]
            try:
                self.proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    bufsize=0,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                )
            except OSError:
                time.sleep(3)
                continue

            try:
                while not self.stop_event.is_set() and self.proc.stdout:
                    frame = read_jpeg(self.proc.stdout)
                    if not frame:
                        break
                    self.on_frame(frame)
            finally:
                try:
                    self.proc.kill()
                except OSError:
                    pass
                try:
                    self.proc.wait(timeout=1)
                except Exception:
                    pass
                self.proc = None
            time.sleep(1)


def read_jpeg(stream) -> bytes | None:
    start = b''
    while True:
        chunk = stream.read(4096)
        if not chunk:
            return None
        start += chunk
        pos = start.find(b'\xff\xd8')
        if pos >= 0:
            break
        if len(start) > 4_000_000:
            return None

    data = start[pos:]
    while True:
        chunk = stream.read(4096)
        if not chunk:
            return None
        data += chunk
        end = data.find(b'\xff\xd9')
        if end >= 0:
            return data[:end + 2]
        if len(data) > 4_000_000:
            return None


class StreamState:
    def __init__(self, camera: dict[str, Any], cfg: dict[str, Any], store: EventStore, logger) -> None:
        self.camera = camera
        self.cfg = cfg
        self.store = store
        self.logger = logger
        self.lock = threading.Condition()
        self.latest_frame: bytes | None = None
        self.frame_seq = 0
        self.last_frame_at = 0.0
        self.preview: PreviewWorker | None = None
        self.recorder: Recorder | None = None
        self.motion: MotionDetector | None = None
        self.motion_active = False
        self.event_id: int | None = None
        self.last_motion_time = 0.0
        self.online = False
        self.last_error = ''
        self._start_preview()

    @property
    def id(self) -> str:
        return str(self.camera.get('id', ''))

    @property
    def name(self) -> str:
        return str(self.camera.get('name', self.id))

    def _start_preview(self) -> None:
        self.preview = PreviewWorker(
            self.cfg['ffmpeg_path'],
            self.camera['url'],
            str(self.camera.get('username', '')),
            str(self.camera.get('password', '')),
            int(self.cfg['web_live_width']),
            int(self.cfg['web_live_fps']),
            self._frame,
        )
        self.preview.start()

        motion = self.cfg.get('motion', {})
        if motion.get('enabled'):
            self.motion = MotionDetector(
                self.get_frame,
                self._on_motion,
                motion.get('interval_seconds', 0.75),
                motion.get('threshold', 8.0),
                motion.get('min_changed_fraction', 0.012),
            )
            self.motion.start()

    def _frame(self, frame: bytes) -> None:
        with self.lock:
            self.latest_frame = frame
            self.frame_seq += 1
            self.last_frame_at = time.time()
            self.online = True
            self.last_error = ''
            self.lock.notify_all()

    def get_frame(self) -> bytes | None:
        with self.lock:
            return self.latest_frame

    def mjpeg(self, handler) -> None:
        with self.lock:
            last = self.frame_seq
        handler.send_response(200)
        handler.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=frame')
        handler.send_header('Cache-Control', 'no-store')
        handler.send_header('Connection', 'close')
        handler.end_headers()
        try:
            while True:
                with self.lock:
                    self.lock.wait_for(lambda: self.frame_seq != last or self.preview is None, timeout=4)
                    frame = self.latest_frame
                    last = self.frame_seq
                if frame:
                    payload = (
                        b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: '
                        + str(len(frame)).encode()
                        + b'\r\n\r\n'
                        + frame
                        + b'\r\n'
                    )
                    handler.wfile.write(payload)
                    handler.wfile.flush()
                elif self.preview is None:
                    break
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def _on_motion(self, active: bool, frame: bytes | None) -> None:
        now = time.time()
        if active:
            self.last_motion_time = now
            if not self.motion_active:
                self.motion_active = True
                snapshot = self.save_snapshot(frame) if self.cfg.get('motion', {}).get('save_event_snapshots', True) else ''
                self.event_id = self.store.start_event(self.id, self.name, snapshot)
                if self.cfg.get('record_mode') == 'motion':
                    self.start_recording()
            return

        cooldown = float(self.cfg.get('motion', {}).get('cooldown_seconds', 15.0))
        if self.motion_active and now - self.last_motion_time >= cooldown:
            self.motion_active = False
            if self.event_id:
                self.store.end_event(self.event_id)
                self.event_id = None
            if self.cfg.get('record_mode') == 'motion':
                self.stop_recording()

    def save_snapshot(self, frame: bytes | None) -> str:
        if not frame:
            return ''
        root = Path(self.cfg.get('snapshot_root', 'G:/LocalCam/snapshots')) / safe_name(self.name) / datetime.now().strftime('%Y-%m-%d')
        root.mkdir(parents=True, exist_ok=True)
        path = root / f'{datetime.now():%Y-%m-%d_%H-%M-%S}.jpg'
        path.write_bytes(frame)
        snapshot_root = Path(self.cfg.get('snapshot_root', 'G:/LocalCam/snapshots')).resolve()
        try:
            return str(path.resolve().relative_to(snapshot_root)).replace(os.sep, '/')
        except ValueError:
            return str(path)

    def start_recording(self) -> bool:
        if self.recorder and self.recorder.running:
            return True
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
        return ok

    def stop_recording(self) -> None:
        if self.recorder:
            self.recorder.stop()

    def status(self) -> dict[str, Any]:
        online = self.online and (time.time() - self.last_frame_at < 6.0)
        return {
            'id': self.id,
            'name': self.name,
            'online': online,
            'recording': bool(self.recorder and self.recorder.running),
            'motion': self.motion_active,
            'last_error': self.last_error,
        }

    def stop(self) -> None:
        if self.motion:
            self.motion.stop()
        if self.preview:
            self.preview.stop()
        if self.recorder:
            self.recorder.stop()
        self.preview = None


class LocalCamServer:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.lock = threading.RLock()
        self.httpd: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None
        self.sessions: dict[str, float] = {}
        self.store = EventStore(base_dir / 'localcam.sqlite3')
        self.streams: dict[str, StreamState] = {}
        self._rebuild_streams()

    def log(self, message: str) -> None:
        print(f'[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}')

    def cfg(self) -> dict[str, Any]:
        return load_config()

    def _rebuild_streams(self) -> None:
        cfg = self.cfg()
        with self.lock:
            for stream in list(self.streams.values()):
                stream.stop()
            self.streams.clear()
            for index, camera in enumerate(cfg.get('cameras', [])):
                cam = dict(camera)
                cam.setdefault('id', f'camera-{index + 1}')
                cam.setdefault('name', f'Camera {index + 1}')
                cam.setdefault('username', 'admin')
                cam.setdefault('password', '')
                if cam.get('url'):
                    self.streams[cam['id']] = StreamState(cam, cfg, self.store, self.log)

    def start(self) -> None:
        cfg = self.cfg()
        if not cfg.get('web_enabled', True):
            raise RuntimeError('Web server is disabled in configuration')
        self._rebuild_streams()

        bind = str(cfg.get('web_bind', '0.0.0.0'))
        port = max(1024, min(65535, int(cfg.get('web_port', 8765))))
        app = self

        class Handler(LocalCamHandler):
            server_app = app

        self.httpd = ThreadingHTTPServer((bind, port), Handler)
        self.httpd.daemon_threads = True
        self.httpd.allow_reuse_address = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True, name='localcam-http')
        self.thread.start()
        self.log(f'Web server listening on {self.url()}')
        threading.Thread(target=self._recording_loop, daemon=True, name='localcam-recording-loop').start()
        threading.Thread(target=self._maintenance_loop, daemon=True, name='localcam-maintenance').start()

    def _recording_loop(self) -> None:
        last_cleanup = 0.0
        while self.httpd:
            cfg = self.cfg()
            mode = cfg.get('record_mode')
            if mode == 'continuous':
                for stream in list(self.streams.values()):
                    stream.start_recording()
            elif mode != 'motion':
                for stream in list(self.streams.values()):
                    if stream.recorder and stream.recorder.running:
                        stream.stop_recording()

            if time.time() - last_cleanup >= 60:
                last_cleanup = time.time()
                for stream in list(self.streams.values()):
                    if stream.recorder:
                        stream.recorder.cleanup()
            time.sleep(5)

    def _maintenance_loop(self) -> None:
        while self.httpd:
            try:
                self.cleanup_snapshots()
                self.sessions = {sid: expiry for sid, expiry in self.sessions.items() if expiry > time.time()}
            except Exception as exc:
                self.log(f'Maintenance error: {exc}')
            time.sleep(60)

    def cleanup_snapshots(self) -> None:
        root = Path(self.cfg().get('snapshot_root', 'G:/LocalCam/snapshots'))
        if not root.exists():
            return
        retention = int(self.cfg().get('max_retention_days', 30))
        if retention <= 0:
            return
        cutoff = time.time() - retention * 86400
        for path in root.rglob('*.jpg'):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                pass

    def stop(self) -> None:
        if self.httpd:
            try:
                self.httpd.shutdown()
                self.httpd.server_close()
            except OSError:
                pass
        self.httpd = None
        for stream in list(self.streams.values()):
            stream.stop()
        self.streams.clear()

    def url(self) -> str:
        cfg = self.cfg()
        host = '127.0.0.1' if cfg.get('web_bind') == '127.0.0.1' else lan_ip()
        return f'http://{host}:{int(cfg.get("web_port", 8765))}/'

    def is_first_run(self) -> bool:
        return not bool(self.cfg().get('web_password_hash'))

    def authenticated(self, handler) -> bool:
        cfg = self.cfg()
        if not cfg.get('web_auth_enabled', True):
            return True
        cookie = http.cookies.SimpleCookie(handler.headers.get('Cookie', ''))
        sid = cookie.get('localcam_session')
        return bool(sid and sid.value in self.sessions and self.sessions[sid.value] > time.time())

    def login(self, password: str) -> bool:
        encoded = self.cfg().get('web_password_hash', '')
        return bool(encoded) and verify_password(password, encoded)

    def create_session(self) -> str:
        sid = secrets.token_urlsafe(32)
        self.sessions[sid] = time.time() + 12 * 3600
        return sid

    def safe_settings(self) -> dict[str, Any]:
        safe = json.loads(json.dumps(self.cfg()))
        safe['web_password_hash'] = ''
        safe['web_secret'] = ''
        for camera in safe.get('cameras', []):
            camera['password'] = '********' if camera.get('password') else ''
        return safe

    def save_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        cfg = self.cfg()
        allowed = (
            'ffmpeg_path', 'record_root', 'snapshot_root', 'record_mode', 'segment_minutes',
            'min_free_gb', 'max_retention_days', 'web_bind', 'web_port', 'web_live_fps',
            'web_live_width', 'web_enabled', 'web_auto_open', 'web_auth_enabled',
            'notifications_enabled', 'motion', 'cameras',
        )
        for key in allowed:
            if key in payload:
                cfg[key] = payload[key]
        if payload.get('web_password'):
            cfg['web_password_hash'] = hash_password(str(payload['web_password']))

        cfg['segment_minutes'] = max(1, int(cfg.get('segment_minutes', 10)))
        cfg['min_free_gb'] = max(1, int(cfg.get('min_free_gb', 20)))
        cfg['max_retention_days'] = max(0, int(cfg.get('max_retention_days', 30)))
        cfg['web_port'] = max(1024, min(65535, int(cfg.get('web_port', 8765))))
        cfg['web_live_fps'] = max(1, min(20, int(cfg.get('web_live_fps', 6))))
        cfg['web_live_width'] = max(320, min(2560, int(cfg.get('web_live_width', 960))))
        cfg['record_root'] = str(cfg.get('record_root', 'G:/LocalCam/recordings')).strip()
        cfg['snapshot_root'] = str(cfg.get('snapshot_root', 'G:/LocalCam/snapshots')).strip()
        if cfg.get('record_mode') not in ('continuous', 'motion', 'manual'):
            cfg['record_mode'] = 'continuous'

        old_by_id = {str(item.get('id')): item for item in self.cfg().get('cameras', []) if isinstance(item, dict)}
        old_by_url = {str(item.get('url')): item for item in self.cfg().get('cameras', []) if isinstance(item, dict)}
        streams = []
        for i, item in enumerate(cfg.get('cameras', [])):
            if not isinstance(item, dict) or not str(item.get('url', '')).strip():
                continue
            camera_id = str(item.get('id') or f'camera-{i + 1}')
            camera_url = str(item['url']).strip()
            password = str(item.get('password', ''))
            if password == '********':
                previous = old_by_id.get(camera_id) or old_by_url.get(camera_url)
                password = str((previous or {}).get('password', ''))
            streams.append({
                'id': camera_id,
                'name': str(item.get('name') or f'Camera {i + 1}'),
                'url': camera_url,
                'username': str(item.get('username', 'admin')),
                'password': password,
            })
        cfg['cameras'] = streams
        save_config(cfg)
        self._rebuild_streams()
        return self.safe_settings()

    def info(self) -> dict[str, Any]:
        cfg = self.cfg()
        root = Path(cfg.get('record_root', 'G:/LocalCam/recordings'))
        drive = Path(root.anchor or root)
        try:
            usage = shutil.disk_usage(drive)
            free, total = usage.free, usage.total
            storage = {
                'path': str(root),
                'free': free,
                'total': total,
                'free_human': human_bytes(free),
                'total_human': human_bytes(total),
                'used_percent': round((total - free) / total * 100, 1) if total else 0,
            }
        except OSError:
            storage = {
                'path': str(root),
                'free': 0,
                'total': 0,
                'free_human': 'Unknown',
                'total_human': 'Unknown',
                'used_percent': 0,
            }
        return {
            'app': 'LocalCam',
            'version': APP_VERSION,
            'hostname': socket.gethostname(),
            'url': self.url(),
            'lan_ip': lan_ip(),
            'storage': storage,
            'streams': [s.status() for s in self.streams.values()],
            'record_mode': cfg.get('record_mode'),
        }


class LocalCamHandler(BaseHTTPRequestHandler):
    server_app: LocalCamServer

    def log_message(self, fmt: str, *args: Any) -> None:
        self.server_app.log('WEB ' + fmt % args)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path.rstrip('/') or '/'
        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)

        if path == '/login':
            return self._serve('login.html', 'text/html; charset=utf-8')
        if path == '/setup':
            return self._serve('setup.html', 'text/html; charset=utf-8')
        if path.startswith('/assets/'):
            return self._serve(path[8:], mimetypes.guess_type(path)[0] or 'application/octet-stream')

        public_paths = ('/api/auth/status',)
        if not self.server_app.authenticated(self) and path not in public_paths:
            target = '/setup' if self.server_app.is_first_run() else '/login'
            self.send_response(302)
            self.send_header('Location', target)
            self.end_headers()
            return

        if path == '/':
            return self._serve('index.html', 'text/html; charset=utf-8')

        try:
            if path == '/api/auth/status':
                return self._json({
                    'authenticated': self.server_app.authenticated(self),
                    'setup_required': self.server_app.is_first_run(),
                    'auth_enabled': bool(self.server_app.cfg().get('web_auth_enabled', True)),
                })
            if path == '/api/info':
                return self._json(self.server_app.info())
            if path == '/api/settings':
                return self._json(self.server_app.safe_settings())
            if path == '/api/streams':
                return self._json([
                    {'id': s.id, 'name': s.name, 'url': s.camera['url'], **s.status()}
                    for s in self.server_app.streams.values()
                ])
            if path == '/api/events':
                day = (query.get('date') or [datetime.now().strftime('%Y-%m-%d')])[0]
                camera_id = (query.get('camera') or [''])[0]
                return self._json(self.server_app.store.list_day(day, camera_id))
            if path == '/api/recordings':
                return self._json(self.recordings(query))
            if path.startswith('/live/') and path.endswith('.mjpg'):
                stream_id = urllib.parse.unquote(path[6:-5])
                stream = self.server_app.streams.get(stream_id)
                if not stream:
                    return self._error(404, 'Stream not found')
                return stream.mjpeg(self)
            if path.startswith('/api/snapshot/'):
                sid = urllib.parse.unquote(path.rsplit('/', 1)[-1])
                stream = self.server_app.streams.get(sid)
                if not stream or not stream.get_frame():
                    return self._error(404, 'No frame available')
                frame = stream.get_frame()
                self.send_response(200)
                self.send_header('Content-Type', 'image/jpeg')
                self.send_header('Content-Length', str(len(frame)))
                self.end_headers()
                self.wfile.write(frame)
                return
            if path == '/api/timeline':
                day = (query.get('date') or [datetime.now().strftime('%Y-%m-%d')])[0]
                camera = (query.get('camera') or [''])[0]
                return self._json(self.timeline(day, camera))
            if path == '/api/download':
                return self.server_app_download(query)
            if path == '/api/media':
                return self.server_app_media(query)
            if path.startswith('/api/event-snapshot/'):
                rel = urllib.parse.unquote(path[len('/api/event-snapshot/'):])
                root = Path(self.server_app.cfg().get('snapshot_root', 'G:/LocalCam/snapshots'))
                candidate = safe_join(root, rel)
                if not candidate or not candidate.is_file():
                    return self._error(404, 'Snapshot not found')
                return self._send_file(candidate, 'image/jpeg')
            if path == '/api/health':
                requested = (query.get('camera') or [''])[0]
                result = {}
                for stream in self.server_app.streams.values():
                    if requested and stream.id != requested:
                        continue
                    result[stream.id] = stream.status() | {
                        'probe': test_rtsp(
                            self.server_app.cfg()['ffmpeg_path'],
                            stream.camera['url'],
                            stream.camera.get('username', ''),
                            stream.camera.get('password', ''),
                            3,
                        )
                    }
                return self._json(result)
            return self._error(404, 'Not found')
        except Exception as exc:
            self.server_app.log(f'Unhandled GET error: {exc}')
            return self._error(500, str(exc))

    def do_POST(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path.rstrip('/') or '/'

        if path == '/api/auth/login':
            payload = self._body()
            if self.server_app.login(str(payload.get('password', ''))):
                sid = self.server_app.create_session()
                self.send_response(200)
                self.send_header('Set-Cookie', f'localcam_session={sid}; HttpOnly; SameSite=Strict; Max-Age=43200')
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"ok":true}')
                return
            return self._error(401, 'Invalid password')

        if path == '/api/auth/setup':
            payload = self._body()
            password = str(payload.get('password', ''))
            if len(password) < 8:
                return self._error(400, 'Password must be at least 8 characters')
            cfg = self.server_app.cfg()
            cfg['web_password_hash'] = hash_password(password)
            cfg['web_auth_enabled'] = True
            save_config(cfg)
            sid = self.server_app.create_session()
            self.send_response(200)
            self.send_header('Set-Cookie', f'localcam_session={sid}; HttpOnly; SameSite=Strict; Max-Age=43200')
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
            return

        if path == '/api/auth/logout':
            cookie = http.cookies.SimpleCookie(self.headers.get('Cookie', ''))
            sid = cookie.get('localcam_session')
            if sid:
                self.server_app.sessions.pop(sid.value, None)
            self.send_response(204)
            self.send_header('Set-Cookie', 'localcam_session=; HttpOnly; SameSite=Strict; Max-Age=0')
            self.end_headers()
            return

        if not self.server_app.authenticated(self):
            return self._error(401, 'Authentication required')

        try:
            if path == '/api/settings':
                return self._json(self.server_app.save_settings(self._body()))
            if path.startswith('/api/record/'):
                sid = urllib.parse.unquote(path[len('/api/record/'):])
                stream = self.server_app.streams.get(sid)
                if not stream:
                    return self._error(404, 'Stream not found')
                if path.endswith('/start'):
                    return self._json({'ok': stream.start_recording()})
                if path.endswith('/stop'):
                    stream.stop_recording()
                    return self._json({'ok': True})
            if path.startswith('/api/events/') and path.endswith('/ack'):
                event_id = path.split('/')[3]
                self.server_app.store.acknowledge(int(event_id))
                return self._json({'ok': True})
            return self._error(404, 'Not found')
        except Exception as exc:
            return self._error(400, str(exc))

    def recordings(self, query: dict[str, list[str]]) -> list[dict[str, Any]]:
        root = Path(self.server_app.cfg().get('record_root', 'G:/LocalCam/recordings'))
        day = (query.get('date') or [datetime.now().strftime('%Y-%m-%d')])[0]
        camera = (query.get('camera') or [''])[0]
        search = (query.get('q') or [''])[0].lower()
        if not root.exists():
            return []

        rows = []
        for cam_dir in root.iterdir():
            if not cam_dir.is_dir():
                continue
            if camera and cam_dir.name not in (safe_name(camera), camera):
                continue
            day_dir = cam_dir / day
            if not day_dir.exists():
                continue
            for path in day_dir.glob('*.mkv'):
                if search and search not in path.name.lower():
                    continue
                try:
                    stat = path.stat()
                except OSError:
                    continue
                rows.append({
                    'id': str(path.relative_to(root)).replace(os.sep, '/'),
                    'camera': cam_dir.name,
                    'name': path.name,
                    'timestamp': stat.st_mtime,
                    'time': datetime.fromtimestamp(stat.st_mtime).strftime('%H:%M:%S'),
                    'size': stat.st_size,
                    'size_human': human_bytes(stat.st_size),
                })
        return sorted(rows, key=lambda x: x['timestamp'], reverse=True)[:2000]

    def timeline(self, day: str, camera: str) -> list[dict[str, Any]]:
        rows = self.recordings({'date': [day], 'camera': [camera], 'q': ['']})
        duration = int(self.server_app.cfg().get('segment_minutes', 10))
        return [
            {
                'id': row['id'],
                'camera': row['camera'],
                'start': datetime.fromtimestamp(row['timestamp']).isoformat(),
                'end': (datetime.fromtimestamp(row['timestamp']) + timedelta(minutes=duration)).isoformat(),
                'timestamp': row['timestamp'],
            }
            for row in rows
        ]

    def server_app_download(self, query):
        rel = (query.get('path') or [''])[0]
        root = Path(self.server_app.cfg().get('record_root', 'G:/LocalCam/recordings'))
        candidate = safe_join(root, rel)
        if not candidate or not candidate.is_file():
            return self._error(404, 'File not found')
        return self._send_file(candidate, mimetypes.guess_type(candidate.name)[0] or 'application/octet-stream', attachment=candidate.name)

    def server_app_media(self, query):
        rel = (query.get('path') or [''])[0]
        root = Path(self.server_app.cfg().get('record_root', 'G:/LocalCam/recordings'))
        candidate = safe_join(root, rel)
        if not candidate or not candidate.is_file():
            return self._error(404, 'File not found')

        ffmpeg = self.server_app.cfg().get('ffmpeg_path', 'ffmpeg')
        cmd = [
            ffmpeg,
            '-hide_banner',
            '-loglevel',
            'error',
            '-i',
            str(candidate),
            '-map',
            '0:v:0?',
            '-map',
            '0:a:0?',
            '-c:v',
            'libx264',
            '-preset',
            'veryfast',
            '-c:a',
            'aac',
            '-movflags',
            '+frag_keyframe+empty_moov+default_base_moof',
            '-f',
            'mp4',
            'pipe:1',
        ]
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
        except OSError as exc:
            return self._error(500, str(exc))

        self.send_response(200)
        self.send_header('Content-Type', 'video/mp4')
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        try:
            while proc.stdout:
                chunk = proc.stdout.read(64 * 1024)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            try:
                proc.kill()
            except OSError:
                pass
        return None

    def _body(self) -> dict[str, Any]:
        length = min(int(self.headers.get('Content-Length', '0')), 1_000_000)
        raw = self.rfile.read(length) if length else b'{}'
        obj = json.loads(raw.decode('utf-8'))
        return obj if isinstance(obj, dict) else {}

    def _serve(self, filename: str, content_type: str) -> None:
        path = (WEB_DIR / filename).resolve()
        if WEB_DIR.resolve() not in path.parents and path != WEB_DIR.resolve():
            return self._error(404, 'Not found')
        try:
            data = path.read_bytes()
        except OSError:
            return self._error(404, 'Not found')
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json(self, payload: Any) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(raw)

    def _error(self, status: int, message: str):
        raw = json.dumps({'error': message}).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _send_file(self, path: Path, content_type: str, attachment: str = ''):
        try:
            size = path.stat().st_size
        except OSError:
            return self._error(404, 'File not found')
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(size))
        if attachment:
            safe_attachment = attachment.replace('"', '_')
            self.send_header('Content-Disposition', f'attachment; filename="{safe_attachment}"')
        self.end_headers()
        with path.open('rb') as fh:
            shutil.copyfileobj(fh, self.wfile, 64 * 1024)
