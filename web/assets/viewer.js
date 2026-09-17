// Live camera viewer controls: digital zoom/pan plus optional physical ONVIF PTZ.
(() => {
  const grid = document.getElementById('cameraGrid');
  if (!grid) return;

  const viewers = new Map();
  let ptzMap = new Map();
  let refreshInFlight = false;
  let refreshTimer = null;
  let refreshInterval = null;

  const style = document.createElement('style');
  style.textContent = `
    .cam > .ptz { display:none !important; }
    .cam-body.live-view { position:relative; overflow:hidden; background:#02060b; touch-action:none; }
    .cam-body.live-view img { transform-origin:center center; will-change:transform; user-select:none; -webkit-user-drag:none; cursor:grab; }
    .cam-body.live-view img.dragging { cursor:grabbing; }
    .viewer-toolbar { display:flex; align-items:center; gap:6px; flex-wrap:wrap; padding:8px 10px; border-top:1px solid rgba(255,255,255,.08); background:rgba(7,12,20,.94); }
    .viewer-group { display:flex; align-items:center; gap:4px; }
    .viewer-btn { min-width:34px; height:30px; padding:0 9px; border:1px solid rgba(255,255,255,.11); border-radius:8px; background:rgba(20,31,47,.9); color:#eef5ff; cursor:pointer; font-size:12px; }
    .viewer-btn:hover { border-color:rgba(119,167,255,.5); background:rgba(28,43,63,.96); }
    .viewer-btn.primary { background:rgba(66,113,190,.26); border-color:rgba(119,167,255,.4); }
    .viewer-pad { display:grid; grid-template-columns:repeat(3,30px); gap:4px; margin-left:auto; }
    .viewer-pad .viewer-btn { min-width:30px; width:30px; padding:0; }
    .viewer-status { min-width:44px; text-align:center; font-size:11px; color:rgba(232,241,250,.62); }
    .viewer-note { width:100%; font-size:10px; color:rgba(232,241,250,.48); }
  `;
  document.head.appendChild(style);

  const clamp = (v, min, max) => Math.max(min, Math.min(max, v));

  async function refreshPtz() {
    try {
      const streams = await fetch('/api/streams', { credentials:'same-origin', cache:'no-store' }).then((r) => {
        if (!r.ok) throw new Error('streams');
        return r.json();
      });
      ptzMap = new Map(streams.map((s) => [String(s.id), !!s.ptz_enabled]));
    } catch {
      ptzMap = new Map();
    }
  }

  function getId(img) {
    try {
      const url = new URL(img.currentSrc || img.src, location.href);
      const match = url.pathname.match(/^\/live\/(.+)\.mjpg$/);
      return match ? decodeURIComponent(match[1]) : '';
    } catch {
      return '';
    }
  }

  function stateFor(id, body, img) {
    let viewer = viewers.get(id);
    if (!viewer) {
      viewer = { id, body, img, zoom:1, x:0, y:0, dragging:false };
      viewers.set(id, viewer);
    } else {
      viewer.body = body;
      viewer.img = img;
    }
    return viewer;
  }

  function apply(viewer) {
    const rect = viewer.body.getBoundingClientRect();
    const maxX = Math.max(0, rect.width * (viewer.zoom - 1) / 2);
    const maxY = Math.max(0, rect.height * (viewer.zoom - 1) / 2);
    viewer.x = clamp(viewer.x, -maxX, maxX);
    viewer.y = clamp(viewer.y, -maxY, maxY);
    viewer.img.style.transform = `translate3d(${viewer.x}px,${viewer.y}px,0) scale(${viewer.zoom})`;
    const label = viewer.body.parentElement.querySelector('[data-viewer-status]');
    if (label) label.textContent = `${Math.round(viewer.zoom * 100)}%`;
  }

  function zoom(viewer, value, anchorX = null, anchorY = null) {
    const old = viewer.zoom;
    viewer.zoom = clamp(value, 1, 4);
    if (anchorX != null && anchorY != null && old !== viewer.zoom) {
      const factor = viewer.zoom / old - 1;
      viewer.x += anchorX * factor;
      viewer.y += anchorY * factor;
    }
    if (viewer.zoom === 1) { viewer.x = 0; viewer.y = 0; }
    apply(viewer);
  }

  async function apiControl(id, path, body) {
    try {
      return await api(`/api/ptz/${encodeURIComponent(id)}/${path}`, {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body: body ? JSON.stringify(body) : undefined
      });
    } catch (error) {
      const message = String(error.message || 'Camera control failed.');
      if (/camera not found|stream not found/i.test(message)) {
        await refreshPtz();
        throw new Error('Camera control is unavailable because the current camera stream is no longer registered. Refresh the page after saving settings.');
      }
      throw error;
    }
  }

  function enhance(card) {
    const img = card.querySelector('.cam-body img');
    const audio = card.querySelector('.live-audio');
    const body = card.querySelector('.cam-body');
    if (!img || !body || body.dataset.liveViewEnhanced) return;
    const id = getId(img);
    if (!id) return;

    body.dataset.liveViewEnhanced = '1';
    body.classList.add('live-view');
    const viewer = stateFor(id, body, img);
    const canControl = typeof window.localcamCanControl === 'function' ? window.localcamCanControl() : false;
    img.draggable = false;

    body.addEventListener('wheel', (e) => {
      e.preventDefault();
      const rect = body.getBoundingClientRect();
      zoom(viewer, viewer.zoom + (e.deltaY < 0 ? .25 : -.25), e.clientX - rect.left - rect.width/2, e.clientY - rect.top - rect.height/2);
    }, { passive:false });

    const endDrag = (e) => {
      viewer.dragging = false;
      img.classList.remove('dragging');
      try { body.releasePointerCapture?.(e.pointerId); } catch {}
    };
    body.addEventListener('pointerdown', (e) => {
      if (viewer.zoom <= 1 || e.target.closest('button')) return;
      viewer.dragging = true;
      viewer.lastX = e.clientX;
      viewer.lastY = e.clientY;
      img.classList.add('dragging');
      body.setPointerCapture?.(e.pointerId);
    });
    body.addEventListener('pointermove', (e) => {
      if (!viewer.dragging) return;
      viewer.x += e.clientX - viewer.lastX;
      viewer.y += e.clientY - viewer.lastY;
      viewer.lastX = e.clientX;
      viewer.lastY = e.clientY;
      apply(viewer);
    });
    body.addEventListener('pointerup', endDrag);
    body.addEventListener('pointercancel', endDrag);

    const toolbar = document.createElement('div');
    toolbar.className = 'viewer-toolbar';
    const group = document.createElement('div'); group.className = 'viewer-group';
    const out = document.createElement('button'); out.type='button'; out.className='viewer-btn'; out.textContent='−'; out.title='Digital zoom out';
    const reset = document.createElement('button'); reset.type='button'; reset.className='viewer-btn primary'; reset.textContent='Reset'; reset.title='Reset digital zoom and pan';
    const into = document.createElement('button'); into.type='button'; into.className='viewer-btn'; into.textContent='+'; into.title='Digital zoom in';
    const full = document.createElement('button'); full.type='button'; full.className='viewer-btn'; full.textContent='Fullscreen';
    const status = document.createElement('span'); status.className='viewer-status'; status.dataset.viewerStatus='1';
    group.append(out, reset, into, full, status); toolbar.appendChild(group);

    const audioGroup = document.createElement('div'); audioGroup.className='viewer-group audio-group';
    const mute = document.createElement('button'); mute.type='button'; mute.className='viewer-btn'; mute.dataset.audioMute='1'; mute.textContent = audio?.muted ? 'Unmute' : 'Mute'; mute.title='Mute or unmute camera audio';
    const volume = document.createElement('input'); volume.type='range'; volume.min='0'; volume.max='1'; volume.step='0.01'; volume.value=audio ? String(audio.volume || .8) : '0.8'; volume.className='viewer-volume'; volume.title='Camera volume';
    if (audio) {
      audio.volume = Number(volume.value);
      audio.addEventListener('error', () => showToast('Live audio is unavailable for this camera.', 'error'), { once:true });
      mute.addEventListener('click', async () => {
        audio.muted = !audio.muted;
        mute.textContent = audio.muted ? 'Unmute' : 'Mute';
        if (!audio.muted) {
          try { await audio.play(); } catch {}
        }
      });
      volume.addEventListener('input', () => {
        audio.volume = Number(volume.value);
        if (audio.volume > 0 && audio.muted) {
          audio.muted = false;
          mute.textContent = 'Mute';
          audio.play().catch(() => {});
        }
      });
    } else {
      mute.disabled = true; volume.disabled = true;
    }
    audioGroup.append(mute, volume);
    group.appendChild(audioGroup);

    if (canControl) {
      const talk = document.createElement('button'); talk.type='button'; talk.className='viewer-btn'; talk.textContent='Talk'; talk.title='Hold to talk through the camera (ONVIF Profile T when supported)';
      talk.dataset.talk='1';
      group.appendChild(talk);
    }

    if (canControl && ptzMap.get(id)) {
      const pad = document.createElement('div'); pad.className='viewer-pad';
      const moves=[['↖',-1,1],['↑',0,1],['↗',1,1],['←',-1,0],['⌂',0,0],['→',1,0],['↙',-1,-1],['↓',0,-1],['↘',1,-1]];
      for (const [label,pan,tilt] of moves) {
        const b=document.createElement('button'); b.type='button'; b.className='viewer-btn'; b.textContent=label; b.dataset.ptz='1'; b.dataset.holdPtz='1'; b.dataset.pan=pan; b.dataset.tilt=tilt; b.title=label==='⌂'?'Home · click to return':'Hold to move camera'; pad.appendChild(b);
        if (label !== '⌂') {
          const start = async (e) => {
            e.preventDefault();
            try {
              b.setPointerCapture?.(e.pointerId);
              await apiControl(id, 'move', { pan:Number(pan), tilt:Number(tilt), zoom:0, seconds:0 });
            } catch (error) { showToast(error.message || 'PTZ move failed.', 'error'); }
          };
          const stop = async (e) => {
            e.preventDefault();
            try { await apiControl(id, 'stop'); } catch {}
          };
          b.addEventListener('pointerdown', start);
          b.addEventListener('pointerup', stop);
          b.addEventListener('pointercancel', stop);
          b.addEventListener('pointerleave', stop);
        }
      }
      toolbar.appendChild(pad);
      const optical=document.createElement('div'); optical.className='viewer-group';
      const zOut=document.createElement('button'); zOut.type='button'; zOut.className='viewer-btn'; zOut.textContent='Cam −'; zOut.dataset.optical='-1';
      const stop=document.createElement('button'); stop.type='button'; stop.className='viewer-btn'; stop.textContent='Stop'; stop.dataset.stopPtz='1';
      const zIn=document.createElement('button'); zIn.type='button'; zIn.className='viewer-btn'; zIn.textContent='Cam +'; zIn.dataset.optical='1';
      optical.append(zOut,stop,zIn); toolbar.appendChild(optical);
    }

    const note=document.createElement('div'); note.className='viewer-note';
    note.textContent=ptzMap.get(id) ? 'Mouse wheel / +− = digital zoom · drag = digital pan · arrows = physical PTZ.' : 'Mouse wheel / +− = digital zoom · drag = digital pan. Enable ONVIF PTZ for physical movement.';
    toolbar.appendChild(note);
    body.appendChild(toolbar);

    toolbar.addEventListener('pointerdown', (e) => {
      const b = e.target.closest('[data-talk]');
      if (!b || !window.localcamStartTalk) return;
      e.preventDefault();
      window.localcamStartTalk(id);
    });
    const endTalk = (e) => {
      const b = e.target.closest?.('[data-talk]');
      if (b && window.localcamStopTalk) {
        e.preventDefault();
        window.localcamStopTalk();
      }
    };
    toolbar.addEventListener('pointerup', endTalk);
    toolbar.addEventListener('pointercancel', endTalk);

    toolbar.addEventListener('click', async (e) => {
      const b=e.target.closest('button'); if(!b) return;
      try {
        if(b===out) zoom(viewer,viewer.zoom-.25);
        else if(b===into) zoom(viewer,viewer.zoom+.25);
        else if(b===reset) zoom(viewer,1);
        else if(b===full) await body.requestFullscreen?.();
        else if(b.dataset.ptz){
          if (b.dataset.holdPtz && b.textContent !== '⌂') return;
          if(b.textContent==='⌂') await apiControl(id,'home');
          else await apiControl(id,'move',{pan:Number(b.dataset.pan),tilt:Number(b.dataset.tilt),zoom:0,seconds:.3});
        } else if(b.dataset.optical) await apiControl(id,'move',{pan:0,tilt:0,zoom:Number(b.dataset.optical),seconds:.3});
        else if(b.dataset.stopPtz) await apiControl(id,'stop');
      } catch (error) { showToast(error.message || 'Camera control failed.','error'); }
    });

    apply(viewer);
  }

  async function refresh() {
    if (document.hidden || refreshInFlight) return;
    refreshInFlight = true;
    try {
      await refreshPtz();
      grid.querySelectorAll('.cam').forEach(enhance);
    } finally {
      refreshInFlight = false;
    }
  }

  const scheduleRefresh = () => {
    clearTimeout(refreshTimer);
    refreshTimer = setTimeout(() => refresh(), 300);
  };

  new MutationObserver(() => scheduleRefresh()).observe(grid, { childList:true, subtree:true });
  window.addEventListener('resize', () => viewers.forEach(apply));
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) refresh();
  });
  refreshInterval = setInterval(refresh, 15000);
  refresh();

  window.addEventListener('beforeunload', () => {
    clearInterval(refreshInterval);
    clearTimeout(refreshTimer);
  });
})();
