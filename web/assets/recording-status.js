// Keep recording state visible in the dashboard and explain why a camera is recording.
(() => {
  const grid = document.getElementById('cameraGrid');
  if (!grid) return;

  const previous = new Map();
  let initialized = false;
  let timer = null;

  function reasonFor(mode) {
    if (mode === 'motion') return 'Motion detected';
    if (mode === 'manual') return 'Manual recording';
    return '24/7 continuous';
  }

  function update(streams, mode) {
    const map = new Map(streams.map((stream) => [String(stream.id), stream]));
    grid.querySelectorAll('.cam').forEach((card) => {
      const img = card.querySelector('.cam-body img');
      const id = img?.src ? decodeURIComponent(new URL(img.src, location.href).pathname.split('/').pop()?.replace(/\.mjpg$/, '') || '') : '';
      const stream = map.get(id);
      if (!stream) return;

      const foot = card.querySelector('.cam-foot > span');
      const badge = card.querySelector('.cam-head .pill');
      const recordButton = card.querySelector('[data-action="record"]');
      if (stream.recording) {
        const reason = reasonFor(mode);
        if (foot) foot.textContent = `Recording · ${reason}`;
        if (badge) { badge.textContent = 'REC'; badge.className = 'pill good'; }
        if (recordButton) { recordButton.textContent = 'Stop'; recordButton.dataset.recording = '1'; }
      } else {
        if (foot) foot.textContent = stream.online ? 'Connected' : 'Waiting for stream';
        if (badge) {
          const text = stream.motion ? 'MOTION' : stream.online ? 'LIVE' : 'OFFLINE';
          badge.textContent = text;
          badge.className = `pill ${stream.motion || stream.online ? 'good' : ''}`;
        }
        if (recordButton) { recordButton.textContent = 'Record'; recordButton.dataset.recording = '0'; }
      }

      const old = previous.get(id);
      if (initialized && old !== undefined && old !== !!stream.recording) {
        showToast(stream.recording ? `Recording started · ${reasonFor(mode)}.` : 'Recording stopped.', stream.recording ? 'success' : 'info');
      }
      previous.set(id, !!stream.recording);
    });
    initialized = true;
  }

  async function poll() {
    try {
      const [streams, info] = await Promise.all([
        fetch('/api/streams', { credentials: 'same-origin', cache: 'no-store' }).then((r) => r.ok ? r.json() : Promise.reject(new Error('streams'))),
        fetch('/api/info', { credentials: 'same-origin', cache: 'no-store' }).then((r) => r.ok ? r.json() : Promise.reject(new Error('info'))),
      ]);
      update(streams, info.record_mode || 'continuous');
    } catch {}
  }

  new MutationObserver(() => { poll(); }).observe(grid, { childList: true, subtree: true });
  poll();
  timer = setInterval(poll, 2000);
  window.addEventListener('beforeunload', () => clearInterval(timer));
})();
