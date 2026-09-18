const state = { info: null, settings: null, streams: [], auth: null, liveQuality: localStorage.getItem('localcam.liveQuality') || 'high', cameraEditorDirty: false, liveSyncAt: 0, liveLayoutEdit: false, liveLayoutOriginal: null, liveLayoutDirty: false, liveLayoutSaving: false };
const webrtcPeers = new Map();
let webrtcGeneration = 0;

function closeWebRTCFeeds() {
  webrtcGeneration += 1;
  for (const pc of webrtcPeers.values()) {
    try { pc.close(); } catch {}
  }
  webrtcPeers.clear();
}

async function waitForIceGathering(pc) {
  if (pc.iceGatheringState === 'complete') return;
  await new Promise((resolve) => {
    const timeout = setTimeout(() => {
      pc.removeEventListener('icegatheringstatechange', check);
      resolve();
    }, 3000);
    function check() {
      if (pc.iceGatheringState === 'complete') {
        clearTimeout(timeout);
        pc.removeEventListener('icegatheringstatechange', check);
        resolve();
      }
    }
    pc.addEventListener('icegatheringstatechange', check);
  });
}

function newPeerId() {
  if (crypto.randomUUID) return crypto.randomUUID();
  return `localcam-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

async function connectWebRTC(stream, video, fallback, quality, generation) {
  if (!window.RTCPeerConnection || generation !== webrtcGeneration) {
    video.style.display = 'none';
    fallback.style.display = '';
    const transport = video.closest('.cam-body')?.querySelector('[data-live-transport]');
    if (transport) transport.textContent = 'MJPEG fallback';
    return false;
  }
  const peerId = newPeerId();
  const pc = new RTCPeerConnection({ iceServers: [] });
  webrtcPeers.set(peerId, pc);
  pc.addTransceiver('video', { direction: 'recvonly' });
  pc.addTransceiver('audio', { direction: 'recvonly' });
  video.addEventListener('playing', () => {
    video.style.display = '';
    fallback.style.display = 'none';
  }, { once: true });
  pc.addEventListener('track', (event) => {
    const remote = event.streams?.[0];
    if (remote) {
      video.srcObject = remote;
    } else {
      const current = video.srcObject instanceof MediaStream ? video.srcObject : new MediaStream();
      current.addTrack(event.track);
      video.srcObject = current;
    }
  });
  pc.addEventListener('connectionstatechange', () => {
    if (pc.connectionState === 'failed' || pc.connectionState === 'closed') {
      if (video.srcObject) video.srcObject = null;
      video.style.display = 'none';
      fallback.style.display = '';
      const transport = video.closest('.cam-body')?.querySelector('[data-live-transport]');
      if (transport) transport.textContent = 'MJPEG fallback';
      webrtcPeers.delete(peerId);
    }
  });
  try {
    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    await waitForIceGathering(pc);
    if (generation !== webrtcGeneration) {
      pc.close();
      webrtcPeers.delete(peerId);
      return false;
    }
    const answer = await api(`/api/webrtc/offer/${encodeURIComponent(stream.id)}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        peer_id: peerId,
        type: pc.localDescription?.type || 'offer',
        sdp: pc.localDescription?.sdp || '',
        quality
      })
    });
    await pc.setRemoteDescription(answer);
    if (generation !== webrtcGeneration) {
      pc.close();
      webrtcPeers.delete(peerId);
      return false;
    }
    video.style.display = 'none';
    fallback.style.display = '';
    video.muted = true;
    video.autoplay = true;
    video.playsInline = true;
    await video.play().catch(() => {});
    video.title = `WebRTC · ${quality}`;
    const transport = video.closest('.cam-body')?.querySelector('[data-live-transport]');
    if (transport) transport.textContent = `WebRTC · H.264 · ${quality}`;
    return true;
  } catch (error) {
    try { pc.close(); } catch {}
    webrtcPeers.delete(peerId);
    video.srcObject = null;
    video.style.display = 'none';
    fallback.style.display = '';
    const transport = video.closest('.cam-body')?.querySelector('[data-live-transport]');
    if (transport) transport.textContent = 'MJPEG fallback';
    if (generation === webrtcGeneration && !/WebRTC is unavailable/i.test(String(error.message || ''))) {
      console.warn(`LocalCam WebRTC failed for ${stream.name}:`, error);
    }
    return false;
  }
}

async function startWebRTCFeeds() {
  const generation = ++webrtcGeneration;
  const quality = selectedLiveQuality();
  const cards = [...document.querySelectorAll('#cameraGrid .cam')];
  for (const card of cards) {
    if (generation !== webrtcGeneration) return;
    const video = card.querySelector('.live-video');
    const fallback = card.querySelector('.live-fallback');
    if (!video || !fallback) continue;
    const stream = state.streams.find((item) => String(item.id) === String(video.dataset.cameraId));
    if (!stream) continue;
    await connectWebRTC(stream, video, fallback, quality, generation);
  }
}

window.localcamCanControl = () => {
  const role = state.auth?.user?.role;
  return role === 'admin' || role === 'operator';
};

const $ = (id) => document.getElementById(id);

const esc = (value) => String(value ?? '').replace(/[&<>\"]/g, (char) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '\"': '&quot;'
}[char]));

function today() {
  const d = new Date();
  return d.toLocaleDateString('en-CA');
}

function dateShift(days) {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return d.toLocaleDateString('en-CA');
}

function archiveDateTime(day, time, endOfDay = false) {
  if (!time) return '';
  return `${day}T${time}:${endOfDay ? '59' : '00'}`;
}

function can(action) {
  const role = state.auth?.user?.role || 'viewer';
  if (action === 'view') return true;
  if (role === 'admin') return true;
  return role === 'operator' && action === 'control';
}

async function api(url, options = {}) {
  const { timeoutMs = 15000, ...requestOptions } = options;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), Math.max(1000, Number(timeoutMs) || 15000));
  const started = performance.now();
  try {
    const response = await fetch(url, {
      credentials: 'same-origin',
      ...requestOptions,
      signal: controller.signal
    });

    if (response.status === 401) {
      location.replace('/login');
      throw new Error('Authentication required');
    }

    const text = await response.text();
    let data = {};
    try {
      data = text ? JSON.parse(text) : {};
    } catch {
      data = { raw: text };
    }

    if (!response.ok) {
      throw new Error(data.error || `HTTP ${response.status}`);
    }
    return data;
  } catch (error) {
    const elapsed = performance.now() - started;
    if (error?.name === 'AbortError') {
      reportClientIssue('timeout', `API request timed out: ${url}`, `${Math.round(elapsed)}ms timeout`);
      throw new Error(`Request timed out after ${Math.round(Number(timeoutMs) / 1000)}s: ${url}`);
    }
    if (elapsed >= 5000) {
      reportClientIssue('error', `Slow API request: ${url}`, `${Math.round(elapsed)}ms · ${error?.message || error}`);
    }
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

function reportClientIssue(level, message, detail = '') {
  try {
    const payload = JSON.stringify({
      level,
      message: String(message || '').slice(0, 2000),
      detail: String(detail || '').slice(0, 8000),
      page: location.pathname,
      user_agent: navigator.userAgent,
      at: new Date().toISOString()
    });
    fetch('/api/client-log', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: payload,
      keepalive: true
    }).catch(() => {});
  } catch {}
}

window.addEventListener('error', (event) => {
  reportClientIssue(
    'javascript',
    event.message || 'Uncaught browser error',
    `${event.filename || ''}:${event.lineno || 0}:${event.colno || 0}`
  );
});

window.addEventListener('unhandledrejection', (event) => {
  reportClientIssue('promise', String(event.reason?.stack || event.reason || 'Unhandled promise rejection'));
});

function showToast(message, kind = 'info') {
  let box = $('toastBox');
  if (!box) {
    box = document.createElement('div');
    box.id = 'toastBox';
    box.style.cssText = 'position:fixed;right:22px;bottom:22px;z-index:9999;display:grid;gap:8px;max-width:360px;pointer-events:none;';
    document.body.appendChild(box);
  }
  const toast = document.createElement('div');
  toast.textContent = message;
  toast.style.cssText = 'padding:12px 14px;border-radius:12px;border:1px solid rgba(255,255,255,.12);background:rgba(11,18,29,.96);box-shadow:0 14px 36px rgba(0,0,0,.3);color:#f3f7fc;font-size:13px;pointer-events:auto;';
  if (kind === 'error') toast.style.borderColor = 'rgba(255,109,131,.35)';
  if (kind === 'success') toast.style.borderColor = 'rgba(53,211,159,.35)';
  box.appendChild(toast);
  setTimeout(() => toast.remove(), 4200);
}

async function loadAuth() {
  state.auth = await api('/api/auth/status');
  const user = state.auth.user;
  $('sideUser').textContent = user ? `${user.username} · ${user.role}` : '—';
  updateLiveLayoutPermission();
}

function setPage(name) {
  if (name === 'settings' && !can('admin')) return;
  document.querySelectorAll('.page').forEach((page) => page.classList.remove('active'));
  const page = $(`page-${name}`);
  if (!page) return;
  page.classList.add('active');

  document.querySelectorAll('.nav').forEach((button) => {
    button.classList.toggle('active', button.dataset.page === name);
  });

  const titles = {
    dashboard: ['Dashboard', 'Live monitoring, recording, and system status.'],
    archive: ['Archive', 'Find, play, and download recordings.'],
    events: ['Events', 'Motion history and captured event snapshots.'],
    settings: ['Settings', 'Storage, server, motion, cameras, users, and backups.']
  };
  const [title, subtitle] = titles[name] || titles.dashboard;
  $('title').textContent = title;
  $('subtitle').textContent = subtitle;

  if (name === 'archive') {
    loadRecordings().catch((error) => showToast(error.message, 'error'));
  }
  if (name === 'events') {
    loadEvents().catch((error) => showToast(error.message, 'error'));
  }
  if (name === 'settings') {
    loadSettings().catch((error) => showToast(error.message, 'error'));
  }
}

function setupNavigation() {
  document.querySelectorAll('.nav').forEach((button) => {
    button.addEventListener('click', () => setPage(button.dataset.page));
  });

  document.querySelectorAll('.tab').forEach((button) => {
    button.addEventListener('click', () => {
      document.querySelectorAll('.tab').forEach((tab) => tab.classList.remove('active'));
      document.querySelectorAll('.settings-panel').forEach((panel) => panel.classList.remove('active'));
      button.classList.add('active');
      $(`tab-${button.dataset.tab}`)?.classList.add('active');
      if (button.dataset.tab === 'security') loadUsers().catch((error) => showToast(error.message, 'error'));
    });
  });
}

async function loadInfo() {
  try {
    state.info = await api('/api/info', { timeoutMs: 5000 });
    $('sideStatus').textContent = 'Online';
    $('sideUrl').textContent = String(state.info.url || '').replace(/^https?:\/\//, '');
    $('version').textContent = state.info.version ? `v${state.info.version}` : '';
    $('mStreams').textContent = state.info.streams?.length ?? 0;
    $('mFree').textContent = state.info.storage?.free_human ?? '—';
    $('mDrive').textContent = state.info.storage?.path ?? '—';
    $('mUsed').textContent = `${state.info.storage?.used_percent ?? 0}%`;
    $('diskBar').style.width = `${Math.min(100, Number(state.info.storage?.used_percent || 0))}%`;
    const cpu = state.info.system?.cpu_percent;
    const ram = state.info.system?.memory_percent;
    $('mSystem').textContent = cpu == null ? '—' : `${cpu}% / ${ram}%`;
    $('mUptime').textContent = `Uptime ${Math.floor(Number(state.info.system?.uptime_seconds || 0) / 3600)}h`;
    $('sideDot').classList.remove('bad');
  } catch (error) {
    $('sideStatus').textContent = 'Server error';
    $('sideDot').classList.add('bad');
    throw error;
  }
}

async function loadStreams() {
  state.streams = await api('/api/streams', { timeoutMs: 5000 });
  renderDashboard();
  fillCameraSelects();
}

function fillCameraSelects() {
  const options = '<option value="">All cameras</option>' + state.streams.map((stream) =>
    `<option value="${esc(stream.id)}">${esc(stream.name)}</option>`
  ).join('');
  $('archiveCamera').innerHTML = options;
  $('eventsCamera').innerHTML = options;
}

function applyLiveDomOrder() {
  const grid = $('cameraGrid');
  if (!grid) return;
  const byId = new Map([...grid.children].map((card) => [String(card.dataset.cameraId || ''), card]));
  state.streams.forEach((stream) => {
    const card = byId.get(String(stream.id));
    if (card) grid.appendChild(card);
  });
}

function setLiveLayoutEdit(enabled) {
  const grid = $('cameraGrid');
  const arrange = $('liveArrange');
  const save = $('liveSaveLayout');
  const cancel = $('liveCancelLayout');
  if (!grid || !arrange || !save || !cancel) return;

  state.liveLayoutEdit = !!enabled;
  grid.classList.toggle('live-layout-editing', state.liveLayoutEdit);
  [...grid.querySelectorAll('.cam')].forEach((card) => {
    card.draggable = state.liveLayoutEdit;
  });
  arrange.hidden = state.liveLayoutEdit;
  save.hidden = !state.liveLayoutEdit;
  cancel.hidden = !state.liveLayoutEdit;

  if (state.liveLayoutEdit) {
    state.liveLayoutOriginal = state.streams.map((stream) => ({ id: String(stream.id), name: String(stream.name) }));
    state.liveLayoutDirty = false;
    showToast('Drag a camera card to swap its position, or use ↑ / ↓. Rename it here, then Save layout.', 'info');
    const first = grid.querySelector('.cam-title-edit');
    first?.focus();
    first?.select();
  }
}

function markLiveLayoutDirty() {
  state.liveLayoutDirty = true;
}

function getLiveLayoutDraft() {
  const grid = $('cameraGrid');
  return [...grid.querySelectorAll('.cam')].map((card) => ({
    id: String(card.dataset.cameraId || ''),
    name: String(card.querySelector('[data-layout-name]')?.value || '').trim()
  })).filter((item) => item.id);
}

async function saveLiveLayout() {
  if (state.liveLayoutSaving) return;
  const draft = getLiveLayoutDraft();
  if (!draft.length) return;

  const invalid = draft.find((item) => !item.name);
  if (invalid) {
    showToast('Every live camera needs a name.', 'error');
    return;
  }

  state.liveLayoutSaving = true;
  const button = $('liveSaveLayout');
  if (button) {
    button.disabled = true;
    button.textContent = 'Saving…';
  }
  try {
    const result = await api('/api/camera-layout', {
      method: 'POST',
      timeoutMs: 5000,
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ cameras: draft })
    });
    const names = new Map((result.cameras || draft).map((camera) => [String(camera.id), String(camera.name)]));
    state.streams = draft.map((item) => {
      const current = state.streams.find((stream) => String(stream.id) === item.id) || {};
      return {...current, id: item.id, name: names.get(item.id) || item.name};
    });
    state.streams.forEach((stream) => {
      stream.name = names.get(String(stream.id)) || stream.name;
    });
    state.liveLayoutDirty = false;
    const grid = $('cameraGrid');
    [...grid.querySelectorAll('.cam')].forEach((card) => {
      const stream = state.streams.find((item) => String(item.id) === String(card.dataset.cameraId));
      if (!stream) return;
      const view = card.querySelector('.cam-title-view');
      const input = card.querySelector('[data-layout-name]');
      if (view) view.textContent = stream.name;
      if (input) input.value = stream.name;
    });
    setLiveLayoutEdit(false);
    applyLiveDomOrder();
    showToast(result.queued ? 'Live view layout queued for saving.' : 'Live view layout saved.', 'success');
    setTimeout(() => loadSettings().catch((error) => {
      reportClientIssue('refresh', 'Layout confirmation refresh failed', error?.message || String(error));
    }), 800);
  } catch (error) {
    showToast(error.message || 'Could not save live layout.', 'error');
  } finally {
    state.liveLayoutSaving = false;
    if (button) {
      button.disabled = false;
      button.textContent = 'Save layout';
    }
  }
}

function cancelLiveLayout() {
  const original = state.liveLayoutOriginal;
  if (original?.length) {
    const names = new Map(original.map((item) => [item.id, item.name]));
    const byId = new Map(state.streams.map((stream) => [String(stream.id), stream]));
    state.streams = original.map((item) => ({...(byId.get(item.id) || {}), id: item.id, name: item.name}));
    const grid = $('cameraGrid');
    [...grid.querySelectorAll('.cam')].forEach((card) => {
      const stream = names.get(String(card.dataset.cameraId));
      if (stream) {
        const view = card.querySelector('.cam-title-view');
        const input = card.querySelector('[data-layout-name]');
        if (view) view.textContent = stream;
        if (input) input.value = stream;
      }
    });
    applyLiveDomOrder();
  }
  state.liveLayoutDirty = false;
  setLiveLayoutEdit(false);
}

function updateLiveLayoutPermission() {
  const arrange = $('liveArrange');
  if (arrange) arrange.hidden = !can('control');
  if (!can('control') && state.liveLayoutEdit) cancelLiveLayout();
}

function setupLiveLayoutActions() {
  const arrange = $('liveArrange');
  const save = $('liveSaveLayout');
  const cancel = $('liveCancelLayout');
  const grid = $('cameraGrid');
  if (!arrange || !save || !cancel || !grid) return;

  arrange.hidden = false;
  save.hidden = true;
  cancel.hidden = true;

  arrange.addEventListener('click', () => setLiveLayoutEdit(true));
  save.addEventListener('click', () => saveLiveLayout());
  cancel.addEventListener('click', () => cancelLiveLayout());

  grid.addEventListener('input', (event) => {
    if (!state.liveLayoutEdit || !event.target.closest('[data-layout-name]')) return;
    state.liveLayoutDirty = true;
  });

  grid.addEventListener('dragstart', (event) => {
    if (!state.liveLayoutEdit) return;
    const card = event.target.closest('.cam');
    const handle = event.target.closest('[data-layout-drag]');
    const interactive = event.target.closest('button,input,select,a');
    if (!card || (interactive && !handle)) {
      event.preventDefault();
      return;
    }
    grid.dataset.dragCameraId = String(card.dataset.cameraId || '');
    card.classList.add('live-layout-dragging');
    event.dataTransfer.effectAllowed = 'move';
    event.dataTransfer.setData('text/plain', String(card.dataset.cameraId || ''));
  });

  grid.addEventListener('dragover', (event) => {
    if (!state.liveLayoutEdit) return;
    const target = event.target.closest('.cam');
    const dragId = grid.dataset.dragCameraId;
    if (!target || !dragId || String(target.dataset.cameraId) === dragId) return;
    event.preventDefault();
    [...grid.querySelectorAll('.cam')].forEach((card) => card.classList.remove('live-layout-drop-target'));
    target.classList.add('live-layout-drop-target');
  });

  grid.addEventListener('drop', (event) => {
    if (!state.liveLayoutEdit) return;
    const target = event.target.closest('.cam');
    const dragId = grid.dataset.dragCameraId;
    if (!target || !dragId || String(target.dataset.cameraId) === dragId) return;
    event.preventDefault();
    const cards = [...grid.querySelectorAll('.cam')];
    const dragged = cards.find((card) => String(card.dataset.cameraId) === dragId);
    if (!dragged) return;
    const targetIndex = cards.indexOf(target);
    const dragIndex = cards.indexOf(dragged);
    if (dragIndex < targetIndex) target.after(dragged);
    else target.before(dragged);
    state.streams = [...grid.querySelectorAll('.cam')].map((card) =>
      state.streams.find((stream) => String(stream.id) === String(card.dataset.cameraId))
    ).filter(Boolean);
    state.liveLayoutDirty = true;
    [...grid.querySelectorAll('.cam')].forEach((card) => card.classList.remove('live-layout-drop-target'));
  });

  grid.addEventListener('dragend', (event) => {
    const card = event.target.closest('.cam');
    card?.classList.remove('live-layout-dragging');
    grid.dataset.dragCameraId = '';
    [...grid.querySelectorAll('.cam')].forEach((node) => node.classList.remove('live-layout-drop-target'));
  });

  grid.addEventListener('click', (event) => {
    if (!state.liveLayoutEdit) return;
    const move = event.target.closest('[data-layout-move]');
    if (!move) return;
    const card = event.target.closest('.cam');
    if (!card) return;
    const direction = move.dataset.layoutMove;
    const sibling = direction === 'up' ? card.previousElementSibling : card.nextElementSibling;
    if (!sibling) return;
    if (direction === 'up') sibling.before(card);
    else sibling.after(card);
    state.streams = [...grid.querySelectorAll('.cam')].map((node) =>
      state.streams.find((stream) => String(stream.id) === String(node.dataset.cameraId))
    ).filter(Boolean);
    state.liveLayoutDirty = true;
  });
}
 
function selectedLiveQuality() { return state.liveQuality || 'high'; }

function renderDashboard() {
  closeWebRTCFeeds();
  const grid = $('cameraGrid');
  if (!state.streams.length) {
    grid.innerHTML = '<div class="panel"><h2>No cameras configured</h2><p class="hint">Open Settings → Cameras to add your first RTSP camera.</p></div>';
    return;
  }

  // All camera MJPEG connections are given the same future start time so
  // the browser and server release the initial frames together.
  if (!state.liveSyncAt || state.liveSyncAt < Date.now() / 1000) {
    state.liveSyncAt = Date.now() / 1000 + 0.8;
  }
  grid.innerHTML = state.streams.map((stream) => {
    const quality = selectedLiveQuality();
    const badge = stream.motion ? 'MOTION' : stream.recording ? 'REC' : stream.online ? 'LIVE' : 'OFFLINE';
    const cls = stream.motion || stream.recording || stream.online ? 'good' : '';
    const recordButton = can('control') ?
      `<label class="record-switch" title="Toggle recording for ${esc(stream.name)}">
        <input type="checkbox" data-action="record" data-id="${esc(stream.id)}" data-recording="${stream.recording ? '1' : '0'}" ${stream.recording ? 'checked' : ''} aria-label="Toggle recording">
        <span class="record-slider" aria-hidden="true"></span>
        <span class="record-switch-label">${stream.recording ? 'Recording' : 'Record'}</span>
      </label>` : '';
    const ptz = stream.ptz_enabled && can('control') ? `
      <div class="ptz">
        <button data-action="ptz" data-id="${esc(stream.id)}" data-pan="-1" data-tilt="1">↖</button>
        <button data-action="ptz" data-id="${esc(stream.id)}" data-pan="0" data-tilt="1">↑</button>
        <button data-action="ptz" data-id="${esc(stream.id)}" data-pan="1" data-tilt="1">↗</button>
        <button data-action="ptz" data-id="${esc(stream.id)}" data-pan="-1" data-tilt="0">←</button>
        <button data-action="ptz-home" data-id="${esc(stream.id)}">⌂</button>
        <button data-action="ptz" data-id="${esc(stream.id)}" data-pan="1" data-tilt="0">→</button>
        <button data-action="ptz" data-id="${esc(stream.id)}" data-pan="-1" data-tilt="-1">↙</button>
        <button data-action="ptz" data-id="${esc(stream.id)}" data-pan="0" data-tilt="-1">↓</button>
        <button data-action="ptz" data-id="${esc(stream.id)}" data-pan="1" data-tilt="-1">↘</button>
      </div>` : '';

    return `<article class="cam" data-camera-id="${esc(stream.id)}" draggable="false">
      <div class="cam-head">
        <div class="cam-title-wrap">
          <div class="cam-title"><span class="cam-title-view">${esc(stream.name)}</span><input class="cam-title-edit" data-layout-name value="${esc(stream.name)}" aria-label="Camera name"></div>
          <div class="cam-edit-tools">
            <button type="button" class="icon-btn cam-drag-handle" data-layout-drag draggable="true" title="Drag to rearrange">↕</button>
            <button type="button" class="icon-btn" data-layout-move="up" title="Move up">↑</button>
            <button type="button" class="icon-btn" data-layout-move="down" title="Move down">↓</button>
          </div>
        </div>
        <span class="pill ${cls}">${badge}</span>
      </div>
      <div class="cam-body"><img draggable="false" decoding="async" fetchpriority="high" src="/live/${encodeURIComponent(stream.id)}.mjpg?quality=${encodeURIComponent(quality)}&sync=${encodeURIComponent(state.liveSyncAt.toFixed(3))}" alt="${esc(stream.name)}"><audio class="live-audio" muted playsinline preload="none" data-audio-src="/live/${encodeURIComponent(stream.id)}.audio.ogg"></audio><div class="cam-overlay" data-live-transport>MJPEG · ${quality}</div></div>
      <div class="cam-foot"><span>${stream.online ? 'Connected' : 'Waiting for stream'}</span><div class="cam-actions"><button class="small-btn" data-action="snapshot" data-id="${esc(stream.id)}">Snapshot</button>${recordButton}</div></div>
      ${ptz}
    </article>`;
  }).join('');
}

function setupLiveQuality() {
  const select = $('liveQuality');
  if (!select) return;
  select.value = state.liveQuality;
  select.addEventListener('change', () => {
    state.liveQuality = select.value || 'high';
    localStorage.setItem('localcam.liveQuality', state.liveQuality);
    // Rebuild the live MJPEG connections from a clean page load. This avoids
    // stale media elements fighting while a quality change is in progress.
    location.reload();
  });
}

function setupDashboardActions() {
  setupLiveLayoutActions();
  $('cameraGrid').addEventListener('click', async (event) => {
    const button = event.target.closest('[data-action]');
    if (!button) return;
    const action = button.dataset.action;
    const id = button.dataset.id;
    try {
      if (action === 'snapshot') await capture(id);
      if (action === 'record') await toggleRecord(id, button.dataset.recording === '1');
      if (action === 'ptz') await ptz(id, Number(button.dataset.pan), Number(button.dataset.tilt));
      if (action === 'ptz-home') await ptzHome(id);
    } catch (error) {
      showToast(error.message, 'error');
    }
  });
}

async function capture(id) {
  const response = await fetch(`/api/snapshot/${encodeURIComponent(id)}`, { credentials: 'same-origin' });
  if (!response.ok) throw new Error(`Snapshot failed (HTTP ${response.status})`);
  const blob = await response.blob();
  const link = document.createElement('a');
  link.href = URL.createObjectURL(blob);
  link.download = `${id}-${Date.now()}.jpg`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(link.href), 1000);
  showToast('Snapshot saved.', 'success');
}

async function toggleRecord(id, recording) {
  const result = await api(`/api/record/${encodeURIComponent(id)}/toggle`, { method: 'POST' });
  if (!result.ok) throw new Error(result.error || 'Recording could not be changed.');
  await Promise.all([loadInfo(), loadStreams()]);
  showToast(result.recording ? 'Recording started.' : 'Recording stopped.', 'success');
}

async function ptz(id, pan, tilt) {
  await api(`/api/ptz/${encodeURIComponent(id)}/move`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ pan, tilt, seconds: 0.35 })
  });
}

async function ptzHome(id) {
  await api(`/api/ptz/${encodeURIComponent(id)}/home`, { method: 'POST' });
}

function initDates() {
  $('archiveDate').value = today();
  $('eventsDate').value = today();
}

async function loadRecordings() {
  const day = $('archiveDate').value || today();
  const camera = $('archiveCamera').value || '';
  const reason = $('archiveReason').value || '';
  const from = archiveDateTime(day, $('archiveFrom').value);
  const to = archiveDateTime(day, $('archiveTo').value, true);
  const query = $('archiveSearch').value || '';
  const params = new URLSearchParams({ date: day, camera, reason, q: query });
  if (from) params.set('from', from);
  if (to) params.set('to', to);
  const rows = await api(`/api/recordings?${params.toString()}`, { timeoutMs: 5000 });
  $('archiveCount').textContent = `${rows.length} segments`;
  $('recordingsList').innerHTML = rows.length ? rows.map((row) => `
    <div class="row archive-row">
      <div class="archive-main">
        <b>${esc(row.camera)}</b>
        <small>${esc(row.time)} · ${esc(row.name)} · ${esc(row.size_human)}</small>
        <code class="archive-path" title="${esc(row.path || '')}">${esc(row.path || row.id)}</code>
      </div>
      <div class="row-actions"><button class="icon-btn" data-play="${esc(row.id)}" data-name="${esc(row.name)}">Play</button><a class="icon-btn" href="/api/download?path=${encodeURIComponent(row.id)}">Download</a></div>
    </div>`).join('') : '<div class="muted">No recordings match the selected filters.</div>';
  await loadTimeline();
}

async function loadTimeline() {
  const day = $('archiveDate').value || today();
  $('timelineDate').textContent = day;
  const camera = $('archiveCamera').value || '';
  const reason = $('archiveReason')?.value || '';
  const [segments, events] = await Promise.all([
    api(`/api/timeline?date=${encodeURIComponent(day)}&camera=${encodeURIComponent(camera)}&reason=${encodeURIComponent(reason)}`),
    api(`/api/events?date=${encodeURIComponent(day)}&camera=${encodeURIComponent(camera)}`)
  ]);
  drawTimeline(segments, events);
}

function drawTimeline(items, events) {
  const box = $('timeline');
  box.replaceChildren();
  for (let hour = 0; hour < 24; hour += 1) {
    const marker = document.createElement('div');
    marker.className = 'hour';
    marker.style.left = `${(hour / 24) * 100}%`;
    marker.textContent = String(hour).padStart(2, '0');
    box.appendChild(marker);
  }
  items.forEach((item) => {
    const start = new Date(item.start);
    const end = new Date(item.end);
    const startSeconds = start.getHours() * 3600 + start.getMinutes() * 60 + start.getSeconds();
    const duration = Math.max(1, (end - start) / 1000);
    const segment = document.createElement('button');
    segment.className = 'segment';
    segment.style.left = `${(startSeconds / 86400) * 100}%`;
    segment.style.width = `${Math.max(0.3, (duration / 86400) * 100)}%`;
    segment.title = `${item.camera} · ${item.reason} · ${start.toLocaleTimeString()}`;
    segment.addEventListener('click', () => playRecording(item.id, item.name || item.id.split('/').pop()));
    box.appendChild(segment);
  });
  events.forEach((item) => {
    const date = new Date(item.started_at);
    const seconds = date.getHours() * 3600 + date.getMinutes() * 60 + date.getSeconds();
    const marker = document.createElement('div');
    marker.className = 'event';
    marker.style.left = `${(seconds / 86400) * 100}%`;
    marker.title = `${item.camera_name} · ${date.toLocaleTimeString()}`;
    box.appendChild(marker);
  });
}

function setupArchiveActions() {
  const reload = () => loadRecordings().catch((error) => showToast(error.message, 'error'));
  $('archiveSearchBtn').addEventListener('click', reload);
  ['archiveDate','archiveCamera','archiveReason','archiveFrom','archiveTo'].forEach((id) => {
    $(id)?.addEventListener('change', reload);
  });
  $('archiveSearch').addEventListener('keydown', (event) => {
    if (event.key === 'Enter') reload();
  });
  document.querySelectorAll('[data-archive-quick]').forEach((button) => {
    button.addEventListener('click', () => {
      const offset = button.dataset.archiveQuick === 'yesterday' ? -1 : 0;
      $('archiveDate').value = dateShift(offset);
      $('archiveFrom').value = '';
      $('archiveTo').value = '';
      reload();
    });
  });
  $('recordingsList').addEventListener('click', (event) => {
    const button = event.target.closest('[data-play]');
    const edit = event.target.closest('[data-edit]');
    if (button) playRecording(button.dataset.play, button.dataset.name);
    if (edit) {
      playRecording(edit.dataset.edit, edit.dataset.name);
      openClipEditor(edit.dataset.edit, edit.dataset.name);
    }
  });
  document.querySelectorAll('[data-seek]').forEach((button) => {
    button.addEventListener('click', () => seekPlayer(Number(button.dataset.seek)));
  });
  $('playbackSpeed').addEventListener('change', () => { $('player').playbackRate = Number($('playbackSpeed').value); });
  $('playerFullscreen').addEventListener('click', () => $('player').requestFullscreen?.());
  $('player').addEventListener('timeupdate', () => { $('playerCurrent').textContent = fmtTime($('player').currentTime); });
  $('player').addEventListener('loadedmetadata', () => {
    $('playerDuration').textContent = fmtTime($('player').duration);
    const end = $('editEnd');
    if (end && (Number(end.value) <= 0 || Number(end.value) > $('player').duration)) {
      end.value = $('player').duration.toFixed(1);
    }
  });
  $('player').addEventListener('error', () => {
    const source = $('player').dataset.archivePath;
    if (source) showToast('This recording could not be played. Try Download to open the original file.', 'error');
  });
}

function playRecording(id, name) {
  const player = $('player');
  player.src = `/api/media?path=${encodeURIComponent(id)}`;
  player.dataset.archivePath = id;
  player.dataset.archiveName = name;
  $('playbackName').textContent = name;
  $('editorSource').textContent = name;
  player.load();
  openClipEditor(id, name);
  player.play().catch(() => {});
}

function openClipEditor(id, name) {
  const player = $('player');
  player.dataset.archivePath = id;
  player.dataset.archiveName = name;
  $('editorSource').textContent = name;
  $('editStart').value = '0';
  $('editEnd').value = Number.isFinite(player.duration) ? player.duration.toFixed(1) : '0';
  $('editorStatus').textContent = 'Use the player to choose the start and end of your clip.';
}

function setupClipEditorActions() {
  document.querySelectorAll('[data-editor]').forEach((button) => {
    button.addEventListener('click', async () => {
      const action = button.dataset.editor;
      const player = $('player');
      if (action === 'set-start') $('editStart').value = Math.max(0, player.currentTime || 0).toFixed(1);
      if (action === 'set-end') $('editEnd').value = Math.max(0, player.currentTime || 0).toFixed(1);
      if (action !== 'export') return;

      const source = player.dataset.archivePath;
      if (!source) {
        showToast('Select a recording first.', 'error');
        return;
      }
      const start = Number($('editStart').value);
      const end = Number($('editEnd').value);
      if (!(end > start)) {
        showToast('Clip end must be after clip start.', 'error');
        return;
      }
      button.disabled = true;
      $('editorStatus').textContent = 'Exporting clip…';
      try {
        const result = await api('/api/clip', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ path: source, start, end })
        });
        $('editorStatus').innerHTML = '';
        const link = document.createElement('a');
        link.className = 'button primary';
        link.href = `/api/download?path=${encodeURIComponent(result.id)}`;
        link.textContent = `Download ${result.filename}`;
        $('editorStatus').appendChild(link);
        showToast('Clip exported successfully.', 'success');
      } catch (error) {
        $('editorStatus').textContent = error.message;
        showToast(error.message, 'error');
      } finally {
        button.disabled = false;
      }
    });
  });
}

function seekPlayer(seconds) {
  const player = $('player');
  if (Number.isFinite(player.duration)) player.currentTime = Math.max(0, Math.min(player.duration, player.currentTime + seconds));
}

function fmtTime(seconds) {
  if (!Number.isFinite(seconds)) return '00:00';
  const minutes = Math.floor(seconds / 60);
  const remaining = Math.floor(seconds % 60);
  return `${String(minutes).padStart(2, '0')}:${String(remaining).padStart(2, '0')}`;
}

let talkSession = null;

async function startTalk(cameraId) {
  if (talkSession) return;
  if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
    showToast('Your browser does not provide microphone recording here. Open LocalCam in a secure browser context.', 'error');
    return;
  }

  try {
    const media = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true }
    });
    const preferred = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus'].find((type) => MediaRecorder.isTypeSupported(type));
    const recorder = preferred ? new MediaRecorder(media, { mimeType: preferred }) : new MediaRecorder(media);
    const chunks = [];
    talkSession = { cameraId, media, recorder, chunks, timer: null };

    recorder.addEventListener('dataavailable', (event) => {
      if (event.data?.size) chunks.push(event.data);
    });
    recorder.addEventListener('stop', async () => {
      const current = talkSession;
      if (!current) return;
      clearTimeout(current.timer);
      current.media.getTracks().forEach((track) => track.stop());
      talkSession = null;
      if (!chunks.length) return;

      try {
        const blob = new Blob(chunks, { type: preferred || recorder.mimeType || 'audio/webm' });
        const buffer = await blob.arrayBuffer();
        const bytes = new Uint8Array(buffer);
        let binary = '';
        for (let offset = 0; offset < bytes.length; offset += 0x8000) {
          binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
        }
        await api(`/api/talk/${encodeURIComponent(cameraId)}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ audio_base64: btoa(binary), mime: blob.type })
        });
        showToast('Talk audio sent to the camera.', 'success');
      } catch (error) {
        showToast(error.message || 'Talk failed.', 'error');
      }
    });
    recorder.start(250);
    talkSession.timer = setTimeout(() => stopTalk(), 10000);
    showToast('Talk mode active — release the button when finished.', 'info');
  } catch (error) {
    showToast(error.message || 'Microphone access was denied.', 'error');
  }
}

function stopTalk() {
  if (!talkSession) return;
  if (talkSession.recorder.state !== 'inactive') talkSession.recorder.stop();
}

window.localcamStartTalk = startTalk;
window.localcamStopTalk = stopTalk;

async function loadEvents() {
  const day = $('eventsDate').value || today();
  const camera = $('eventsCamera').value || '';
  const rows = await api(`/api/events?date=${encodeURIComponent(day)}&camera=${encodeURIComponent(camera)}`, { timeoutMs: 5000 });
  $('eventsCount').textContent = `${rows.length} events`;
  $('eventsList').innerHTML = rows.length ? rows.map((event) => {
    const snapshot = event.snapshot_path ? `/api/event-snapshot/${encodeURIComponent(event.snapshot_path.split(/[\\/]/).slice(-3).join('/')).replaceAll('%2F', '/')}` : '';
    return `<div class="event">
      ${snapshot ? `<img class="thumb" src="${snapshot}" loading="lazy" alt="Motion event">` : '<div class="thumb"></div>'}
      <div class="event-main"><b>${esc(event.camera_name)}</b><small>${esc(event.started_at.replace('T', ' '))}${event.ended_at ? ` → ${esc(event.ended_at.replace('T', ' '))}` : ' · active'}</small></div>
      ${can('control') ? `<button class="icon-btn" data-ack="${event.id}">Acknowledge</button>` : ''}
      <span class="pill warn">MOTION</span>
    </div>`;
  }).join('') : '<div class="muted">No events for this date.</div>';
}

function setupEventActions() {
  $('eventsSearchBtn').addEventListener('click', () => loadEvents().catch((error) => showToast(error.message, 'error')));
  $('eventsDate').addEventListener('change', () => loadEvents().catch((error) => showToast(error.message, 'error')));
  $('eventsCamera').addEventListener('change', () => loadEvents().catch((error) => showToast(error.message, 'error')));
  $('eventsList').addEventListener('click', async (event) => {
    const button = event.target.closest('[data-ack]');
    if (!button) return;
    try {
      await api(`/api/events/${button.dataset.ack}/ack`, { method: 'POST' });
      await loadEvents();
      showToast('Event acknowledged.', 'success');
    } catch (error) {
      showToast(error.message, 'error');
    }
  });
}

async function loadSettings() {
  const settings = await api('/api/settings', { timeoutMs: 5000 });
  state.settings = settings;
  $('sRecordRoot').value = settings.record_root ?? '';
  syncPathDisplay('sRecordRoot', 'sRecordRootFull');
  $('sSnapshotRoot').value = settings.snapshot_root ?? '';
  syncPathDisplay('sSnapshotRoot', 'sSnapshotRootFull');
  $('sRecordMode').value = settings.record_mode ?? 'manual';
  $('sSegment').value = settings.segment_minutes ?? 10;
  $('sMinFree').value = settings.min_free_gb ?? 10;
  $('sRetention').value = settings.max_retention_days ?? 30;
  $('sBind').value = settings.web_bind ?? '0.0.0.0';
  $('sPort').value = settings.web_port ?? 8765;
  $('sFps').value = settings.web_live_fps ?? 5;
  $('sWidth').value = settings.web_live_width ?? 960;
  $('sFfmpeg').value = settings.ffmpeg_path ?? 'ffmpeg';
  $('sSessionHours').value = settings.web_session_hours ?? 12;
  $('sEnabled').checked = !!settings.web_enabled;
  $('sAutoOpen').checked = !!settings.web_auto_open;
  $('sAuth').checked = !!settings.web_auth_enabled;
  $('mEnabled').checked = !!settings.motion?.enabled;
  $('mInterval').value = settings.motion?.interval_seconds ?? 0.5;
  $('mThreshold').value = settings.motion?.threshold ?? 6;
  $('mFraction').value = settings.motion?.min_changed_fraction ?? 0.008;
  $('mCooldown').value = settings.motion?.cooldown_seconds ?? 15;
  $('mSnapshots').checked = !!settings.motion?.save_event_snapshots;
  $('mNotifications').checked = !!settings.notifications_enabled;
  state.cameraEditorDirty = false;
  renderCameraEditor(settings.cameras || []);
  renderMotionCameraPicker(settings.cameras || []);
}

function renderMotionCameraPicker(cameras) {
  const box = $('motionCameraPicker');
  if (!box) return;

  const items = Array.isArray(cameras) ? cameras.filter((camera) => camera?.id && camera?.url) : [];
  const configured = Array.isArray(state.settings?.motion_cameras)
    ? state.settings.motion_cameras.map((id) => String(id))
    : [];
  const configuredSet = new Set(configured);
  const defaultAll = configured.length === 0;

  box.innerHTML = items.length ? `
    <div class="motion-camera-head">
      <div>
        <strong>Cameras used for motion detection</strong>
        <span class="hint">Select one or more cameras. Each preview below is the actual live feed LocalCam will analyze.</span>
      </div>
      <div class="row-actions">
        <button type="button" class="icon-btn" data-motion-select-all>Select all</button>
        <button type="button" class="icon-btn" data-motion-clear-all>Clear all</button>
      </div>
    </div>
    <div class="motion-camera-grid">
      ${items.map((camera) => {
        const selected = defaultAll || configuredSet.has(String(camera.id));
        return `
          <label class="motion-camera-card">
            <div class="motion-camera-preview">
              <img data-motion-preview data-camera-id="${esc(camera.id)}" src="/live/${encodeURIComponent(camera.id)}.mjpg?quality=medium" alt="${esc(camera.name)} live preview">
              <span>LIVE</span>
            </div>
            <div class="motion-camera-info">
              <input type="checkbox" data-motion-camera value="${esc(camera.id)}" ${selected ? 'checked' : ''}>
              <span><strong>${esc(camera.name)}</strong><small>${esc(camera.url)}</small></span>
            </div>
          </label>`;
      }).join('')}
    </div>
    <div class="motion-camera-summary" id="motionCameraSummary"></div>
  ` : '<div class="muted">Add at least one camera before choosing motion detection cameras.</div>';

  const refreshState = () => {
    const selected = box.querySelectorAll('[data-motion-camera]:checked').length;
    const total = box.querySelectorAll('[data-motion-camera]').length;
    const enabled = !!$('mEnabled')?.checked;
    const summary = box.querySelector('#motionCameraSummary');
    if (summary) summary.textContent = enabled
      ? (selected ? `Motion will analyze ${selected} of ${total} camera${total === 1 ? '' : 's'}.` : `No specific cameras selected — motion detection will use all ${total} configured camera${total === 1 ? '' : 's'}.`)
      : `Motion detection is disabled. ${selected} of ${total} cameras are selected for when you enable it.`;
    box.classList.toggle('motion-picker-disabled', !enabled);
    box.querySelectorAll('[data-motion-camera],[data-motion-select-all],[data-motion-clear-all]').forEach((element) => {
      element.disabled = !enabled;
    });
    box.classList.toggle('motion-picker-invalid', enabled && selected === 0);
  };

  box.querySelectorAll('[data-motion-camera]').forEach((input) => input.addEventListener('change', refreshState));
  box.querySelector('[data-motion-select-all]')?.addEventListener('click', () => {
    box.querySelectorAll('[data-motion-camera]').forEach((input) => { input.checked = true; });
    refreshState();
  });
  box.querySelector('[data-motion-clear-all]')?.addEventListener('click', () => {
    box.querySelectorAll('[data-motion-camera]').forEach((input) => { input.checked = false; });
    refreshState();
  });
  box.querySelectorAll('[data-motion-preview]').forEach((img) => img.addEventListener('error', (event) => {
    const empty = document.createElement('div');
    empty.className = 'motion-preview-empty';
    empty.textContent = 'Preview unavailable';
    event.currentTarget.replaceWith(empty);
  }));
  refreshState();
}
function renderCameraEditor(cameras) {
  $('cameraEditor').innerHTML = cameras.length ? cameras.map((camera, index) => `
    <div class="camera-block" data-index="${index}" data-camera-id="${esc(camera.id)}">
      <div class="camera-row">
        <input data-k="id" value="${esc(camera.id)}" placeholder="ID">
        <input data-k="name" value="${esc(camera.name)}" placeholder="Name">
        <input data-k="url" value="${esc(camera.url)}" placeholder="rtsp://IP:554/live/ch00_0">
        <input data-k="username" value="${esc(camera.username || 'admin')}" placeholder="Username">
        <input data-k="password" type="password" placeholder="Keep existing password">
        <button class="icon-btn" data-test-camera="${esc(camera.id)}">Test RTSP</button>
        <button class="icon-btn danger-text" data-remove-camera="${esc(camera.id)}">Remove</button>
      </div>
      <div class="ptz-editor">
        <label><input data-k="ptzEnabled" type="checkbox" ${camera.ptz?.enabled ? 'checked' : ''}> Enable ONVIF PTZ</label>
        <input data-k="ptzHost" value="${esc(camera.ptz?.host || '')}" placeholder="ONVIF host/IP">
        <input data-k="ptzPort" type="number" value="${camera.ptz?.port || 80}" placeholder="Port">
        <input data-k="ptzUser" value="${esc(camera.ptz?.username || '')}" placeholder="ONVIF username">
        <input data-k="ptzPass" type="password" placeholder="Keep existing ONVIF password">
        <button class="icon-btn" data-test-ptz="${esc(camera.id)}">Test PTZ</button>
      </div>
    </div>`).join('') : '<div class="muted">No cameras configured.</div>';
}

function collectCameras() {
  const blocks = [...document.querySelectorAll('.camera-block')];
  if (!blocks.length) {
    return Array.isArray(state.settings?.cameras) ? state.settings.cameras.map((camera) => ({ ...camera, ptz: { ...(camera.ptz || {}) } })) : [];
  }
  return blocks.map((block, index) => {
    const get = (key) => block.querySelector(`[data-k="${key}"]`);
    const originalId = String(block.dataset.cameraId || '').trim();
    const old = state.settings.cameras.find((camera) => String(camera.id) === originalId) || {};
    const oldPtz = old.ptz || {};
    return {
      id: (get('id')?.value || '').trim() || `camera-${index + 1}`,
      name: (get('name')?.value || '').trim() || `Camera ${index + 1}`,
      url: (get('url')?.value || '').trim(),
      username: (get('username')?.value || '').trim() || 'admin',
      password: get('password')?.value || old.password || '',
      ptz: {
        enabled: !!get('ptzEnabled')?.checked,
        host: (get('ptzHost')?.value || '').trim(),
        port: Number(get('ptzPort')?.value || 80),
        username: (get('ptzUser')?.value || '').trim(),
        password: get('ptzPass')?.value || oldPtz.password || ''
      }
    };
  }).filter((camera) => camera.url);
}

function markCameraEditorDirty() {
  state.cameraEditorDirty = true;
}

function setupCameraSettings() {
  $('addCamera').addEventListener('click', () => {
    markCameraEditorDirty();
    state.settings.cameras.push({ id: `camera-${state.settings.cameras.length + 1}`, name: `Camera ${state.settings.cameras.length + 1}`, url: '', username: 'admin', password: '', ptz: { enabled: false, host: '', port: 80, username: '', password: '' } });
    renderCameraEditor(state.settings.cameras);
    renderMotionCameraPicker(state.settings.cameras);
  });

  $('cameraEditor').addEventListener('input', markCameraEditorDirty);
  $('cameraEditor').addEventListener('change', markCameraEditorDirty);

  $('cameraEditor').addEventListener('click', async (event) => {
    const remove = event.target.closest('[data-remove-camera]');
    const test = event.target.closest('[data-test-camera]');
    const ptzTest = event.target.closest('[data-test-ptz]');
    try {
      if (remove) {
        markCameraEditorDirty();
        const removeId = String(remove.dataset.removeCamera || '');
        state.settings.cameras = state.settings.cameras.filter((camera) => String(camera.id) !== removeId);
        renderCameraEditor(state.settings.cameras);
        renderMotionCameraPicker(state.settings.cameras);
      }
      if (test) await testCamera(test.dataset.testCamera);
      if (ptzTest) await testPTZ(ptzTest.dataset.testPtz);
    } catch (error) {
      showToast(error.message, 'error');
    }
  });
}

async function testCamera(id) {
  const data = await api(`/api/health?camera=${encodeURIComponent(id)}`);
  const probe = data[id]?.probe;
  if (probe?.ok) showToast('RTSP test passed.', 'success');
  else showToast(`RTSP test failed: ${probe?.error || 'unknown error'}`, 'error');
}

async function testPTZ(id) {
  const result = await api(`/api/ptz/test?camera=${encodeURIComponent(id)}`);
  if (result.ok) showToast('ONVIF/PTZ connection passed.', 'success');
  else showToast('PTZ test failed.', 'error');
}

async function saveSettings() {
  if (!state.settings) return;
  const payload = {
    ...state.settings,
    record_root: $('sRecordRoot').value.trim(),
    snapshot_root: $('sSnapshotRoot').value.trim(),
    record_mode: $('sRecordMode').value,
    segment_minutes: Number($('sSegment').value),
    min_free_gb: Number($('sMinFree').value),
    max_retention_days: Number($('sRetention').value),
    web_bind: $('sBind').value,
    web_port: Number($('sPort').value),
    web_live_fps: Number($('sFps').value),
    web_live_width: Number($('sWidth').value),
    ffmpeg_path: $('sFfmpeg').value.trim() || 'ffmpeg',
    web_session_hours: Number($('sSessionHours').value),
    web_enabled: $('sEnabled').checked,
    web_auto_open: $('sAutoOpen').checked,
    web_auth_enabled: $('sAuth').checked,
    notifications_enabled: $('mNotifications').checked,
    motion: {
      ...(state.settings.motion || {}),
      enabled: $('mEnabled').checked,
      interval_seconds: Number($('mInterval').value),
      threshold: Number($('mThreshold').value),
      min_changed_fraction: Number($('mFraction').value),
      cooldown_seconds: Number($('mCooldown').value),
      save_event_snapshots: $('mSnapshots').checked
    },
    motion_cameras: [...document.querySelectorAll('[data-motion-camera]:checked')].map((input) => input.value),
    // Saving Motion/Web-server settings must not rewrite cameras unless the
    // camera editor was actually changed in this page session.
    cameras: state.cameraEditorDirty ? collectCameras() : (state.settings.cameras || [])
  };
  const result = await api('/api/settings', { timeoutMs: 5000, method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
  // The server only acknowledges the queue. Keep the edited values in the UI;
  // persistence and any FFmpeg rebuild happen on the background worker.
  state.settings = { ...state.settings, ...payload, cameras: payload.cameras };
  state.cameraEditorDirty = false;
  $('settingsStatus').textContent = result.queued
    ? 'Settings queued. LocalCam is saving them in the background.'
    : 'Settings saved.';
  renderCameraEditor(state.settings.cameras || []);
  renderMotionCameraPicker(state.settings.cameras || []);
  showToast(result.queued ? 'Settings queued for saving.' : 'Settings saved.', 'success');

  const refreshRuntime = async (attempt = 0) => {
    try {
      await Promise.all([loadInfo(), loadStreams()]);
      if (attempt < 12 && state.settings?.cameras?.length && !state.streams?.length) {
        setTimeout(() => refreshRuntime(attempt + 1), 300);
      }
    } catch (error) {
      reportClientIssue('refresh', 'Post-save dashboard refresh failed', error?.message || String(error));
    }
  };
  refreshRuntime();
}

async function loadUsers(force = false) {
  if (state.auth?.user?.role !== 'admin') return;
  const users = !force && Array.isArray(state.settings?.users)
    ? state.settings.users
    : await api('/api/users');
  state.settings = { ...(state.settings || {}), users };
  $('userEditor').innerHTML = users.map((user) => `
    <div class="user-row">
      <div><b>${esc(user.username)}</b><small>${esc(user.role)} · ${user.enabled ? 'enabled' : 'disabled'} · last login ${esc(user.last_login || 'never')}</small></div>
      <div>
        <select data-user-role="${user.id}"><option value="viewer" ${user.role === 'viewer' ? 'selected' : ''}>Viewer</option><option value="operator" ${user.role === 'operator' ? 'selected' : ''}>Operator</option><option value="admin" ${user.role === 'admin' ? 'selected' : ''}>Admin</option></select>
        <button class="icon-btn" data-toggle-user="${user.id}" data-enabled="${user.enabled ? '1' : '0'}">${user.enabled ? 'Disable' : 'Enable'}</button>
        <button class="icon-btn" data-delete-user="${user.id}">Delete</button>
      </div>
    </div>`).join('');
}

function setupSecurityActions() {
  $('createUser').addEventListener('click', async () => {
    try {
      const username = $('newUsername').value.trim();
      const password = $('newPassword').value;
      if (username.length < 3 || password.length < 10) throw new Error('Username must be 3+ characters and password must be 10+ characters.');
      await api('/api/users', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username, password, role: $('newRole').value }) });
      $('newUsername').value = '';
      $('newPassword').value = '';
      await loadUsers(true);
      showToast('User created.', 'success');
    } catch (error) { showToast(error.message, 'error'); }
  });

  $('userEditor').addEventListener('change', async (event) => {
    const select = event.target.closest('[data-user-role]');
    if (!select) return;
    try {
      await api(`/api/users/${select.dataset.userRole}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ role: select.value }) });
      showToast('User role updated.', 'success');
      await loadUsers(true);
    } catch (error) { showToast(error.message, 'error'); }
  });

  $('userEditor').addEventListener('click', async (event) => {
    const toggle = event.target.closest('[data-toggle-user]');
    const remove = event.target.closest('[data-delete-user]');
    try {
      if (toggle) {
        await api(`/api/users/${toggle.dataset.toggleUser}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ enabled: toggle.dataset.enabled !== '1' }) });
        await loadUsers(true);
      }
      if (remove) {
        if (!confirm('Delete this user account?')) return;
        await api(`/api/users/${remove.dataset.deleteUser}`, { method: 'DELETE' });
        await loadUsers(true);
      }
    } catch (error) { showToast(error.message, 'error'); }
  });
}

function syncPathDisplay(inputId, displayId) {
  const input = $(inputId);
  const display = $(displayId);
  if (!input || !display) return;
  const value = String(input.value || '');
  display.textContent = value ? value : 'Not selected';
  display.title = value;
  input.title = value;
}

async function pickFolder(kind) {
  const inputId = kind === 'snapshot' ? 'sSnapshotRoot' : 'sRecordRoot';
  const displayId = kind === 'snapshot' ? 'sSnapshotRootFull' : 'sRecordRootFull';
  const button = document.querySelector(`[data-pick-folder="${kind}"]`);
  if (button) button.disabled = true;
  try {
    const current = $(inputId)?.value || '';
    const data = await api(`/api/folder-picker?kind=${encodeURIComponent(kind)}&current=${encodeURIComponent(current)}`);
    if (data.cancelled) return;
    if (!data.path) throw new Error('No folder was selected.');
    $(inputId).value = data.path;
    syncPathDisplay(inputId, displayId);
  } catch (error) {
    showToast(error.message || 'Could not choose a folder.', 'error');
  } finally {
    if (button) button.disabled = false;
  }
}

function setupFolderPickers() {
  document.querySelectorAll('[data-pick-folder]').forEach((button) => {
    button.addEventListener('click', () => pickFolder(button.dataset.pickFolder));
  });
  ['sRecordRoot', 'sSnapshotRoot'].forEach((id) => {
    $(id)?.addEventListener('input', () => {
      syncPathDisplay(id, id === 'sRecordRoot' ? 'sRecordRootFull' : 'sSnapshotRootFull');
    });
  });
  syncPathDisplay('sRecordRoot', 'sRecordRootFull');
  syncPathDisplay('sSnapshotRoot', 'sSnapshotRootFull');
}

async function testMotion() {
  const button = $('motionTest');
  const status = $('motionTestStatus');
  if (!button || !status) return;
  button.disabled = true;
  status.textContent = 'Reading live motion detector status…';
  try {
    const data = await api('/api/motion/test');
    const rows = Object.values(data);
    if (!rows.length) {
      status.textContent = 'No cameras are configured.';
      return;
    }
    const parts = rows.map((row) => {
      const d = row.diagnostics || {};
      if (!d.enabled) return `${row.name}: detector disabled`;
      if (d.last_error) return `${row.name}: detector error — ${d.last_error}`;
      const checked = d.last_check_at ? new Date(Number(d.last_check_at) * 1000).toLocaleTimeString() : 'never';
      return `${row.name}: ${d.active ? 'MOTION' : 'quiet'} · difference ${Number(d.mean_difference || 0).toFixed(1)} · changed ${(Number(d.changed_fraction || 0) * 100).toFixed(1)}% · checked ${checked}`;
    });
    status.textContent = parts.join('  |  ');
  } catch (error) {
    status.textContent = error.message || 'Motion diagnostic failed.';
  } finally {
    button.disabled = false;
  }
}

async function refreshDiagnostics() {
  const output = $('diagnosticsOutput');
  const button = $('diagnosticsRefresh');
  if (!output) return;
  if (button) { button.disabled = true; button.textContent = 'Checking…'; }
  output.textContent = 'Collecting runtime diagnostics…';
  try {
    const data = await api('/api/diagnostics', { timeoutMs: 5000 });
    const lines = [
      `Active HTTP/Python threads: ${data.threads ?? '—'}`,
      `Configured stream objects: ${data.streams ?? '—'}`,
      `Shared preview workers: ${data.preview_workers?.length ?? 0}`,
      ...((data.preview_workers || []).map((worker, index) =>
        `Worker ${index + 1}: ${worker.source} · listeners ${worker.listeners} · alive ${worker.alive} · frames ${worker.frames} · restarts ${worker.restarts} · failures ${worker.consecutive_failures}${worker.last_error ? ` · error: ${worker.last_error}` : ''}`
      )),
      `Log file: ${data.log_file || 'localcam.log'}`
    ];
    output.textContent = lines.join('\n');
  } catch (error) {
    output.textContent = error.message || 'Diagnostics request failed.';
    reportClientIssue('diagnostics', 'Diagnostics request failed', error.message || String(error));
  } finally {
    if (button) { button.disabled = false; button.textContent = 'Check diagnostics'; }
  }
}

function setupSettingsActions() {
  $('motionTest')?.addEventListener('click', () => testMotion());
  $('diagnosticsRefresh')?.addEventListener('click', () => refreshDiagnostics());
  $('mEnabled')?.addEventListener('change', () => renderMotionCameraPicker(state.settings?.cameras || []));
  $('saveSettings').addEventListener('click', async () => {
    const button = $('saveSettings');
    button.disabled = true;
    try { await saveSettings(); }
    catch (error) { $('settingsStatus').textContent = error.message; showToast(error.message, 'error'); }
    finally { button.disabled = false; }
  });
}

function setupBackupActions() {
  $('downloadBackup').addEventListener('click', () => { location.href = '/api/backup'; });
  $('restoreBackup').addEventListener('click', async () => {
    const file = $('restoreFile').files[0];
    if (!file) { showToast('Choose a backup file first.', 'error'); return; }
    if (!confirm('Restore this LocalCam backup? Current configuration and event database will be replaced.')) return;
    try {
      const buffer = await file.arrayBuffer();
      let binary = '';
      const bytes = new Uint8Array(buffer);
      const chunk = 0x8000;
      for (let offset = 0; offset < bytes.length; offset += chunk) binary += String.fromCharCode(...bytes.subarray(offset, offset + chunk));
      const archiveBase64 = btoa(binary);
      const result = await api('/api/admin/restore', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ archive_base64: archiveBase64 }) });
      $('backupStatus').textContent = result.message || 'Backup restored. Restart LocalCam if needed.';
      showToast('Backup restored.', 'success');
    } catch (error) { $('backupStatus').textContent = error.message; showToast(error.message, 'error'); }
  });
}

function setupTopActions() {
  $('refresh').addEventListener('click', async () => {
    const button = $('refresh');
    button.disabled = true;
    try {
      await Promise.all([loadInfo(), loadStreams()]);
      showToast('Dashboard refreshed.', 'success');
    } catch (error) { showToast(error.message, 'error'); }
    finally { button.disabled = false; }
  });

  $('logout').addEventListener('click', async () => {
    try { await api('/api/auth/logout', { method: 'POST' }); }
    finally { location.replace('/login'); }
  });

  $('clock').textContent = new Date().toLocaleTimeString();
  setInterval(() => { $('clock').textContent = new Date().toLocaleTimeString(); }, 1000);
}

async function init() {
  setupNavigation();
  setupLiveQuality();
  setupDashboardActions();
  setupArchiveActions();
  setupClipEditorActions();
  setupEventActions();
  setupCameraSettings();
  setupSecurityActions();
  setupSettingsActions();
  setupFolderPickers();
  setupBackupActions();
  setupTopActions();
  initDates();

  try {
    await loadAuth();
    await Promise.all([loadInfo(), loadStreams()]);
  } catch (error) {
    console.error('LocalCam UI initialization failed:', error);
    showToast(error.message || 'Could not initialize the dashboard.', 'error');
  }
}

document.addEventListener('DOMContentLoaded', init);
