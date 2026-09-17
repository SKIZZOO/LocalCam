from __future__ import annotations

import base64
import http.cookies
import json
import mimetypes
import os
import ipaddress
import socket
from concurrent.futures import ThreadPoolExecutor, as_completed
import shutil
import subprocess
import urllib.parse
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

from core.config import hash_password, verify_password
from core.nvr import LocalCamServer, safe_name, human_bytes
from core.rtsp import test_rtsp

WEB_DIR = Path(__file__).resolve().parent.parent / 'web'


def safe_join(root: Path, rel: str) -> Path | None:
    try:
        base = root.resolve(); target = (base / urllib.parse.unquote(rel)).resolve(); target.relative_to(base); return target
    except (ValueError, OSError):
        return None


class LocalCamHandler(BaseHTTPRequestHandler):
    server_app: LocalCamServer

    def log_message(self, fmt, *args):
        self.server_app.log('WEB ' + fmt % args)

    def _origin_ok(self):
        origin = self.headers.get('Origin', '')
        return not origin or urllib.parse.urlsplit(origin).netloc == self.headers.get('Host', '')

    def _body(self, max_bytes=1_000_000):
        length = min(int(self.headers.get('Content-Length', '0')), max_bytes)
        raw = self.rfile.read(length) if length else b'{}'
        obj = json.loads(raw.decode('utf-8'))
        return obj if isinstance(obj, dict) else {}

    def _json(self, payload: Any, status=200, headers=None):
        raw = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status); self.send_header('Content-Type', 'application/json; charset=utf-8'); self.send_header('Content-Length', str(len(raw))); self.send_header('Cache-Control', 'no-store')
        for key, value in (headers or {}).items(): self.send_header(key, value)
        self.end_headers(); self.wfile.write(raw)

    def _error(self, status, message):
        return self._json({'error': message}, status)

    def _serve(self, name, ctype='application/octet-stream'):
        path = (WEB_DIR / name).resolve()
        if WEB_DIR.resolve() not in path.parents: return self._error(404, 'Not found')
        try: data = path.read_bytes()
        except OSError: return self._error(404, 'Not found')
        self.send_response(200); self.send_header('Content-Type', ctype); self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)

    def _send_file(self, path, ctype, attachment=''):
        try: size = path.stat().st_size
        except OSError: return self._error(404, 'File not found')
        self.send_response(200); self.send_header('Content-Type', ctype); self.send_header('Content-Length', str(size))
        if attachment: self.send_header('Content-Disposition', f'attachment; filename="{attachment}"')
        self.end_headers();
        with path.open('rb') as fh: shutil.copyfileobj(fh, self.wfile, 64 * 1024)

    def _send_bytes(self, data, ctype, filename=''):
        self.send_response(200); self.send_header('Content-Type', ctype); self.send_header('Content-Length', str(len(data)))
        if filename: self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
        self.end_headers(); self.wfile.write(data)

    def _logout(self):
        cookie=http.cookies.SimpleCookie(self.headers.get('Cookie','')); sid=cookie.get('localcam_session')
        if sid:self.server_app.sessions.pop(sid.value,None)
        self.send_response(204); self.send_header('Set-Cookie','localcam_session=; Max-Age=0; HttpOnly; SameSite=Strict'); self.end_headers()

    def do_GET(self):
        u=urllib.parse.urlsplit(self.path); path=u.path.rstrip('/') or '/'; q=urllib.parse.parse_qs(u.query,keep_blank_values=True)
        if path in ('/login','/setup'):return self._serve('login.html' if path=='/login' else 'setup.html','text/html; charset=utf-8')
        if path.startswith('/assets/'):return self._serve(path[8:],mimetypes.guess_type(path)[0] or 'application/octet-stream')
        if not self.server_app.session(self) and path not in ('/api/auth/status','/api/auth/login','/api/auth/setup'):
            self.send_response(302);self.send_header('Location','/setup' if self.server_app.is_first_run() else '/login');self.end_headers();return
        try:
            if path=='/':return self._serve('index.html','text/html; charset=utf-8')
            if path=='/api/auth/status':
                user=self.server_app.session(self); return self._json({'authenticated':bool(user),'setup_required':self.server_app.is_first_run(),'auth_enabled':bool(self.server_app.cfg().get('web_auth_enabled',True)),'user':{'id':user['user_id'],'username':user['username'],'role':user['role']} if user else None})
            if path=='/api/info':return self._json(self.server_app.info())
            if path=='/api/settings':return self._json(self.server_app.safe_settings())
            if path=='/api/streams':
                out=[]
                for s in self.server_app.streams.values():out.append({'id':s.id,'name':s.name,'url':s.camera['url'],'ptz_enabled':bool((s.camera.get('ptz') or {}).get('enabled'))}|s.status())
                return self._json(out)
            if path=='/api/events':return self._json(self.server_app.store.list_day((q.get('date') or [datetime.now().strftime('%Y-%m-%d')])[0],(q.get('camera') or [''])[0]))
            if path=='/api/users':
                if not self.server_app.role(self,'admin'):return self._error(403,'Admin role required')
                return self._json(self.server_app.store.list_users())
            if path=='/api/recordings':return self._json(self.recordings(q))
            if path.startswith('/live/') and path.endswith('.mjpg'):
                sid=urllib.parse.unquote(path[6:-5]);s=self.server_app.streams.get(sid);return s.mjpeg(self) if s else self._error(404,'Stream not found')
            if path.startswith('/api/snapshot/'):
                sid=urllib.parse.unquote(path.rsplit('/',1)[-1]);s=self.server_app.streams.get(sid);frame=s.get_frame() if s else None
                if not frame:return self._error(404,'No frame available')
                self.send_response(200);self.send_header('Content-Type','image/jpeg');self.send_header('Content-Length',str(len(frame)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(frame);return
            if path=='/api/timeline':return self._json(self.timeline((q.get('date') or [datetime.now().strftime('%Y-%m-%d')])[0],(q.get('camera') or [''])[0]))
            if path=='/api/download':
                root=Path(self.server_app.cfg().get('record_root','G:/LocalCam/recordings'));p=safe_join(root,(q.get('path') or [''])[0]);return self._send_file(p,mimetypes.guess_type(p.name)[0] or 'application/octet-stream',p.name) if p and p.is_file() else self._error(404,'File not found')
            if path=='/api/media':return self.media(q)
            if path.startswith('/api/event-snapshot/'):
                root=Path(self.server_app.cfg().get('snapshot_root','G:/LocalCam/snapshots'));p=safe_join(root,path[len('/api/event-snapshot/'):]);return self._send_file(p,'image/jpeg') if p and p.is_file() else self._error(404,'Snapshot not found')
            if path=='/api/health':
                wanted=(q.get('camera') or [''])[0];out={}
                for s in self.server_app.streams.values():
                    if wanted and wanted!=s.id:continue
                    out[s.id]=s.status()|{'probe':test_rtsp(self.server_app.cfg()['ffmpeg_path'],s.camera['url'],s.camera.get('username',''),s.camera.get('password',''),4)}
                return self._json(out)
            if path=='/api/backup':
                if not self.server_app.role(self,'admin'):return self._error(403,'Admin role required')
                return self._send_bytes(self.server_app.backup(),'application/zip',f'LocalCam-backup-{datetime.now():%Y%m%d-%H%M%S}.zip')
            if path=='/api/ptz/test':
                sid=(q.get('camera') or [''])[0];s=self.server_app.streams.get(sid)
                if not s:return self._error(404,'Camera not found')
                if not self.server_app.role(self,'admin','operator'):return self._error(403,'Operator role required')
                return self._json(self.server_app.ptz.test(sid,s.camera))
            return self._error(404,'Not found')
        except Exception as exc:
            self.server_app.log(f'GET error: {exc}');return self._error(500,str(exc))

    def do_POST(self):
        p=urllib.parse.urlsplit(self.path).path.rstrip('/') or '/'
        if not self._origin_ok():return self._error(403,'Origin check failed')
        if p=='/api/auth/login':
            x=self._body()
            try:u=self.server_app.authenticate(str(x.get('username','')).strip(),str(x.get('password','')),self.client_address[0])
            except ValueError as exc:return self._error(429,str(exc))
            if not u:return self._error(401,'Invalid username or password')
            sid=self.server_app.new_session(u);max_age=int(self.server_app.cfg().get('web_session_hours',12))*3600;return self._json({'ok':True,'user':u},headers={'Set-Cookie':f'localcam_session={sid}; HttpOnly; SameSite=Strict; Max-Age={max_age}'})
        if p=='/api/auth/setup':
            if not self.server_app.is_first_run():return self._error(409,'Initial setup is already complete')
            x=self._body();username=str(x.get('username','admin')).strip();password=str(x.get('password',''))
            if len(username)<3 or len(password)<10:return self._error(400,'Username must be 3+ characters and password must be 10+ characters')
            uid=self.server_app.store.create_user(username,hash_password(password),'admin');sid=self.server_app.new_session({'user_id':uid,'username':username,'role':'admin'});return self._json({'ok':True},headers={'Set-Cookie':f'localcam_session={sid}; HttpOnly; SameSite=Strict; Max-Age=43200'})
        if not self.server_app.session(self):return self._error(401,'Authentication required')
        try:
            if p=='/api/auth/logout':self._logout();return
            if p=='/api/camera-discovery':
                if not self.server_app.role(self,'admin'):return self._error(403,'Admin role required')
                x=self._body()
                try:
                    network=ipaddress.ip_network(str(x.get('subnet','')).strip(), strict=False)
                    if network.version != 4 or not network.is_private or network.num_addresses > 256:
                        return self._error(400,'Enter a private IPv4 subnet with no more than 256 addresses (for example, 192.168.1.0/24).')
                    hosts=list(network.hosts())
                    ports=(554,8554,10554)
                    targets=[(str(host),port) for host in hosts for port in ports]
                    found=[]
                    def probe(target):
                        host,port=target
                        try:
                            with socket.create_connection((host,port),timeout=0.35): return {'host':host,'port':port}
                        except OSError:return None
                    with ThreadPoolExecutor(max_workers=48) as pool:
                        for future in as_completed([pool.submit(probe,t) for t in targets]):
                            result=future.result()
                            if result:found.append(result)
                    found.sort(key=lambda r:(ipaddress.ip_address(r['host']),r['port']))
                    return self._json({'results':found,'scanned_addresses':len(hosts),'ports':list(ports)})
                except ValueError:
                    return self._error(400,'Enter a valid private IPv4 subnet, such as 192.168.1.0/24.')
            if p=='/api/settings':
                if not self.server_app.role(self,'admin'):return self._error(403,'Admin role required')
                return self._json(self.server_app.save_settings(self._body()))
            if p.startswith('/api/record/'):
                sid=urllib.parse.unquote(p[len('/api/record/'):]);s=self.server_app.streams.get(sid)
                if not s:return self._error(404,'Stream not found')
                if not self.server_app.role(self,'admin','operator'):return self._error(403,'Operator role required')
                if p.endswith('/start'):return self._json({'ok':s.start_recording()})
                if p.endswith('/stop'):s.stop_recording();return self._json({'ok':True})
            if p.startswith('/api/ptz/'):
                sid=urllib.parse.unquote(p[len('/api/ptz/'):]);s=self.server_app.streams.get(sid)
                if not s:return self._error(404,'Camera not found')
                if not self.server_app.role(self,'admin','operator'):return self._error(403,'Operator role required')
                if p.endswith('/move'):
                    x=self._body();self.server_app.ptz.move(sid,s.camera,float(x.get('pan',0)),float(x.get('tilt',0)),float(x.get('zoom',0)),float(x.get('seconds',.35)));return self._json({'ok':True})
                if p.endswith('/stop'):self.server_app.ptz.stop(sid,s.camera);return self._json({'ok':True})
                if p.endswith('/home'):self.server_app.ptz.home(sid,s.camera);return self._json({'ok':True})
            if p=='/api/admin/restore':
                if not self.server_app.role(self,'admin'):return self._error(403,'Admin role required')
                raw=base64.b64decode(str(self._body(55_000_000).get('archive_base64','')),validate=True);self.server_app.restore(raw);return self._json({'ok':True,'message':'Backup restored. Restart LocalCam if needed.'})
            if p.startswith('/api/events/') and p.endswith('/ack'):
                if not self.server_app.role(self,'admin','operator'):return self._error(403,'Operator role required')
                self.server_app.store.acknowledge(int(p.split('/')[-2]));return self._json({'ok':True})
            if p=='/api/users':
                if not self.server_app.role(self,'admin'):return self._error(403,'Admin role required')
                x=self._body();password=str(x.get('password',''))
                if len(password)<10:return self._error(400,'Password must be at least 10 characters')
                return self._json({'ok':True,'id':self.server_app.store.create_user(str(x.get('username','')).strip(),hash_password(password),str(x.get('role','viewer')))})
            return self._error(404,'Not found')
        except Exception as exc:
            self.server_app.log(f'POST error: {exc}');return self._error(400,str(exc))

    def do_PUT(self):
        if not self._origin_ok() or not self.server_app.role(self,'admin'):return self._error(403,'Admin role required')
        p=urllib.parse.urlsplit(self.path).path.rstrip('/')
        try:
            uid=int(p.rsplit('/',1)[-1]);x=self._body();self.server_app.store.update_user(uid,role=str(x['role']) if x.get('role') else None,enabled=bool(x['enabled']) if 'enabled' in x else None,password_hash=hash_password(str(x['password'])) if x.get('password') else None);return self._json({'ok':True})
        except Exception as exc:return self._error(400,str(exc))

    def do_DELETE(self):
        if not self._origin_ok() or not self.server_app.role(self,'admin'):return self._error(403,'Admin role required')
        try:
            uid=int(urllib.parse.urlsplit(self.path).path.rstrip('/').rsplit('/',1)[-1]);current=self.server_app.session(self)
            if current and int(current['user_id'])==uid:return self._error(400,'You cannot delete your own account')
            if self.server_app.store.user_count()<=1:return self._error(400,'At least one user must remain')
            self.server_app.store.delete_user(uid);return self._json({'ok':True})
        except Exception as exc:return self._error(400,str(exc))

    def recordings(self,q):
        root=Path(self.server_app.cfg().get('record_root','G:/LocalCam/recordings'));day=(q.get('date') or [datetime.now().strftime('%Y-%m-%d')])[0];camera=(q.get('camera') or [''])[0];term=(q.get('q') or [''])[0].lower()
        if not root.exists():return []
        out=[]
        for cdir in root.iterdir():
            if not cdir.is_dir() or (camera and cdir.name not in (camera,safe_name(camera))):continue
            d=cdir/day
            if not d.exists():continue
            for f in d.glob('*.mkv'):
                if term and term not in f.name.lower():continue
                try:s=f.stat()
                except OSError:continue
                out.append({'id':str(f.relative_to(root)).replace(os.sep,'/'),'camera':cdir.name,'name':f.name,'timestamp':s.st_mtime,'time':datetime.fromtimestamp(s.st_mtime).strftime('%H:%M:%S'),'size':s.st_size,'size_human':human_bytes(s.st_size)})
        return sorted(out,key=lambda x:x['timestamp'])[-5000:]

    def timeline(self,day,camera):
        duration=int(self.server_app.cfg().get('segment_minutes',10))*60;return [{'id':r['id'],'camera':r['camera'],'start':(s:=datetime.fromtimestamp(r['timestamp'])).isoformat(),'end':(s+timedelta(seconds=duration)).isoformat(),'timestamp':r['timestamp']} for r in self.recordings({'date':[day],'camera':[camera],'q':['']})]

    def media(self,q):
        root=Path(self.server_app.cfg().get('record_root','G:/LocalCam/recordings'));p=safe_join(root,(q.get('path') or [''])[0])
        if not p or not p.is_file():return self._error(404,'File not found')
        ff=self.server_app.cfg().get('ffmpeg_path','ffmpeg');cmd=[ff,'-hide_banner','-loglevel','error','-i',str(p),'-map','0:v:0?','-map','0:a:0?','-c:v','libx264','-preset','veryfast','-c:a','aac','-movflags','+frag_keyframe+empty_moov+default_base_moof','-f','mp4','pipe:1']
        try:proc=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        except OSError as exc:return self._error(500,str(exc))
        self.send_response(200);self.send_header('Content-Type','video/mp4');self.send_header('Cache-Control','no-store');self.end_headers()
        try:
            while proc.stdout:
                chunk=proc.stdout.read(64*1024)
                if not chunk:break
                self.wfile.write(chunk);self.wfile.flush()
        except (BrokenPipeError,ConnectionResetError,OSError):pass
        finally:
            try:proc.kill()
            except OSError:pass
