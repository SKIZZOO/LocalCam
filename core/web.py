from __future__ import annotations

import base64
import http.cookies
import ipaddress
import json
import mimetypes
import os
import re
import tempfile
import shutil
import socket
import subprocess
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

from core.config import hash_password, verify_password
from core.nvr import LocalCamServer, safe_name, human_bytes
from core.rtsp import discover_and_test_rtsp, test_rtsp

WEB_DIR = Path(__file__).resolve().parent.parent / 'web'


_NEW_RECORDING_RE = re.compile(
    r'(?P<stamp>\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2})-(?P<camera>.+)-(?P<reason>CONTINUOUS|MOTION|MANUAL)\.mkv$',
    re.IGNORECASE,
)
_OLD_RECORDING_RE = re.compile(
    r'(?P<camera>.+)_(?P<stamp>\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})_(?P<reason>Continuous|Motion|Manual)\.mkv$',
    re.IGNORECASE,
)


def recording_meta(name: str, fallback_time: datetime | None = None) -> dict[str, Any]:
    match = _NEW_RECORDING_RE.match(name)
    new_format = bool(match)
    if not match:
        match = _OLD_RECORDING_RE.match(name)
    if match:
        stamp = match.group('stamp')
        fmt = '%Y-%m-%d-%H-%M-%S' if new_format else '%Y-%m-%d_%H-%M-%S'
        try:
            start = datetime.strptime(stamp, fmt)
        except ValueError:
            start = fallback_time or datetime.fromtimestamp(0)
        reason = match.group('reason').upper()
        camera = match.group('camera')
        if new_format:
            camera = camera.replace('-', ' ')
        return {'start': start, 'camera': camera, 'reason': reason}
    return {
        'start': fallback_time or datetime.fromtimestamp(0),
        'camera': '',
        'reason': 'UNKNOWN',
    }


def safe_join(root: Path, rel: str) -> Path | None:
    try:
        base = root.resolve()
        target = (base / urllib.parse.unquote(rel.lstrip('/'))).resolve()
        target.relative_to(base)
        return target
    except (ValueError, OSError):
        return None


class LocalCamHandler(BaseHTTPRequestHandler):
    server_app: LocalCamServer

    def log_message(self, fmt, *args):
        # Keep the console focused on meaningful activity. Successful page/API
        # requests are routine and are intentionally not printed.
        message = fmt % args
        parts = message.split(' ')
        status = None
        if len(parts) >= 2:
            try:
                status = int(parts[-2])
            except (TypeError, ValueError):
                pass
        if status is not None and status < 400:
            return
        self.server_app.log('WEB ' + message)

    def _origin_ok(self):
        origin = self.headers.get('Origin', '')
        return not origin or urllib.parse.urlsplit(origin).netloc == self.headers.get('Host', '')

    def _body(self, max_bytes=1_000_000):
        try:
            length = min(int(self.headers.get('Content-Length', '0')), max_bytes)
            raw = self.rfile.read(length) if length else b'{}'
            obj = json.loads(raw.decode('utf-8'))
            return obj if isinstance(obj, dict) else {}
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            return {}

    def _json(self, payload: Any, status=200, headers=None):
        raw = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        try:
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Cache-Control', 'no-store')
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            # Browsers can cancel requests while navigating, refreshing, or
            # closing a tab. Do not turn a normal disconnect into a traceback.
            return None

    def _error(self, status, message):
        return self._json({'error': message}, status)

    def _serve(self, name, ctype='application/octet-stream'):
        path = safe_join(WEB_DIR, name)
        if not path or not path.is_file():
            return self._error(404, 'Not found')
        try:
            data = path.read_bytes()
        except OSError:
            return self._error(404, 'Not found')
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        self.wfile.write(data)

    def _send_file(self, path, ctype, attachment=''):
        try:
            size = path.stat().st_size
        except OSError:
            return self._error(404, 'File not found')
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(size))
        self.send_header('Cache-Control', 'no-store')
        if attachment:
            self.send_header('Content-Disposition', f'attachment; filename="{attachment}"')
        self.end_headers()
        with path.open('rb') as fh:
            shutil.copyfileobj(fh, self.wfile, 64 * 1024)

    def _send_bytes(self, data, ctype, filename=''):
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        if filename:
            self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(data)

    def _logout(self):
        cookie = http.cookies.SimpleCookie(self.headers.get('Cookie', ''))
        sid = cookie.get('localcam_session')
        if sid:
            self.server_app.sessions.pop(sid.value, None)
        self.send_response(204)
        self.send_header('Set-Cookie', 'localcam_session=; Max-Age=0; Path=/; HttpOnly; SameSite=Strict')
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path.rstrip('/') or '/'
        q = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)

        if path in ('/login', '/setup'):
            return self._serve('login.html' if path == '/login' else 'setup.html', 'text/html; charset=utf-8')

        if path.startswith('/assets/'):
            return self._serve(path[1:], mimetypes.guess_type(path)[0] or 'application/octet-stream')

        if not self.server_app.session(self) and path not in ('/api/auth/status', '/api/auth/login', '/api/auth/setup'):
            self.send_response(302)
            self.send_header('Location', '/setup' if self.server_app.is_first_run() else '/login')
            self.end_headers()
            return

        try:
            if path == '/':
                return self._serve('index.html', 'text/html; charset=utf-8')
            if path == '/api/auth/status':
                user = self.server_app.session(self)
                return self._json({
                    'authenticated': bool(user),
                    'setup_required': self.server_app.is_first_run(),
                    'auth_enabled': bool(self.server_app.cfg().get('web_auth_enabled', True)),
                    'user': {'id': user['user_id'], 'username': user['username'], 'role': user['role']} if user else None,
                })
            if path == '/api/info':
                return self._json(self.server_app.info())
            if path == '/api/settings':
                if not self.server_app.role(self, 'admin'):
                    return self._error(403, 'Admin role required')
                return self._json(self.server_app.safe_settings())
            if path == '/api/folder-picker':
                if not self.server_app.role(self, 'admin'):
                    return self._error(403, 'Admin role required')
                kind = (q.get('kind') or ['record'])[0]
                if kind not in ('record', 'snapshot'):
                    return self._error(400, 'Unknown folder type')
                current = (q.get('current') or [''])[0].strip()
                try:
                    from tkinter import Tk, filedialog
                    initial = Path(current).expanduser()
                    if not initial.is_absolute():
                        initial = self.server_app.base_dir / initial
                    if not initial.is_dir():
                        initial = self.server_app.base_dir
                    root = Tk()
                    root.withdraw()
                    try:
                        root.attributes('-topmost', True)
                        root.update()
                        selected = filedialog.askdirectory(
                            parent=root,
                            title='Choose LocalCam folder',
                            initialdir=str(initial),
                            mustexist=False,
                        )
                    finally:
                        root.destroy()
                    return self._json({'cancelled': not bool(selected), 'path': selected or ''})
                except Exception as exc:
                    return self._error(500, f'Windows folder picker is unavailable: {exc}')
            if path == '/api/streams':
                return self._json([
                    {'id': s.id, 'name': s.name, 'url': s.camera['url'], 'ptz_enabled': bool((s.camera.get('ptz') or {}).get('enabled')), **s.status()}
                    for s in self.server_app.streams.values()
                ])
            if path == '/api/events':
                day = (q.get('date') or [datetime.now().strftime('%Y-%m-%d')])[0]
                camera = (q.get('camera') or [''])[0]
                return self._json(self.server_app.store.list_day(day, camera))
            if path == '/api/users':
                if not self.server_app.role(self, 'admin'):
                    return self._error(403, 'Admin role required')
                return self._json(self.server_app.store.list_users())
            if path == '/api/recordings':
                return self._json(self.recordings(q))
            if path.startswith('/live/') and path.endswith('.mjpg'):
                sid = urllib.parse.unquote(path[6:-5])
                stream = self.server_app.streams.get(sid)
                if not stream:
                    return self._error(404, 'Stream not found')
                quality = (q.get('quality') or ['high'])[0].lower()
                if quality not in ('low', 'medium', 'high', 'ultra'):
                    quality = 'high'
                return stream.mjpeg(self, quality=quality)
            if path.startswith('/api/snapshot/'):
                sid = urllib.parse.unquote(path.rsplit('/', 1)[-1])
                stream = self.server_app.streams.get(sid)
                frame = stream.get_frame() if stream else None
                if not frame:
                    return self._error(404, 'No frame available')
                return self._send_bytes(frame, 'image/jpeg')
            if path == '/api/timeline':
                day = (q.get('date') or [datetime.now().strftime('%Y-%m-%d')])[0]
                camera = (q.get('camera') or [''])[0]
                return self._json(self.timeline(day, camera))
            if path == '/api/download':
                root = Path(self.server_app.cfg().get('record_root', str(self.server_app.base_dir / 'recordings')))
                rel = (q.get('path') or [''])[0]
                target = safe_join(root, rel)
                return self._send_file(target, mimetypes.guess_type(target.name)[0] or 'application/octet-stream', target.name) if target and target.is_file() else self._error(404, 'File not found')
            if path == '/api/media':
                return self.media(q)
            if path.startswith('/live/') and (path.endswith('.audio.ogg') or path.endswith('.audio.mp4')):
                suffix = '.audio.ogg' if path.endswith('.audio.ogg') else '.audio.mp4'
                sid = urllib.parse.unquote(path[6:-len(suffix)])
                stream = self.server_app.streams.get(sid)
                return stream.live_audio(self) if stream else self._error(404, 'Stream not found')
            if path.startswith('/api/event-snapshot/'):
                root = Path(self.server_app.cfg().get('snapshot_root', str(self.server_app.base_dir / 'snapshots')))
                target = safe_join(root, path[len('/api/event-snapshot/'):])
                return self._send_file(target, 'image/jpeg') if target and target.is_file() else self._error(404, 'Snapshot not found')
            if path == '/api/health':
                wanted = (q.get('camera') or [''])[0]
                out = {}
                cfg = self.server_app.cfg()
                for stream in self.server_app.streams.values():
                    if wanted and wanted != stream.id:
                        continue
                    out[stream.id] = stream.status() | {
                        'probe': test_rtsp(cfg['ffmpeg_path'], stream.camera['url'], stream.camera.get('username', ''), stream.camera.get('password', ''), 4)
                    }
                return self._json(out)
            if path == '/api/backup':
                if not self.server_app.role(self, 'admin'):
                    return self._error(403, 'Admin role required')
                return self._send_bytes(self.server_app.backup(), 'application/zip', f'LocalCam-backup-{datetime.now():%Y%m%d-%H%M%S}.zip')
            if path == '/api/ptz/test':
                sid = (q.get('camera') or [''])[0]
                stream = self.server_app.streams.get(sid)
                if not stream:
                    return self._error(404, 'Camera not found')
                if not self.server_app.role(self, 'admin', 'operator'):
                    return self._error(403, 'Operator role required')
                return self._json(self.server_app.ptz.test(sid, stream.camera))
            return self._error(404, 'Not found')
        except Exception as exc:
            self.server_app.log(f'GET error: {exc}')
            return self._error(500, str(exc))

    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path.rstrip('/') or '/'
        if not self._origin_ok():
            return self._error(403, 'Origin check failed')

        if path == '/api/auth/login':
            x = self._body()
            try:
                user = self.server_app.authenticate(str(x.get('username', '')).strip(), str(x.get('password', '')), self.client_address[0])
            except ValueError as exc:
                return self._error(429, str(exc))
            if not user:
                return self._error(401, 'Invalid username or password')
            sid = self.server_app.new_session(user)
            max_age = int(self.server_app.cfg().get('web_session_hours', 12)) * 3600
            return self._json({'ok': True, 'user': user}, headers={'Set-Cookie': f'localcam_session={sid}; HttpOnly; Path=/; SameSite=Strict; Max-Age={max_age}'})

        if path == '/api/auth/setup':
            if not self.server_app.is_first_run():
                return self._error(409, 'Initial setup is already complete')
            x = self._body()
            username = str(x.get('username', 'admin')).strip()
            password = str(x.get('password', ''))
            if len(username) < 3 or len(password) < 10:
                return self._error(400, 'Username must be 3+ characters and password must be 10+ characters')
            uid = self.server_app.store.create_user(username, hash_password(password), 'admin')
            sid = self.server_app.new_session({'user_id': uid, 'username': username, 'role': 'admin'})
            return self._json({'ok': True}, headers={'Set-Cookie': f'localcam_session={sid}; HttpOnly; Path=/; SameSite=Strict; Max-Age=43200'})

        if not self.server_app.session(self):
            return self._error(401, 'Authentication required')

        try:
            if path == '/api/auth/logout':
                return self._logout()
            if path == '/api/camera-discovery':
                if not self.server_app.role(self, 'admin'):
                    return self._error(403, 'Admin role required')
                x = self._body()
                try:
                    network = ipaddress.ip_network(str(x.get('subnet', '')).strip(), strict=False)
                    if network.version != 4 or not network.is_private or network.num_addresses > 256:
                        return self._error(400, 'Enter a private IPv4 subnet with no more than 256 addresses (for example, 192.168.1.0/24).')
                    hosts = list(network.hosts())
                    ports = (554, 8554, 10554)
                    targets = [(str(host), port) for host in hosts for port in ports]

                    def probe(target):
                        host, port = target
                        try:
                            with socket.create_connection((host, port), timeout=0.35):
                                return {'host': host, 'port': port}
                        except OSError:
                            return None

                    found = []
                    with ThreadPoolExecutor(max_workers=48) as pool:
                        futures = [pool.submit(probe, target) for target in targets]
                        for future in as_completed(futures):
                            result = future.result()
                            if result:
                                found.append(result)
                    found.sort(key=lambda row: (ipaddress.ip_address(row['host']), row['port']))
                    return self._json({'results': found, 'scanned_addresses': len(hosts), 'ports': list(ports)})
                except ValueError:
                    return self._error(400, 'Enter a valid private IPv4 subnet, such as 192.168.1.0/24.')
            if path == '/api/camera-assist':
                if not self.server_app.role(self, 'admin'):
                    return self._error(403, 'Admin role required')
                x = self._body(100_000)
                camera_id = str(x.get('camera_id', '')).strip()
                url = str(x.get('url', '')).strip()
                username = str(x.get('username', '')).strip()
                password = str(x.get('password', ''))
                saved = next((c for c in self.server_app.cfg().get('cameras', []) if str(c.get('id', '')) == camera_id), None)
                if saved:
                    url = url or str(saved.get('url', ''))
                    username = username or str(saved.get('username', ''))
                    if not password:
                        password = str(saved.get('password', ''))
                if not url:
                    return self._error(400, 'Camera RTSP URL is required.')
                result = discover_and_test_rtsp(self.server_app.cfg()['ffmpeg_path'], url, username, password, 2)
                return self._json(result)
            if path == '/api/settings':
                if not self.server_app.role(self, 'admin'):
                    return self._error(403, 'Admin role required')
                return self._json(self.server_app.save_settings(self._body()))
            if path.startswith('/api/webrtc/offer/'):
                if not self.server_app.webrtc.available:
                    return self._error(503, self.server_app.webrtc.error or 'WebRTC is unavailable. Install the LocalCam WebRTC dependencies.')
                sid = urllib.parse.unquote(path[len('/api/webrtc/offer/'):])
                stream = self.server_app.streams.get(sid)
                if not stream:
                    return self._error(404, 'Camera not found')
                x = self._body(2_000_000)
                offer_type = str(x.get('type', 'offer')).strip() or 'offer'
                offer_sdp = str(x.get('sdp', ''))
                peer_id = str(x.get('peer_id', '')).strip()
                quality = str(x.get('quality', 'high')).lower()
                if not offer_sdp:
                    return self._error(400, 'WebRTC SDP offer is required.')
                if not peer_id or len(peer_id) > 128:
                    return self._error(400, 'Invalid WebRTC peer id.')
                if quality not in ('low', 'medium', 'high', 'ultra'):
                    quality = 'high'
                try:
                    result = self.server_app.webrtc.offer(
                        peer_id, stream, offer_type, offer_sdp, quality
                    )
                    return self._json(result)
                except Exception as exc:
                    self.server_app.log(f'{stream.name}: WebRTC offer failed: {exc}')
                    return self._error(502, f'WebRTC connection failed: {exc}')
            if path == '/api/clip':
                if not self.server_app.role(self, 'admin', 'operator'):
                    return self._error(403, 'Operator role required')
                x = self._body(2_000_000)
                root = Path(self.server_app.cfg().get('record_root', str(self.server_app.base_dir / 'recordings')))
                source_rel = str(x.get('path', '')).strip()
                target = safe_join(root, source_rel)
                if not target or not target.is_file():
                    return self._error(404, 'Recording not found')
                try:
                    start = max(0.0, float(x.get('start', 0)))
                    end = float(x.get('end', 0))
                except (TypeError, ValueError):
                    return self._error(400, 'Invalid clip range.')
                if end <= start:
                    return self._error(400, 'Clip end must be after clip start.')
                if end - start > 3600:
                    return self._error(400, 'Clips are limited to one hour.')
                meta = recording_meta(target.name, datetime.fromtimestamp(target.stat().st_mtime))
                stamp = (meta['start'] + timedelta(seconds=start)).strftime('%Y-%m-%d-%H-%M-%S')
                camera_token = re.sub(r'[^A-Za-z0-9]+', '-', meta['camera']).strip('-') or 'Camera'
                output_dir = root / 'Exports'
                output_dir.mkdir(parents=True, exist_ok=True)
                output = output_dir / f'{stamp}-{camera_token}-EDITED.mp4'
                ffmpeg = self.server_app.cfg().get('ffmpeg_path', 'ffmpeg')
                cmd = [
                    ffmpeg, '-hide_banner', '-loglevel', 'error',
                    '-ss', str(start), '-i', str(target), '-t', str(end - start),
                    '-map', '0:v:0?', '-map', '0:a:0?',
                    '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '22',
                    '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '128k',
                    '-movflags', '+faststart', str(output),
                ]
                try:
                    completed = subprocess.run(
                        cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                        text=True, encoding='utf-8', errors='replace',
                        timeout=3700,
                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                    )
                except (OSError, subprocess.TimeoutExpired) as exc:
                    try: output.unlink()
                    except OSError: pass
                    return self._error(500, f'Clip export failed: {exc}')
                if completed.returncode != 0 or not output.exists():
                    try: output.unlink()
                    except OSError: pass
                    detail = completed.stderr.strip().splitlines()[-1] if completed.stderr else 'FFmpeg could not export the clip.'
                    return self._error(500, detail)
                rel = str(output.relative_to(root)).replace('\\', '/')
                return self._json({'ok': True, 'path': str(output.resolve()), 'id': rel, 'filename': output.name})
            if path.startswith('/api/talk/'):
                if not self.server_app.role(self, 'admin', 'operator'):
                    return self._error(403, 'Operator role required')
                sid = urllib.parse.unquote(path[len('/api/talk/'):])
                stream = self.server_app.streams.get(sid)
                if not stream:
                    return self._error(404, 'Camera not found')
                x = self._body(12_000_000)
                audio = str(x.get('audio_base64', ''))
                if not audio:
                    return self._error(400, 'Microphone audio is required.')
                try:
                    raw = base64.b64decode(audio, validate=True)
                except Exception:
                    return self._error(400, 'Invalid microphone audio.')
                if len(raw) > 8_000_000:
                    return self._error(413, 'Talk clip is too large.')
                suffix = '.ogg' if 'ogg' in str(x.get('mime', '')).lower() else '.webm'
                fd, temp_name = tempfile.mkstemp(prefix='localcam-talk-', suffix=suffix)
                os.close(fd)
                try:
                    Path(temp_name).write_bytes(raw)
                    result = self.server_app.talk(stream, temp_name, float(x.get('volume', 0.05)))
                    return self._json({'ok': True, 'message': 'Talk audio sent.', 'result': result})
                finally:
                    try: Path(temp_name).unlink()
                    except OSError: pass
            if path.startswith('/api/record/'):
                record_path = path[len('/api/record/'):].strip('/')
                sid, action = record_path.rsplit('/', 1) if '/' in record_path else ('', '')
                sid = urllib.parse.unquote(sid)
                stream = self.server_app.streams.get(sid)
                if not stream:
                    return self._error(404, 'Stream not found')
                if not self.server_app.role(self, 'admin', 'operator'):
                    return self._error(403, 'Operator role required')
                if action == 'start':
                    ok = stream.start_recording(force=True)
                    if not ok:
                        return self._json({'ok': False, 'recording': False, 'error': stream.last_error or 'Recording could not be started.'}, status=500)
                    return self._json({'ok': True, 'recording': True})
                if action == 'stop':
                    stream.stop_recording()
                    return self._json({'ok': True, 'recording': False})
                if action == 'toggle':
                    if stream.recorder and stream.recorder.running:
                        stream.stop_recording()
                        return self._json({'ok': True, 'recording': False})
                    ok = stream.start_recording(force=True)
                    if not ok:
                        return self._json({'ok': False, 'recording': False, 'error': stream.last_error or 'Recording could not be started.'}, status=500)
                    return self._json({'ok': True, 'recording': True})
            if path.startswith('/api/ptz/'):
                sid = urllib.parse.unquote(path[len('/api/ptz/'):])
                stream = self.server_app.streams.get(sid)
                if not stream:
                    return self._error(404, 'Camera not found')
                if not self.server_app.role(self, 'admin', 'operator'):
                    return self._error(403, 'Operator role required')
                if path.endswith('/move'):
                    x = self._body()
                    self.server_app.ptz.move(sid, stream.camera, float(x.get('pan', 0)), float(x.get('tilt', 0)), float(x.get('zoom', 0)), float(x.get('seconds', .35)))
                    return self._json({'ok': True})
                if path.endswith('/stop'):
                    self.server_app.ptz.stop(sid, stream.camera)
                    return self._json({'ok': True})
                if path.endswith('/home'):
                    self.server_app.ptz.home(sid, stream.camera)
                    return self._json({'ok': True})
            if path == '/api/admin/restore':
                if not self.server_app.role(self, 'admin'):
                    return self._error(403, 'Admin role required')
                x = self._body(55_000_000)
                raw = base64.b64decode(str(x.get('archive_base64', '')), validate=True)
                self.server_app.restore(raw)
                return self._json({'ok': True, 'message': 'Backup restored. Restart LocalCam if needed.'})
            if path.startswith('/api/events/') and path.endswith('/ack'):
                if not self.server_app.role(self, 'admin', 'operator'):
                    return self._error(403, 'Operator role required')
                self.server_app.store.acknowledge(int(path.split('/')[-2]))
                return self._json({'ok': True})
            if path == '/api/users':
                if not self.server_app.role(self, 'admin'):
                    return self._error(403, 'Admin role required')
                x = self._body()
                password = str(x.get('password', ''))
                if len(password) < 10:
                    return self._error(400, 'Password must be at least 10 characters')
                uid = self.server_app.store.create_user(str(x.get('username', '')).strip(), hash_password(password), str(x.get('role', 'viewer')))
                return self._json({'ok': True, 'id': uid})
            return self._error(404, 'Not found')
        except Exception as exc:
            self.server_app.log(f'POST error: {exc}')
            return self._error(400, str(exc))

    def do_PUT(self):
        if not self._origin_ok() or not self.server_app.role(self, 'admin'):
            return self._error(403, 'Admin role required')
        path = urllib.parse.urlsplit(self.path).path.rstrip('/')
        try:
            uid = int(path.rsplit('/', 1)[-1])
            x = self._body()
            self.server_app.store.update_user(
                uid,
                role=str(x['role']) if x.get('role') else None,
                enabled=bool(x['enabled']) if 'enabled' in x else None,
                password_hash=hash_password(str(x['password'])) if x.get('password') else None,
            )
            return self._json({'ok': True})
        except Exception as exc:
            return self._error(400, str(exc))

    def do_DELETE(self):
        if not self._origin_ok() or not self.server_app.role(self, 'admin'):
            return self._error(403, 'Admin role required')
        try:
            uid = int(urllib.parse.urlsplit(self.path).path.rstrip('/').rsplit('/', 1)[-1])
            current = self.server_app.session(self)
            if current and int(current['user_id']) == uid:
                return self._error(400, 'You cannot delete your own account')
            if self.server_app.store.user_count() <= 1:
                return self._error(400, 'At least one user must remain')
            self.server_app.store.delete_user(uid)
            return self._json({'ok': True})
        except Exception as exc:
            return self._error(400, str(exc))

    def recordings(self, q):
        day = (q.get('date') or [''])[0]
        camera = (q.get('camera') or [''])[0]
        reason_filter = (q.get('reason') or [''])[0].upper()
        query = (q.get('q') or [''])[0].lower().strip()
        from_value = (q.get('from') or [''])[0]
        to_value = (q.get('to') or [''])[0]
        root = Path(self.server_app.cfg().get('record_root', str(self.server_app.base_dir / 'recordings')))
        if not root.exists():
            return []

        def parse_filter(value):
            if not value:
                return None
            try:
                return datetime.fromisoformat(value)
            except ValueError:
                return None

        from_dt = parse_filter(from_value)
        to_dt = parse_filter(to_value)
        results = []
        for path in root.rglob('*.mkv'):
            try:
                rel = path.relative_to(root)
                stat = path.stat()
            except (ValueError, OSError):
                continue
            meta = recording_meta(path.name, datetime.fromtimestamp(stat.st_mtime))
            start = meta['start']
            cam = meta['camera'] or (rel.parts[0] if len(rel.parts) >= 3 else path.parent.name)
            reason = meta['reason']

            if day and start.strftime('%Y-%m-%d') != day:
                continue
            if camera and camera not in (cam, safe_name(camera), cam.replace(' ', '-')):
                continue
            if reason_filter and reason != reason_filter:
                continue
            if from_dt and start < from_dt:
                continue
            if to_dt and start > to_dt:
                continue

            name = path.name
            searchable = f'{name} {cam} {reason} {path.as_posix()}'.lower()
            if query and query not in searchable:
                continue

            results.append({
                'id': str(rel).replace('\\', '/'),
                'camera': cam,
                'reason': reason,
                'name': name,
                'size': stat.st_size,
                'size_human': human_bytes(stat.st_size),
                'time': start.strftime('%H:%M:%S'),
                'start': start.isoformat(),
                'path': str(path.resolve()),
            })
        results.sort(key=lambda row: row.get('start', ''), reverse=True)
        return results

    def timeline(self, day, camera='', reason=''):
        rows = self.recordings({'date': [day], 'camera': [camera], 'reason': [reason] if reason else ['']})
        segments = []
        minutes = max(1, int(self.server_app.cfg().get('segment_minutes', 10)))
        for row in rows:
            dt = datetime.fromisoformat(row['start'])
            end = dt + timedelta(minutes=minutes)
            segments.append({
                'id': row['id'],
                'camera': row['camera'],
                'reason': row['reason'],
                'start': dt.isoformat(),
                'end': end.isoformat(),
                'name': row['name'],
                'path': row['path'],
            })
        return segments

    def media(self, q):
        root = Path(self.server_app.cfg().get('record_root', str(self.server_app.base_dir / 'recordings')))
        target = safe_join(root, (q.get('path') or [''])[0])
        if not target or not target.is_file():
            return self._error(404, 'Media not found')

        # Keep MKV as the recording format, but remux it to fragmented MP4 for
        # browser playback. This makes the Archive player work across browsers
        # without rewriting or degrading the stored recording.
        ffmpeg = self.server_app.cfg().get('ffmpeg_path', 'ffmpeg')
        cmd = [
            ffmpeg,
            '-hide_banner',
            '-loglevel', 'error',
            '-i', str(target),
            '-map', '0:v:0?',
            '-map', '0:a:0?',
            '-c:v', 'libx264',
            '-preset', 'veryfast',
            '-crf', '23',
            '-pix_fmt', 'yuv420p',
            '-c:a', 'aac',
            '-b:a', '128k',
            '-avoid_negative_ts', 'make_zero',
            '-movflags', '+frag_keyframe+empty_moov+default_base_moof',
            '-f', 'mp4',
            'pipe:1',
        ]
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                bufsize=64 * 1024,
            )
        except OSError as exc:
            return self._error(500, f'Could not start archive playback: {exc}')

        self.send_response(200)
        self.send_header('Content-Type', 'video/mp4')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()

        try:
            while True:
                chunk = proc.stdout.read(64 * 1024) if proc.stdout else b''
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            try:
                proc.kill()
            except OSError:
                pass
        finally:
            try:
                if proc.poll() is None:
                    proc.terminate()
            except OSError:
                pass
            proc.wait(timeout=3)

        if proc.returncode not in (0, None) and not getattr(proc, 'returncode', None) in (-15,):
            try:
                err = proc.stderr.read().decode('utf-8', 'replace').strip() if proc.stderr else ''
            except Exception:
                err = ''
            if err:
                self.server_app.log(f'Archive playback failed: {err.splitlines()[-1]}')
