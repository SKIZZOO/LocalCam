from __future__ import annotations

import io
import json
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

from core.config import load_config, save_config, verify_password
from core.db import EventStore
from core.motion import MotionDetector
from core.ptz import PTZController
from core.recorder import Recorder
from core.rtsp import with_credentials

try:
    import psutil
except ImportError:
    psutil = None

APP_VERSION = '0.7.0'
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
    def __init__(self, ffmpeg, url, username, password, width, fps, on_frame):
        self.cmd = [
            ffmpeg, '-hide_banner', '-loglevel', 'error', '-rtsp_transport', 'tcp',
            '-rw_timeout', '15000000', '-i', with_credentials(url, username, password),
            '-an', '-vf', f'scale={width}:-2,fps={fps}', '-q:v', '5', '-f', 'mjpeg', 'pipe:1'
        ]
        self.on_frame = on_frame
        self.proc = None
        self.thread = None
        self.stop_event = threading.Event()

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, daemon=True, name='localcam-preview')
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
            chunk = stream.read(4096)
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
            chunk = stream.read(4096)
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
                time.sleep(3)
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
            time.sleep(1)


class StreamState:
    def __init__(self, camera: dict[str, Any], cfg: dict[str, Any], store: EventStore, logger):
        self.camera = dict(camera)
        self.cfg = cfg
        self.store = store
        self.logger = logger
        self.lock = threading.Condition()
        self.frame = None
        self.seq = 0
        self.last_frame = 0.0
        self.preview = None
        self.recorder = None
        self.motion = None
        self.motion_active = False
        self.event_id = None
        self.last_motion = 0.0
        self.last_error = ''
        self._start()

    @property
    def id(self):
        return str(self.camera.get('id', ''))

    @property
    def name(self):
        return str(self.camera.get('name', self.id))

    def _start(self):
        self.preview = PreviewWorker(
            self.cfg['ffmpeg_path'], self.camera['url'], self.camera.get('username', ''),
            self.camera.get('password', ''), int(self.cfg['web_live_width']),
            int(self.cfg['web_live_fps']), self._frame,
        )
        self.preview.start()
        motion = self.cfg.get('motion', {})
        if motion.get('enabled'):
            self.motion = MotionDetector(
                self.get_frame, self._on_motion,
                motion.get('interval_seconds', 0.5), motion.get('threshold', 8),
                motion.get('min_changed_fraction', 0.012),
            )
            self.motion.start()

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

    def mjpeg(self, handler):
        with self.lock:
            last = self.seq
        handler.send_response(200)
        handler.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=frame')
        handler.send_header('Cache-Control', 'no-store')
        handler.send_header('Connection', 'close')
        handler.end_headers()
        try:
            while self.preview:
                with self.lock:
                    self.lock.wait_for(lambda: self.seq != last or self.preview is None, timeout=4)
                    frame = self.frame
                    last = self.seq
                if frame:
                    payload = (
                        b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: '
                        + str(len(frame)).encode() + b'\r\n\r\n' + frame + b'\r\n'
                    )
                    handler.wfile.write(payload)
                    handler.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def _on_motion(self, active, frame):
        now = time.time()
        motion = self.cfg.get('motion', {})
        if active:
            self.last_motion = now
            if not self.motion_active:
                self.motion_active = True
                snapshot = self.save_snapshot(frame) if motion.get('save_event_snapshots', True) else ''
                self.event_id = self.store.start_event(self.id, self.name, snapshot)
                if self.cfg.get('record_mode') == 'motion':
                    self.start_recording()
            return
        cooldown = float(motion.get('cooldown_seconds', 15))
        if self.motion_active and now - self.last_motion >= cooldown:
            self.motion_active = False
            if self.event_id:
                self.store.end_event(self.event_id)
                self.event_id = None
            if self.cfg.get('record_mode') == 'motion':
                self.stop_recording()

    def save_snapshot(self, frame):
        if not frame:
            return ''
        root = Path(self.cfg.get('snapshot_root', str(BASE_DIR / 'snapshots')))
        target = root / safe_name(self.name) / datetime.now().strftime('%Y-%m-%d')
        target.mkdir(parents=True, exist_ok=True)
        path = target / f'{datetime.now():%Y-%m-%d_%H-%M-%S}.jpg'
        path.write_bytes(frame)
        try:
            return str(path.resolve().relative_to(root.resolve())).replace('\\', '/')
        except ValueError:
            return str(path)

    def start_recording(self):
        if self.recorder and self.recorder.running:
            return True
        self.recorder = Recorder(
            self.cfg['ffmpeg_path'], Path(self.cfg['record_root']),
            int(self.cfg['segment_minutes']), int(self.cfg['min_free_gb']),
            int(self.cfg['max_retention_days']), str(self.camera.get('username', '')),
            str(self.camera.get('password', '')), self.logger,
        )
        ok = self.recorder.start(self.id, self.name, self.camera['url'])
        if not ok:
            self.last_error = 'Recording could not be started. Check FFmpeg and the RTSP URL.'
        return ok

    def stop_recording(self):
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
        }

    def stop(self):
        if self.motion:
            self.motion.stop()
        if self.preview:
            self.preview.stop()
        if self.recorder:
            self.recorder.stop()
        self.preview = None


class LocalCamServer:
    def __init__(self, base_dir: Path = BASE_DIR):
        self.base_dir = Path(base_dir)
        self.lock = threading.RLock()
        self.httpd = None
        self.thread = None
        self.started_at = time.time()
        self.sessions = {}
        self.failures = {}
        self.store = EventStore(self.base_dir / 'localcam.sqlite3')
        cfg = load_config()
        self.store.ensure_legacy_admin(str(cfg.get('web_password_hash', '')))
        self.ptz = PTZController(self.log)
        self.streams = {}
        self.rebuild_streams()

    def log(self, message):
        print(f'[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}', flush=True)

    def cfg(self):
        return load_config()

    def rebuild_streams(self):
        cfg = self.cfg()
        with self.lock:
            for stream in self.streams.values():
                stream.stop()
            self.streams.clear()
            for index, item in enumerate(cfg.get('cameras', [])):
                camera = dict(item)
                camera.setdefault('id', f'camera-{index + 1}')
                camera.setdefault('name', f'Camera {index + 1}')
                camera.setdefault('username', 'admin')
                camera.setdefault('password', '')
                camera.setdefault('ptz', {})
                if camera.get('url'):
                    self.streams[camera['id']] = StreamState(camera, cfg, self.store, self.log)

    def start(self):
        from core.web import LocalCamHandler
        from http.server import ThreadingHTTPServer
        cfg = self.cfg()
        if not cfg.get('web_enabled', True):
            raise RuntimeError('Web server is disabled in Settings.')
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
        threading.Thread(target=self._controller, daemon=True, name='localcam-controller').start()
        threading.Thread(target=self._maintenance, daemon=True, name='localcam-maintenance').start()

    def _controller(self):
        while self.httpd:
            mode = self.cfg().get('record_mode')
            for stream in list(self.streams.values()):
                if mode == 'continuous':
                    stream.start_recording()
                elif mode != 'motion':
                    stream.stop_recording()
            time.sleep(5)

    def _maintenance(self):
        while self.httpd:
            try:
                self.cleanup()
                self.expire_sessions()
            except Exception as exc:
                self.log(f'Maintenance error: {exc}')
            time.sleep(60)

    def cleanup(self):
        cfg = self.cfg()
        retention = int(cfg.get('max_retention_days', 30))
        snapshot_root = Path(cfg.get('snapshot_root', str(self.base_dir / 'snapshots')))
        if retention > 0 and snapshot_root.exists():
            cutoff = time.time() - retention * 86400
            for path in snapshot_root.rglob('*.jpg'):
                try:
                    if path.stat().st_mtime < cutoff:
                        path.unlink()
                except OSError:
                    pass

        record_root = Path(cfg.get('record_root', str(self.base_dir / 'recordings')))
        if record_root.exists():
            try:
                usage = shutil.disk_usage(record_root)
                minimum = int(cfg.get('min_free_gb', 20)) * 1024**3
            except OSError:
                usage = None
                minimum = 0
            while usage and usage.free < minimum:
                files = sorted(
                    (p for p in record_root.rglob('*.mkv') if p.is_file()),
                    key=lambda p: p.stat().st_mtime,
                )
                if not files:
                    break
                try:
                    files[0].unlink()
                except OSError:
                    break
                usage = shutil.disk_usage(record_root)

        for stream in self.streams.values():
            if not stream.status()['online'] and cfg.get('cameras'):
                if not stream.last_error:
                    stream.last_error = 'Stream offline; waiting for reconnect.'

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
        for stream in list(self.streams.values()):
            stream.stop()
        self.streams.clear()

    def url(self):
        cfg = self.cfg()
        host = '127.0.0.1' if cfg.get('web_bind') == '127.0.0.1' else lan_ip()
        return f'http://{host}:{int(cfg.get("web_port", 8765))}/'

    def is_first_run(self):
        return self.store.user_count() == 0

    def session(self, handler):
        if not self.cfg().get('web_auth_enabled', True):
            return {'user_id': 0, 'username': 'system', 'role': 'admin', 'expires': float('inf')}
        import http.cookies
        cookie = http.cookies.SimpleCookie(handler.headers.get('Cookie', ''))
        sid = cookie.get('localcam_session')
        data = self.sessions.get(sid.value) if sid else None
        return data if data and data['expires'] > time.time() else None

    def role(self, handler, *roles):
        user = self.session(handler)
        return bool(user and user['role'] in roles)

    def authenticate(self, username, password, ip):
        now = time.time()
        recent = [stamp for stamp in self.failures.get(ip, []) if now - stamp < 300]
        if len(recent) >= 8:
            raise ValueError('Too many login attempts. Try again later.')
        user = self.store.get_user_by_username(username)
        if not user or not user['enabled'] or not verify_password(password, user['password_hash']):
            recent.append(now)
            self.failures[ip] = recent
            return None
        self.failures.pop(ip, None)
        self.store.mark_login(user['id'])
        return {'user_id': int(user['id']), 'username': user['username'], 'role': user['role']}

    def new_session(self, user):
        sid = secrets.token_urlsafe(32)
        hours = max(1, int(self.cfg().get('web_session_hours', 12)))
        self.sessions[sid] = {**user, 'expires': time.time() + hours * 3600}
        return sid

    def safe_settings(self):
        cfg = json.loads(json.dumps(self.cfg()))
        cfg['web_password_hash'] = ''
        for camera in cfg.get('cameras', []):
            if camera.get('password'):
                camera['password'] = '********'
            if camera.get('ptz', {}).get('password'):
                camera['ptz']['password'] = '********'
        cfg['users'] = self.store.list_users()
        return cfg

    def save_settings(self, payload):
        cfg = self.cfg()
        allowed = (
            'ffmpeg_path', 'record_root', 'snapshot_root', 'record_mode', 'segment_minutes',
            'min_free_gb', 'max_retention_days', 'web_bind', 'web_port', 'web_live_fps',
            'web_live_width', 'web_enabled', 'web_auto_open', 'web_auth_enabled',
            'notifications_enabled', 'web_session_hours', 'motion'
        )
        for key in allowed:
            if key in payload:
                cfg[key] = payload[key]
        cfg['segment_minutes'] = max(1, int(cfg.get('segment_minutes', 10)))
        cfg['min_free_gb'] = max(1, int(cfg.get('min_free_gb', 20)))
        cfg['max_retention_days'] = max(0, int(cfg.get('max_retention_days', 30)))
        cfg['web_port'] = max(1024, min(65535, int(cfg.get('web_port', 8765))))
        cfg['web_live_fps'] = max(1, min(15, int(cfg.get('web_live_fps', 8))))
        cfg['web_live_width'] = max(320, min(2560, int(cfg.get('web_live_width', 1280))))
        cfg['web_session_hours'] = max(1, min(168, int(cfg.get('web_session_hours', 12))))
        cfg['record_root'] = str(cfg.get('record_root', str(self.base_dir / 'recordings'))).strip()
        cfg['snapshot_root'] = str(cfg.get('snapshot_root', str(self.base_dir / 'snapshots'))).strip()
        cfg['record_mode'] = cfg.get('record_mode') if cfg.get('record_mode') in ('continuous', 'motion', 'manual') else 'continuous'

        old = cfg.get('cameras', [])
        cameras = []
        for index, item in enumerate(payload.get('cameras', old)):
            if not isinstance(item, dict) or not str(item.get('url', '')).strip():
                continue
            previous = old[index] if index < len(old) else {}
            password = item.get('password', '')
            password = previous.get('password', '') if password == '********' else str(password)
            ptz = item.get('ptz') or {}
            previous_ptz = previous.get('ptz') or {}
            ptz_password = ptz.get('password', '')
            ptz_password = previous_ptz.get('password', '') if ptz_password == '********' else str(ptz_password)
            cameras.append({
                'id': str(item.get('id') or f'camera-{index + 1}'),
                'name': str(item.get('name') or f'Camera {index + 1}'),
                'url': str(item['url']).strip(),
                'username': str(item.get('username', 'admin')),
                'password': password,
                'ptz': {
                    'enabled': bool(ptz.get('enabled', False)),
                    'host': str(ptz.get('host', '')),
                    'port': int(ptz.get('port', 80) or 80),
                    'username': str(ptz.get('username', '')),
                    'password': ptz_password,
                },
            })
        cfg['cameras'] = cameras
        save_config(cfg)
        self.rebuild_streams()
        return self.safe_settings()

    def info(self):
        cfg = self.cfg()
        record_root = Path(cfg.get('record_root', str(self.base_dir / 'recordings')))
        drive = record_root if record_root.exists() else record_root.parent
        try:
            usage = shutil.disk_usage(drive)
            storage = {
                'path': str(record_root),
                'free': usage.free,
                'total': usage.total,
                'free_human': human_bytes(usage.free),
                'total_human': human_bytes(usage.total),
                'used_percent': round((usage.total - usage.free) / usage.total * 100, 1) if usage.total else 0,
            }
        except OSError:
            storage = {'path': str(record_root), 'free': 0, 'total': 0, 'free_human': 'Unknown', 'total_human': 'Unknown', 'used_percent': 0}
        system = {
            'cpu_percent': psutil.cpu_percent(interval=None) if psutil else None,
            'memory_percent': psutil.virtual_memory().percent if psutil else None,
            'uptime_seconds': time.time() - self.started_at,
        }
        return {
            'app': 'LocalCam', 'version': APP_VERSION, 'hostname': socket.gethostname(),
            'url': self.url(), 'lan_ip': lan_ip(), 'storage': storage,
            'streams': [stream.status() for stream in self.streams.values()],
            'record_mode': cfg.get('record_mode'), 'system': system,
        }

    def backup(self):
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name in ('config.json', 'localcam.sqlite3'):
                path = self.base_dir / name
                if path.exists():
                    archive.writestr(name, path.read_bytes())
            archive.writestr('manifest.json', json.dumps({
                'format': 1, 'app': 'LocalCam', 'version': APP_VERSION,
                'created_at': datetime.now().isoformat(timespec='seconds'),
            }, indent=2))
        return output.getvalue()

    def restore(self, raw):
        if len(raw) > 50 * 1024 * 1024:
            raise ValueError('Backup is too large.')
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            names = set(archive.namelist())
            allowed = {'config.json', 'localcam.sqlite3', 'manifest.json'}
            if 'config.json' not in names or not names.issubset(allowed):
                raise ValueError('Invalid LocalCam backup archive.')
            cfg = json.loads(archive.read('config.json').decode('utf-8'))
            if not isinstance(cfg, dict):
                raise ValueError('Invalid configuration.')
            for stream in self.streams.values():
                stream.stop()
            self.streams.clear()
            (self.base_dir / 'config.json').write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding='utf-8')
            if 'localcam.sqlite3' in names:
                db = self.base_dir / 'localcam.sqlite3'
                db.unlink(missing_ok=True)
                db.write_bytes(archive.read('localcam.sqlite3'))
            self.store = EventStore(self.base_dir / 'localcam.sqlite3')
            self.sessions.clear()
            self.rebuild_streams()
