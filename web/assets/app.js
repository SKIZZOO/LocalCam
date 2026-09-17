const state = { info: null, settings: null, streams: [], auth: null };
const $ = (id) => document.getElementById(id);

const esc = (value) => String(value ?? '').replace(/[&<>\"]/g, (char) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '\"': '&quot;'
}[char]));

function today() {
  return new Date().toISOString().slice(0, 10);
}

function can(action) {
  const role = state.auth?.user?.role || 'viewer';
  if (action === 'view') return true;
  if (role === 'admin') return true;
  return role === 'operator' && action === 'control';
}

async function api(url, options = {}) {
  const response = await fetch(url, {
    credentials: 'same-origin',
    ...options
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
}

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
    state.info = await api('/api/info');
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
  state.streams = await api('/api/streams');
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

function renderDashboard() {
  const grid = $('cameraGrid');
  if (!state.streams.length) {
    grid.innerHTML = '<div class="panel"><h2>No cameras configured</h2><p class="hint">Open Settings → Cameras to add your first RTSP camera.</p></div>';
    return;
  }

  grid.innerHTML = state.streams.map((stream) => {
    const badge = stream.motion ? 'MOTION' : stream.recording ? 'REC' : stream.online ? 'LIVE' : 'OFFLINE';
    const cls = stream.motion || stream.recording || stream.online ? 'good' : '';
    const recordButton = can('control') ?
      `<button class="small-btn" data-action="record" data-id="${esc(stream.id)}" data-recording="${stream.recording ? '1' : '0'}">${stream.recording ? 'Stop' : 'Record'}</button>` : '';
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

    return `<article class="cam">
      <div class="cam-head"><div class="cam-title">${esc(stream.name)}</div><span class="pill ${cls}">${badge}</span></div>
      <div class="cam-body"><img src="/live/${encodeURIComponent(stream.id)}.mjpg" alt="${esc(stream.name)}"><div class="cam-overlay">RTSP · local LAN</div></div>
      <div class="cam-foot"><span>${stream.online ? 'Connected' : 'Waiting for stream'}</span><div class="cam-actions"><button class="small-btn" data-action="snapshot" data-id="${esc(stream.id)}">Snapshot</button>${recordButton}</div></div>
      ${ptz}
    </article>`;
  }).join('');
}

function setupDashboardActions() {
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
  await api(`/api/record/${encodeURIComponent(id)}/${recording ? 'stop' : 'start'}`, { method: 'POST' });
  await Promise.all([loadInfo(), loadStreams()]);
  showToast(recording ? 'Recording stopped.' : 'Recording started.', 'success');
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
  const query = $('archiveSearch').value || '';
  const rows = await api(`/api/recordings?date=${encodeURIComponent(day)}&camera=${encodeURIComponent(camera)}&q=${encodeURIComponent(query)}`);
  $('archiveCount').textContent = `${rows.length} segments`;
  $('recordingsList').innerHTML = rows.length ? rows.map((row) => `
    <div class="row">
      <div><b>${esc(row.camera)}</b><small>${esc(row.time)} · ${esc(row.name)} · ${esc(row.size_human)}</small></div>
      <div class="row-actions"><button class="icon-btn" data-play="${esc(row.id)}" data-name="${esc(row.name)}">Play</button><a class="icon-btn" href="/api/download?path=${encodeURIComponent(row.id)}">Download</a></div>
    </div>`).join('') : '<div class="muted">No recordings match the selected filters.</div>';
  await loadTimeline();
}

async function loadTimeline() {
  const day = $('archiveDate').value || today();
  $('timelineDate').textContent = day;
  const camera = $('archiveCamera').value || '';
  const [segments, events] = await Promise.all([
    api(`/api/timeline?date=${encodeURIComponent(day)}&camera=${encodeURIComponent(camera)}`),
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
    segment.title = `${item.camera} · ${start.toLocaleTimeString()}`;
    segment.addEventListener('click', () => playRecording(item.id, item.id.split('/').pop()));
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
  $('archiveSearchBtn').addEventListener('click', () => loadRecordings().catch((error) => showToast(error.message, 'error')));
  $('archiveDate').addEventListener('change', () => loadRecordings().catch((error) => showToast(error.message, 'error')));
  $('archiveCamera').addEventListener('change', () => loadRecordings().catch((error) => showToast(error.message, 'error')));
  $('archiveSearch').addEventListener('keydown', (event) => {
    if (event.key === 'Enter') loadRecordings().catch((error) => showToast(error.message, 'error'));
  });
  $('recordingsList').addEventListener('click', (event) => {
    const button = event.target.closest('[data-play]');
    if (button) playRecording(button.dataset.play, button.dataset.name);
  });
  document.querySelectorAll('[data-seek]').forEach((button) => {
    button.addEventListener('click', () => seekPlayer(Number(button.dataset.seek)));
  });
  $('playbackSpeed').addEventListener('change', () => { $('player').playbackRate = Number($('playbackSpeed').value); });
  $('playerFullscreen').addEventListener('click', () => $('player').requestFullscreen?.());
  $('player').addEventListener('timeupdate', () => { $('playerCurrent').textContent = fmtTime($('player').currentTime); });
  $('player').addEventListener('loadedmetadata', () => { $('playerDuration').textContent = fmtTime($('player').duration); });
}

function playRecording(id, name) {
  const player = $('player');
  player.src = `/api/media?path=${encodeURIComponent(id)}`;
  $('playbackName').textContent = name;
  player.load();
  player.play().catch(() => {});
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

async function loadEvents() {
  const day = $('eventsDate').value || today();
  const camera = $('eventsCamera').value || '';
  const rows = await api(`/api/events?date=${encodeURIComponent(day)}&camera=${encodeURIComponent(camera)}`);
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
  const settings = await api('/api/settings');
  state.settings = settings;
  $('sRecordRoot').value = settings.record_root ?? '';
  $('sSnapshotRoot').value = settings.snapshot_root ?? '';
  $('sRecordMode').value = settings.record_mode ?? 'continuous';
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
  $('mThreshold').value = settings.motion?.threshold ?? 8;
  $('mFraction').value = settings.motion?.min_changed_fraction ?? 0.012;
  $('mCooldown').value = settings.motion?.cooldown_seconds ?? 15;
  $('mSnapshots').checked = !!settings.motion?.save_event_snapshots;
  $('mNotifications').checked = !!settings.notifications_enabled;
  renderCameraEditor(settings.cameras || []);
}

function renderCameraEditor(cameras) {
  $('cameraEditor').innerHTML = cameras.length ? cameras.map((camera, index) => `
    <div class="camera-block" data-index="${index}">
      <div class="camera-row">
        <input data-k="id" value="${esc(camera.id)}" placeholder="ID">
        <input data-k="name" value="${esc(camera.name)}" placeholder="Name">
        <input data-k="url" value="${esc(camera.url)}" placeholder="rtsp://IP:554/live/ch00_0">
        <input data-k="username" value="${esc(camera.username || 'admin')}" placeholder="Username">
        <input data-k="password" type="password" placeholder="Keep existing password">
        <button class="icon-btn" data-test-camera="${esc(camera.id)}">Test RTSP</button>
        <button class="icon-btn danger-text" data-remove-camera="${index}">Remove</button>
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
  return [...document.querySelectorAll('.camera-block')].map((block, index) => {
    const get = (key) => block.querySelector(`[data-k="${key}"]`);
    const old = state.settings.cameras[Number(block.dataset.index)] || {};
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

function setupCameraSettings() {
  $('addCamera').addEventListener('click', () => {
    state.settings.cameras.push({ id: `camera-${state.settings.cameras.length + 1}`, name: `Camera ${state.settings.cameras.length + 1}`, url: '', username: 'admin', password: '', ptz: { enabled: false, host: '', port: 80, username: '', password: '' } });
    renderCameraEditor(state.settings.cameras);
  });

  $('cameraEditor').addEventListener('click', async (event) => {
    const remove = event.target.closest('[data-remove-camera]');
    const test = event.target.closest('[data-test-camera]');
    const ptzTest = event.target.closest('[data-test-ptz]');
    try {
      if (remove) {
        state.settings.cameras.splice(Number(remove.dataset.removeCamera), 1);
        renderCameraEditor(state.settings.cameras);
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
    cameras: collectCameras()
  };
  const result = await api('/api/settings', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
  state.settings = result;
  $('settingsStatus').textContent = 'Settings saved. Some server changes apply after restart.';
  renderCameraEditor(state.settings.cameras || []);
  await Promise.all([loadInfo(), loadStreams()]);
  showToast('Settings saved.', 'success');
}

async function loadUsers() {
  if (state.auth?.user?.role !== 'admin') return;
  const users = await api('/api/users');
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
      await loadUsers();
      showToast('User created.', 'success');
    } catch (error) { showToast(error.message, 'error'); }
  });

  $('userEditor').addEventListener('change', async (event) => {
    const select = event.target.closest('[data-user-role]');
    if (!select) return;
    try {
      await api(`/api/users/${select.dataset.userRole}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ role: select.value }) });
      showToast('User role updated.', 'success');
      await loadUsers();
    } catch (error) { showToast(error.message, 'error'); }
  });

  $('userEditor').addEventListener('click', async (event) => {
    const toggle = event.target.closest('[data-toggle-user]');
    const remove = event.target.closest('[data-delete-user]');
    try {
      if (toggle) {
        await api(`/api/users/${toggle.dataset.toggleUser}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ enabled: toggle.dataset.enabled !== '1' }) });
        await loadUsers();
      }
      if (remove) {
        if (!confirm('Delete this user account?')) return;
        await api(`/api/users/${remove.dataset.deleteUser}`, { method: 'DELETE' });
        await loadUsers();
      }
    } catch (error) { showToast(error.message, 'error'); }
  });
}

function setupSettingsActions() {
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
  setupDashboardActions();
  setupArchiveActions();
  setupEventActions();
  setupCameraSettings();
  setupSecurityActions();
  setupSettingsActions();
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
