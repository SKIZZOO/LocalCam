import json, shutil, subprocess, threading, time
from datetime import datetime
from pathlib import Path
from flask import Flask, Response, jsonify, request, send_from_directory
from waitress import serve
from PIL import Image, ImageChops
import io

ROOT=Path(__file__).resolve().parent
CONFIG=ROOT/'config.json'; DEFAULT=ROOT/'config.example.json'; LOG=ROOT/'localcam.log'
app=Flask(__name__,static_folder='web',static_url_path='')
lock=threading.RLock(); procs={}; motion_procs={}; motion_last={}

def log(msg):
    line=f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n"
    with lock: LOG.open('a',encoding='utf-8').write(line)
    print(line,end='')

def load_config():
    if not CONFIG.exists(): shutil.copy2(DEFAULT,CONFIG)
    return json.loads(CONFIG.read_text(encoding='utf-8'))

def save_config(cfg):
    tmp=CONFIG.with_suffix('.tmp'); tmp.write_text(json.dumps(cfg,indent=2,ensure_ascii=False),encoding='utf-8'); tmp.replace(CONFIG)

def cam(cfg,idx):
    cams=cfg.get('cameras',[]); return cams[idx] if 0<=idx<len(cams) else None

def auth_url(c):
    u=c.get('url','')
    if '://' not in u or not c.get('username') or '@' in u: return u
    scheme,rest=u.split('://',1); user=c.get('username',''); pwd=c.get('password','')
    token=f'{user}:{pwd}@' if pwd else f'{user}@'
    return f'{scheme}://{token}{rest}'

def ffmpeg(cfg): return cfg.get('ffmpeg_path','ffmpeg') or 'ffmpeg'

def outdir(cfg,idx):
    c=cam(cfg,idx); name=(c or {}).get('name',f'Camera {idx+1}')
    safe=''.join(x if x.isalnum() or x in ' _-' else '_' for x in name).strip() or f'Camera-{idx+1}'
    p=Path(cfg.get('record_root','G:/CAM720/recordings'))/safe; p.mkdir(parents=True,exist_ok=True); return p

def start_record(idx,cfg):
    with lock:
        p=procs.get(idx)
        if p and p.poll() is None: return True
        c=cam(cfg,idx)
        if not c: return False
        pattern=str(outdir(cfg,idx)/'%Y-%m-%d_%H-%M-%S.mkv')
        cmd=[ffmpeg(cfg),'-hide_banner','-loglevel','warning','-rtsp_transport','tcp','-i',auth_url(c),'-map','0:v:0?','-map','0:a:0?','-c','copy','-f','segment','-segment_time',str(int(cfg.get('segment_minutes',10))*60),'-reset_timestamps','1','-strftime','1',pattern]
        try: p=subprocess.Popen(cmd,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        except OSError as e: log(f'Camera {idx+1}: ffmpeg start failed: {e}'); return False
        procs[idx]=p; threading.Thread(target=drain,args=(idx,p),daemon=True).start(); log(f'Camera {idx+1}: recording started'); return True

def drain(idx,p):
    try:
        for line in p.stderr:
            if line.strip() and any(w in line.lower() for w in ('error','failed')): log(f'Camera {idx+1}: {line.strip()}')
    except Exception: pass

def stop_record(idx):
    with lock:
        p=procs.pop(idx,None)
        if not p: return
        if p.poll() is None:
            p.terminate()
            try: p.wait(timeout=5)
            except subprocess.TimeoutExpired: p.kill()
        log(f'Camera {idx+1}: recording stopped')

def disk(cfg):
    root=Path(cfg.get('record_root','G:/CAM720/recordings'))
    try:
        u=shutil.disk_usage(root if root.exists() else root.parent); return {'free':u.free,'total':u.total,'used':u.used}
    except Exception: return {'free':0,'total':0,'used':0}

def prune(cfg):
    root=Path(cfg.get('record_root','G:/CAM720/recordings'))
    if not root.exists(): return
    retention=int(cfg.get('retention_days',30)); cutoff=time.time()-retention*86400 if retention>0 else 0
    files=sorted([p for p in root.rglob('*.mkv') if p.exists()],key=lambda p:p.stat().st_mtime)
    if retention>0:
        for f in list(files):
            if f.stat().st_mtime<cutoff:
                try:f.unlink()
                except OSError:pass
    need=max(0,float(cfg.get('min_free_gb',20)))*(1024**3)
    while disk(cfg)['free']<need:
        files=sorted([p for p in root.rglob('*.mkv') if p.exists()],key=lambda p:p.stat().st_mtime)
        if not files: break
        try: files[0].unlink()
        except OSError: break

def frame_stream(p):
    buf=b''
    while True:
        chunk=p.stdout.read(8192)
        if not chunk: break
        buf+=chunk
        while True:
            s=buf.find(b'\xff\xd8'); e=buf.find(b'\xff\xd9',s+2)
            if s<0 or e<0: break
            frame=buf[s:e+2]; buf=buf[e+2:]
            yield frame

def motion_worker(idx):
    while True:
        try:
            cfg=load_config(); m=cfg.get('motion',{})
            if cfg.get('record_mode')!='motion' or not m.get('enabled',True): time.sleep(2); continue
            c=cam(cfg,idx)
            if not c: time.sleep(2); continue
            cmd=[ffmpeg(cfg),'-hide_banner','-loglevel','error','-rtsp_transport','tcp','-i',auth_url(c),'-vf',f"fps=1,scale={int(m.get('resolution',320))}:-1",'-f','image2pipe','-vcodec','mjpeg','pipe:1']
            p=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)); motion_procs[idx]=p
            prev=None
            for raw in frame_stream(p):
                try:
                    img=Image.open(io.BytesIO(raw)).convert('L').resize((160,90))
                    if prev is not None:
                        diff=ImageChops.difference(prev,img); hist=diff.histogram(); total=sum(hist) or 1
                        changed=sum(v for i,v in enumerate(hist) if i>=18); frac=changed/(160*90); mean=sum(i*v for i,v in enumerate(hist))/total
                        if mean>=float(m.get('threshold',12)) and frac>=float(m.get('min_changed_fraction',0.02)):
                            motion_last[idx]=time.time(); start_record(idx,cfg); log(f'Camera {idx+1}: motion detected')
                    prev=img
                    if idx in procs and procs[idx].poll() is None and motion_last.get(idx,0)>0 and time.time()-motion_last[idx]>float(m.get('cooldown_seconds',15)):
                        stop_record(idx); motion_last[idx]=0
                except Exception: pass
            p.kill()
        except Exception as e: log(f'Camera {idx+1}: motion worker {e}')
        time.sleep(2)

def monitor():
    while True:
        try:
            cfg=load_config()
            if cfg.get('record_mode','continuous')=='continuous':
                for i in range(len(cfg.get('cameras',[]))): start_record(i,cfg)
            prune(cfg)
        except Exception as e: log(f'monitor: {e}')
        time.sleep(5)

for i in range(8): threading.Thread(target=motion_worker,args=(i,),daemon=True).start()
threading.Thread(target=monitor,daemon=True).start()

@app.get('/')
def index(): return send_from_directory(ROOT/'web','index.html')
@app.get('/<path:path>')
def static_files(path): return send_from_directory(ROOT/'web',path)

@app.get('/api/state')
def state():
    cfg=load_config(); cams=[]
    for i,c in enumerate(cfg.get('cameras',[])):
        p=procs.get(i); cams.append({'id':i,'name':c.get('name',f'Camera {i+1}'),'recording':bool(p and p.poll() is None),'motion':bool(motion_last.get(i))})
    return jsonify({'cameras':cams,'storage':disk(cfg),'record_mode':cfg.get('record_mode'),'record_root':cfg.get('record_root'),'port':cfg.get('web_port',8765)})

@app.get('/api/config')
def get_config():
    cfg=load_config(); safe=json.loads(json.dumps(cfg))
    for c in safe.get('cameras',[]): c['password']='********' if c.get('password') else ''
    return jsonify(safe)

@app.post('/api/config')
def set_config():
    incoming=request.get_json(force=True); cfg=load_config()
    for k in ('web_host','web_port','record_root','snapshot_root','record_mode','segment_minutes','min_free_gb','retention_days','motion','ffmpeg_path'):
        if k in incoming: cfg[k]=incoming[k]
    if 'cameras' in incoming:
        old=cfg.get('cameras',[]); cams=incoming['cameras']
        for i,c in enumerate(cams):
            if i<len(old) and c.get('password')=='********': c['password']=old[i].get('password','')
        cfg['cameras']=cams
    save_config(cfg); return jsonify({'ok':True})

@app.post('/api/record/<int:idx>/start')
def record_start(idx): return jsonify({'ok':start_record(idx,load_config())})
@app.post('/api/record/<int:idx>/stop')
def record_stop(idx): stop_record(idx); return jsonify({'ok':True})

@app.get('/api/mjpeg/<int:idx>')
def mjpeg(idx):
    cfg=load_config(); c=cam(cfg,idx)
    if not c: return ('not found',404)
    cmd=[ffmpeg(cfg),'-hide_banner','-loglevel','error','-rtsp_transport','tcp','-i',auth_url(c),'-an','-vf',f"fps={max(1,int(cfg.get('web_live_fps',4)))},scale={max(240,int(cfg.get('web_live_width',640)))}:-1",'-f','mjpeg','-q:v','6','pipe:1']
    try: p=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    except OSError as e: return (str(e),500)
    def generate():
        try:
            for frame in frame_stream(p): yield b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: '+str(len(frame)).encode()+b'\r\n\r\n'+frame+b'\r\n'
        finally:
            if p.poll() is None: p.kill()
    return Response(generate(),mimetype='multipart/x-mixed-replace; boundary=frame')

@app.get('/api/snapshot/<int:idx>')
def snapshot(idx):
    cfg=load_config(); c=cam(cfg,idx)
    if not c: return ('not found',404)
    p=subprocess.Popen([ffmpeg(cfg),'-hide_banner','-loglevel','error','-rtsp_transport','tcp','-i',auth_url(c),'-frames:v','1','-f','mjpeg','pipe:1'],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    data=p.stdout.read(); p.kill(); return Response(data,mimetype='image/jpeg')

@app.get('/api/recordings')
def recordings():
    cfg=load_config(); root=Path(cfg.get('record_root','G:/CAM720/recordings')); rows=[]
    if root.exists():
        for p in sorted(root.rglob('*.mkv'),key=lambda x:x.stat().st_mtime if x.exists() else 0,reverse=True)[:500]:
            rows.append({'name':p.name,'camera':p.parent.name,'path':str(p.relative_to(root)).replace('\\','/'),'size':p.stat().st_size,'mtime':datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec='seconds')})
    return jsonify(rows)

@app.get('/api/file/<path:relpath>')
def file_serve(relpath):
    root=Path(load_config().get('record_root','G:/CAM720/recordings')).resolve(); target=(root/relpath).resolve()
    if root not in target.parents: return ('forbidden',403)
    if not target.exists(): return ('not found',404)
    return send_from_directory(target.parent,target.name,as_attachment=True)

@app.get('/api/play/<path:relpath>')
def play(relpath):
    cfg=load_config(); root=Path(cfg.get('record_root','G:/CAM720/recordings')).resolve(); target=(root/relpath).resolve()
    if root not in target.parents or not target.exists(): return ('not found',404)
    p=subprocess.Popen([ffmpeg(cfg),'-hide_banner','-loglevel','error','-i',str(target),'-c','copy','-movflags','frag_keyframe+empty_moov','-f','mp4','pipe:1'],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    def stream():
        try:
            while True:
                b=p.stdout.read(65536)
                if not b: break
                yield b
        finally:
            if p.poll() is None: p.kill()
    return Response(stream(),mimetype='video/mp4',headers={'Cache-Control':'no-store'})

if __name__=='__main__':
    cfg=load_config(); host=cfg.get('web_host','0.0.0.0'); port=int(cfg.get('web_port',8765)); log(f'LocalCam listening on http://{host}:{port}'); serve(app,host=host,port=port,threads=16)
