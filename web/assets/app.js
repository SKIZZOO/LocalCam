const state = { info: null, settings: null, streams: [], lastEventId: 0 };
const $ = (id) => document.getElementById(id);

const esc = (value) => String(value ?? '').replace(/[&<>\"]/g, (char) => ({
  '&': '&amp;',
  '<': '&lt;',
  '>': '&gt;',
  '\"': '&quot;'
}[char]));

function fmtBytes(value) {
  let n = Number(value || 0);
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let index = 0;
  while (n >= 1024 && index < units.length - 1) {
    n /= 1024;
    index += 1;
  }
  return `${n.toFixed(index ? 1 : 0)} ${units[index]}`;
}

function today() {
  return new Date().toISOString().slice(0, 10);
}

async function api(url, options = {}) {
  const response = await fetch(url, options);
  if (response.status === 401) {
    location.href = '/login';
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
}

function showPage(name) {
  document.querySelectorAll('.page').forEach((node) => node.classList.remove('active'));
  document.querySelectorAll('.nav').forEach((node) => node.classList.toggle('active', node.dataset.page === name));
  $(`page-${name}`).classList.add('active');

  const titles = {
    dashboard: ['Dashboard', 'Live monitoring, recording, and system status.'],
    archive: ['Archive', 'Find, play, and download recordings.'],
    events: ['Events', 'Motion history and captured event snapshots.'],
    settings: ['Settings', 'Storage, server, motion, cameras, and security.']
  };
  $('title').textContent = titles[name][0];
  $('subtitle').textContent = titles[name][1];

  if (name === 'archive') {
    loadRecordings();
  } else if (name === 'events') {
    loadEvents();
  } else if (name === 'settings') {
    loadSettings();
  }
}

function setupNav() {
  document.querySelectorAll('.nav').forEach((button) => button.addEventListener('click', () => showPage(button.dataset.page)));
  document.querySelectorAll('.tab').forEach((button) => button.addEventListener('click', () => {
    document.querySelectorAll('.tab,.settings-panel').forEach((node) => node.classList.remove('active'));
    button.classList.add('active');
    $(`tab-${button.dataset.tab}`).classList.add('active');
  }));
}

function cameraCard(stream) {
  const badge = stream.motion ? 'MOTION' : stream.recording ? 'REC' : stream.online ? 'LIVE' : 'OFFLINE';
  const cls = stream.motion ? 'warn' : stream.online ? 'good' : '';
  return `<article class="cam">
    <div class="cam-head">
      <div class="cam-title">${esc(stream.name)}</div>
      <span class="pill ${cls}">${badge}</span>
    </div>
    <div class="cam-body" data-camera="${esc(stream.id)}">
      <img src="/live/${encodeURIComponent(stream.id)}.mjpg" alt="${esc(stream.name)}" ondblclick="fullscreenCamera('${esc(stream.id)}')">
      <div class="cam-overlay">RTSP · local LAN</div>
    </div>
    <div class="cam-foot">
      <span>${stream.online ? 'Connected' : 'Waiting for stream'}</span>
      <div class="cam-actions">
        <button class="small-btn" onclick="capture('${esc(stream.id)}')">Snapshot</button>
        <button class="small-btn" onclick="fullscreenCamera('${esc(stream.id)}')">Fullscreen</button>
        <button class="small-btn" onclick="toggleRecord('${esc(stream.id)}',${stream.recording})">${stream.recording ? 'Stop' : 'Record'}</button>
      </div>
    </div>
  </article>`;
}

function renderDashboard() {
  const grid = $('cameraGrid');
  if (!state.streams.length) {
    grid.innerHTML = '<div class="panel">No camera streams configured.</div>';
    return;
  }
  grid.innerHTML = state.streams.map(cameraCard).join('');
}

async function loadInfo() {
  try {
    state.info = await api('/api/info');
    $('sideStatus').textContent = 'Online';
    $('sideUrl').textContent = state.info.url.replace('http://', '');
    $('version').textContent = `v${state.info.version}`;
    $('mStreams').textContent = state.info.streams.length;
    $('mFree').textContent = state.info.storage.free_human;
    $('mDrive').textContent = state.info.storage.path;
    $('mUsed').textContent = `${state.info.storage.used_percent}%`;
    $('diskBar').style.width = `${Math.min(100, state.info.storage.used_percent)}%`;
    $('mMode').textContent = String(state.info.record_mode || 'manual').toUpperCase();
    $('sideDot').classList.remove('bad');
  } catch (error) {
    console.error(error);
    $('sideStatus').textContent = 'Server error';
    $('sideDot').classList.add('bad');
  }
}

async function loadStreams() {
  try {
    state.streams = await api('/api/streams');
    renderDashboard();
    fillCameraSelects();
  } catch (error) {
    console.error(error);
  }
}

function fillCameraSelects() {
  const options = '<option value="">All cameras</option>' + state.streams.map((stream) => `<option value="${esc(stream.id)}">${esc(stream.name)}</option>`).join('');
  $('archiveCamera').innerHTML = options;
  $('eventsCamera').innerHTML = options;
}

async function capture(id) {
  const response = await fetch(`/api/snapshot/${encodeURIComponent(id)}`);
  if (!response.ok) return;
  const blob = await response.blob();
  const anchor = document.createElement('a');
  anchor.href = URL.createObjectURL(blob);
  anchor.download = `${id}-${Date.now()}.jpg`;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(anchor.href), 1000);
}

function fullscreenCamera(id) {
  const node = document.querySelector(`[data-camera="${CSS.escape(id)}"]`);
  if (node && node.requestFullscreen) node.requestFullscreen().catch(() => {});
}

async function toggleRecord(id, recording) {
  await api(`/api/record/${encodeURIComponent(id)}/${recording ? 'stop' : 'start'}`, { method: 'POST' });
  await Promise.all([loadInfo(), loadStreams()]);
}

function drawTimeline(items, events) {
  const box = $('timeline');
  box.innerHTML = '';
  for (let hour = 0; hour < 24; hour += 1) {
    const node = document.createElement('div');
    node.className = 'hour';
    node.style.left = `${(hour / 24) * 100}%`;
    node.textContent = String(hour).padStart(2, '0');
    box.appendChild(node);
  }

  items.forEach((item) => {
    const start = new Date(item.start);
    const end = new Date(item.end);
    const startSeconds = start.getHours() * 3600 + start.getMinutes() * 60 + start.getSeconds();
    const duration = Math.max(1, (end - start) / 1000);
    const node = document.createElement('div');
    node.className = 'segment';
    node.style.left = `${(startSeconds / 86400) * 100}%`;
    node.style.width = `${Math.max(0.3, (duration / 86400) * 100)}%`;
    node.title = `${item.camera} · click to play`;
    node.onclick = () => playRecording(item.id, item.id.split('/').pop());
    box.appendChild(node);
  });

  events.forEach((event) => {
    const start = new Date(event.started_at);
    const seconds = start.getHours() * 3600 + start.getMinutes() * 60 + start.getSeconds();
    const node = document.createElement('div');
    node.className = 'event';
    node.style.left = `${(seconds / 86400) * 100}%`;
    node.title = `${event.camera_name} · ${start.toLocaleTimeString()}`;
    box.appendChild(node);
  });
}

async function loadTimeline() {
  const date = $('archiveDate').value || today();
  $('timelineDate').textContent = date;
  const camera = $('archiveCamera').value || '';
  const [segments, events] = await Promise.all([
    api(`/api/timeline?date=${encodeURIComponent(date)}&camera=${encodeURIComponent(camera)}`),
    api(`/api/events?date=${encodeURIComponent(date)}&camera=${encodeURIComponent(camera)}`)
  ]);
  drawTimeline(segments, events);
}

async function loadRecordings() {
  const date = $('archiveDate').value || today();
  const camera = $('archiveCamera').value || '';
  const q = $('archiveSearch').value || '';
  const rows = await api(`/api/recordings?date=${encodeURIComponent(date)}&camera=${encodeURIComponent(camera)}&q=${encodeURIComponent(q)}`);
  $('archiveCount').textContent = `${rows.length} segments`;
  $('recordingsList').innerHTML = rows.length ? rows.map((row) => `<div class="row">
    <div><b>${esc(row.camera)}</b><small>${esc(row.time)} · ${esc(row.name)} · ${esc(row.size_human)}</small></div>
    <div class="row-actions"><button class="icon-btn" onclick="playRecording('${esc(row.id)}','${esc(row.name)}')">Play</button><a class="icon-btn" href="/api/download?path=${encodeURIComponent(row.id)}">Download</a></div>
  </div>`).join('') : '<div class="muted">No recordings match the selected filters.</div>';
  await loadTimeline();
}

function playRecording(id, name) {
  $('player').src = `/api/media?path=${encodeURIComponent(id)}`;
  $('playbackName').textContent = name;
  $('player').play().catch(() => {});
}

async function loadEvents() {
  const date = $('eventsDate').value || today();
  const camera = $('eventsCamera').value || '';
  const rows = await api(`/api/events?date=${encodeURIComponent(date)}&camera=${encodeURIComponent(camera)}`);
  $('eventsCount').textContent = `${rows.length} events`;
  $('eventsList').innerHTML = rows.length ? rows.map((event) => {
    const snapshot = event.snapshot_path ? `/api/event-snapshot/${encodeURIComponent(event.snapshot_path) .replaceAll('%2F', '/')}` : '';
    return `<div class="event">
      <div>${snapshot ? `<img class="thumb" src="${snapshot}" loading="lazy" alt="Motion event">` : '<div class="thumb"></div>'}</div>
      <div class="event-main"><b>${esc(event.camera_name)}</b><small>${esc(event.started_at.replace('T', ' '))}${event.ended_at ? ` → ${esc(event.ended_at.replace('T', ' '))}` : ' · active'}</small></div>
      <span><span class="pill warn">MOTION</span>${event.acknowledged ? '' : `<button class="icon-btn" onclick="ackEvent(${event.id})">Acknowledge</button>`}</span>
    </div>`;
  }).join('') : '<div class="muted">No events for this date.</div>';
}

async function ackEvent(id) {
  await api(`/api/events/${id}/ack`, { method: 'POST' });
  loadEvents();
}

async function loadSettings() {
  const settings = await api('/api/settings');
  state.settings = settings;
  $('sRecordRoot').value = settings.record_root;
  $('sSnapshotRoot').value = settings.snapshot_root;
  $('sRecordMode').value = settings.record_mode;
  $('sSegment').value = settings.segment_minutes;
  $('sMinFree').value = settings.min_free_gb;
  $('sRetention').value = settings.max_retention_days;
  $('sBind').value = settings.web_bind;
  $('sPort').value = settings.web_port;
  $('sFps').value = settings.web_live_fps;
  $('sWidth').value = settings.web_live_width;
  $('sEnabled').checked = !!settings.web_enabled;
  $('sAutoOpen').checked = !!settings.web_auto_open;
  $('mEnabled').checked = !!settings.motion.enabled;
  $('mInterval').value = settings.motion.interval_seconds;
  $('mThreshold').value = settings.motion.threshold;
  $('mFraction').value = settings.motion.min_changed_fraction;
  $('mCooldown').value = settings.motion.cooldown_seconds;
  $('mSnapshots').checked = !!settings.motion.save_event_snapshots;
  $('mNotifications').checked = !!settings.notifications_enabled;
  $('sAuth').checked = !!settings.web_auth_enabled;
  $('sPassword').value = '';
  renderCameraEditor(settings.cameras || []);
}

function renderCameraEditor(cameras) {
  $('cameraEditor').innerHTML = cameras.map((camera, index) => `<div class="camera-row" data-index="${index}">
    <input data-k="id" value="${esc(camera.id)}" placeholder="ID">
    <input data-k="name" value="${esc(camera.name)}" placeholder="Name">
    <input data-k="url" value="${esc(camera.url)}" placeholder="rtsp://IP:554/live/ch00_0">
    <input data-k="username" value="${esc(camera.username || 'admin')}" placeholder="Username">
    <input data-k="password" type="password" value="" placeholder="Keep existing password">
    <button class="icon-btn" onclick="testCamera('${esc(camera.id)}')">Test</button>
    <button class="icon-btn" onclick="removeCamera(${index})">Remove</button>
  </div>`).join('') || '<div class="muted">No cameras configured.</div>';
}

function collectCameras() {
  return [...document.querySelectorAll('.camera-row')]
    .map((row) => {
      const get = (key) => row.querySelector(`[data-k="${key}"]`)?.value || '';
      const old = state.settings.cameras[Number(row.dataset.index)] || {};
      const entered = get('password');
      return {
        id: get('id').trim() || `camera-${Number(row.dataset.index) + 1}`,
        name: get('name').trim() || `Camera ${Number(row.dataset.index) + 1}`,
        url: get('url').trim(),
        username: get('username').trim() || 'admin',
        password: entered || old.password || ''
      };
    })
    .filter((camera) => camera.url);
}

function removeCamera(index) {
  state.settings.cameras.splice(index, 1);
  renderCameraEditor(state.settings.cameras);
}

$('addCamera')?.addEventListener('click', () => {
  state.settings.cameras.push({
    id: `camera-${state.settings.cameras.length + 1}`,
    name: `Camera ${state.settings.cameras.length + 1}`,
    url: '',
    username: 'admin',
    password: ''
  });
  renderCameraEditor(state.settings.cameras);
});

async function saveSettings() {
  const payload = {
    record_root: $('sRecordRoot').value.trim(),
    snapshot_root: $('sSnapshotRoot').value.trim(),
    record_mode: $('sRecordMode').value,
    segment_minutes: Number($('sSegment').value),
    min_free_gb: Number($('sMinFree').value),
    max_retention_days: Number($('sRetention').value),
    notifications_enabled: $('mNotifications').checked,
    web_bind: $('sBind').value,
    web_port: Number($('sPort').value),
    web_live_fps: Number($('sFps').value),
    web_live_width: Number($('sWidth').value),
    web_enabled: $('sEnabled').checked,
    web_auto_open: $('sAutoOpen').checked,
    web_auth_enabled: $('sAuth').checked,
    web_password: $('sPassword').value,
    motion: {
      enabled: $('mEnabled').checked,
      interval_seconds: Number($('mInterval').value),
      threshold: Number($('mThreshold').value),
      min_changed_fraction: Number($('mFraction').value),
      cooldown_seconds: Number($('mCooldown').value),
      save_event_snapshots: $('mSnapshots').checked
    },
    cameras: collectCameras()
  };

  $('settingsStatus').textContent = 'Saving…';
  try {
    await api('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    $('settingsStatus').textContent = 'Saved.';
    setTimeout(() => location.reload(), 500);
  } catch (error) {
    $('settingsStatus').textContent = error.message;
  }
}

async function testCamera(id) {
  try {
    const data = await api(`/api/health?camera=${encodeURIComponent(id)}`);
    const item = data[id];
    alert(item?.probe?.ok ? 'RTSP test passed.' : `RTSP test failed: ${item?.probe?.error || 'Unknown error'}`);
  } catch (error) {
    alert(error.message);
  }
}

async function pollNotifications() {
  try {
    if (!state.settings) state.settings = await api('/api/settings').catch(() => null);
    if (!state.settings?.notifications_enabled) return;
    const rows = await api(`/api/events?date=${encodeURIComponent(today())}&camera=`);
    const latest = rows.reduce((max, row) => Math.max(max, Number(row.id || 0)), 0);
    if (!state.lastEventId) {
      state.lastEventId = latest;
      return;
    }
    const fresh = rows.filter((row) => Number(row.id || 0) > state.lastEventId);
    state.lastEventId = latest;
    if (fresh.length && 'Notification' in window) {
      if (Notification.permission === 'default') await Notification.requestPermission();
      if (Notification.permission === 'granted') {
        fresh.slice(0, 3).forEach((event) => new Notification('LocalCam motion detected', {
          body: `${event.camera_name} · ${event.started_at.replace('T', ' ')}`
        }));
      }
    }
  } catch (error) {
    console.debug(error);
  }
}

$('archiveSearchBtn').onclick = loadRecordings;
$('eventsSearchBtn').onclick = loadEvents;
$('archiveDate').onchange = loadRecordings;
$('archiveCamera').onchange = loadRecordings;
$('eventsDate').onchange = loadEvents;
$('eventsCamera').onchange = loadEvents;
$('refresh').onclick = () => Promise.all([loadInfo(), loadStreams()]);
$('saveSettings').onclick = saveSettings;
$('logout').onclick = () => api('/api/auth/logout', { method: 'POST' }).then(() => { location.href = '/login'; });

function init() {
  setupNav();
  $('archiveDate').value = today();
  $('eventsDate').value = today();
  Promise.all([loadInfo(), loadStreams()]);
  setInterval(loadInfo, 5000);
  setInterval(loadStreams, 8000);
  setInterval(() => { $('clock').textContent = new Date().toLocaleString(); }, 1000);
}

init();
setInterval(pollNotifications, 5000);
