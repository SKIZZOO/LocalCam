// Interactive live-view controls: low-latency viewer helpers, digital zoom/pan,
// fullscreen, wheel/drag navigation, and optional ONVIF PTZ controls.
(() => {
  const grid = document.getElementById('cameraGrid');
  if (!grid || typeof api !== 'function') return;

  const viewers = new Map();
  let ptzCapabilities = new Map();

  const style = document.createElement('style');
  style.textContent = `
    .cam-body.viewer-ready { position: relative; overflow: hidden; background: #03070c; touch-action: none; }
    .cam-body.viewer-ready img { transform-origin: center center; will-change: transform; user-select: none; -webkit-user-drag: none; cursor: grab; }
    .cam-body.viewer-ready img.dragging { cursor: grabbing; }
    .viewer-toolbar { display:flex; align-items:center; gap:6px; flex-wrap:wrap; padding:8px 10px; border-top:1px solid rgba(255,255,255,.08); background:rgba(7,12,20,.92); }
    .viewer-toolbar .viewer-label { margin-right:auto; font-size:11px; color:rgba(232,241,250,.64); letter-spacing:.02em; }
    .viewer-btn { min-width:34px; height:30px; padding:0 9px; border:1px solid rgba(255,255,255,.11); border-radius:8px; background:rgba(20,31,47,.9); color:#eef5ff; cursor:pointer; font-size:12px; }
    .viewer-btn:hover { border-color:rgba(119,167,255,.5); background:rgba(28,43,63,.96); }
    .viewer-btn.primary { background:rgba(66,113,190,.26); border-color:rgba(119,167,255,.4); }
    .viewer-ptz { display:grid; grid-template-columns:repeat(3,30px); gap:4px; margin-left:6px; }
    .viewer-ptz .viewer-btn { min-width:30px; width:30px; padding:0; }
    .viewer-zoom { display:flex; gap:4px; margin-left:4px; }
    .viewer-status { font-size:10px; color:rgba(232,241,250,.56); margin-left:4px; min-width:42px; text-align:center; }
    .viewer-help { width:100%; font-size:10px; color:rgba(232,241,250,.48); margin-top:1px; }
  `;
  document.head.appendChild(style);

  function clamp(value, min, max) { return Math.max(min, Math.min(max, value)); }

  function makeButton(label, action, title) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'viewer-btn';
    button.textContent = label;
    button.dataset.viewerAction = action;
    if (title) button.title = title;
    return button;
  }

  function stateFor(id) {
    if (!viewers.has(id)) viewers.set(id, { zoom: 1, x: 0, y: 0, body: null, img: null });
    return viewers.get(id);
  }

  function limits(viewer) {
    const rect = viewer.body.getBoundingClientRect();
    return {
      x: Math.max(0, rect.width * (viewer.zoom - 1) / 2),
      y: Math.max(0, rect.height * (viewer.zoom - 1) / 2)
    };
  }

  function apply(viewer) {
    const max = limits(viewer);
    viewer.x = clamp(viewer.x, -max.x, max.x);
    viewer.y = clamp(viewer.y, -max.y, max.y);
    viewer.img.style.transform = `translate3d(${viewer.x}px, ${viewer.y}px, 0) scale(${viewer.zoom})`;
    const status = viewer.body.parentElement.querySelector('[data-viewer-status]');
    if (status) status.textContent = `${Math.round(viewer.zoom * 100)}%`;
  }

  function reset(viewer) {
    viewer.zoom = 1;
    viewer.x = 0;
    viewer.y = 0;
    apply(viewer);
  }

  function setZoom(viewer, next, anchorX = null, anchorY = null) {
    const oldZoom = viewer.zoom;
    viewer.zoom = clamp(Number(next), 1, 4);
    if (anchorX != null && anchorY != null && oldZoom !== viewer.zoom) {
      const factor = (viewer.zoom / oldZoom) - 1;
      viewer.x += anchorX * factor;
      viewer.y += anchorY * factor;
    }
    if (viewer.zoom === 1) { viewer.x = 0; viewer.y = 0; }
    apply(viewer);
  }

  async function physicalPtz(id, pan, tilt, zoom) {
    if (!ptzCapabilities.get(id)) {
      showToast('Camera PTZ is disabled. Enable ONVIF PTZ in Settings → Cameras.', 'error');
      return;
    }
    await api(`/api/ptz/${encodeURIComponent(id)}/move`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pan, tilt, zoom, seconds: 0.3 })
    });
  }

  async function ptzHome(id) {
    if (!ptzCapabilities.get(id)) {
      showToast('Camera PTZ is disabled. Enable ONVIF PTZ in Settings → Cameras.', 'error');
      return;
    }
    await api(`/api/ptz/${encodeURIComponent(id)}/home`, { method: 'POST' });
  }

  async function loadPtzCapabilities() {
    try {
      const streams = await api('/api/streams');
      ptzCapabilities = new Map(streams.map((stream) => [stream.id, !!stream.ptz_enabled]));
    } catch {
      ptzCapabilities = new Map();
    }
  }

  function enhanceCard(card) {
    const img = card.querySelector('.cam-body img');
    const body = card.querySelector('.cam-body');
    if (!img || !body || body.dataset.viewerEnhanced) return;
    const idMatch = img.getAttribute('src')?.match(/\/live\/([^/.]+)\.mjpg/);
    const id = idMatch ? decodeURIComponent(idMatch[1]) : '';
    if (!id) return;

    body.dataset.viewerEnhanced = '1';
    body.classList.add('viewer-ready');
    const viewer = stateFor(id);
    viewer.body = body;
    viewer.img = img;

    img.draggable = false;
    let dragging = false;
    let lastX = 0;
    let lastY = 0;

    body.addEventListener('wheel', (event) => {
      event.preventDefault();
      const rect = body.getBoundingClientRect();
      const anchorX = event.clientX - rect.left - rect.width / 2;
      const anchorY = event.clientY - rect.top - rect.height / 2;
      setZoom(viewer, viewer.zoom + (event.deltaY < 0 ? 0.25 : -0.25), anchorX, anchorY);
    }, { passive: false });

    body.addEventListener('pointerdown', (event) => {
      if (viewer.zoom <= 1 || event.target.closest('button')) return;
      dragging = true;
      lastX = event.clientX;
      lastY = event.clientY;
      img.classList.add('dragging');
      body.setPointerCapture?.(event.pointerId);
    });
    body.addEventListener('pointermove', (event) => {
      if (!dragging) return;
      viewer.x += event.clientX - lastX;
      viewer.y += event.clientY - lastY;
      lastX = event.clientX;
      lastY = event.clientY;
      apply(viewer);
    });
    const endDrag = (event) => {
      dragging = false;
      img.classList.remove('dragging');
      try { body.releasePointerCapture?.(event.pointerId); } catch {}
    };
    body.addEventListener('pointerup', endDrag);
    body.addEventListener('pointercancel', endDrag);

    img.addEventListener('error', () => {
      if (viewer.retryTimer) return;
      viewer.retryTimer = setTimeout(() => {
        viewer.retryTimer = null;
        const src = img.src.split('#')[0];
        img.src = `${src}#${Date.now()}`;
      }, 1500);
    });

    const toolbar = document.createElement('div');
    toolbar.className = 'viewer-toolbar';
    toolbar.innerHTML = '<span class="viewer-label">Live controls</span>';

    const zoomOut = makeButton('−', 'zoom-out', 'Zoom out');
    const resetBtn = makeButton('100%', 'reset', 'Reset digital zoom and pan');
    resetBtn.classList.add('primary');
    const zoomIn = makeButton('+', 'zoom-in', 'Zoom in');
    const full = makeButton('Fullscreen', 'fullscreen', 'Open this camera full screen');
    const status = document.createElement('span');
    status.className = 'viewer-status';
    status.dataset.viewerStatus = '1';
    toolbar.append(zoomOut, resetBtn, zoomIn, full, status);

    if (ptzCapabilities.get(id)) {
      const pad = document.createElement('div');
      pad.className = 'viewer-ptz';
      const moves = [
        ['↖', -1, 1], ['↑', 0, 1], ['↗', 1, 1],
        ['←', -1, 0], ['⌂', 0, 0], ['→', 1, 0],
        ['↙', -1, -1], ['↓', 0, -1], ['↘', 1, -1],
      ];
      moves.forEach(([label, pan, tilt]) => {
        const b = makeButton(label, 'ptz-move');
        b.dataset.pan = String(pan);
        b.dataset.tilt = String(tilt);
        b.title = label === '⌂' ? 'Go to home position' : 'Move camera';
        pad.appendChild(b);
      });
      toolbar.appendChild(pad);

      const zoom = document.createElement('div');
      zoom.className = 'viewer-zoom';
      zoom.append(makeButton('Cam −', 'ptz-zoom-out', 'Optical zoom out'), makeButton('Stop', 'ptz-stop', 'Stop camera movement'), makeButton('Cam +', 'ptz-zoom-in', 'Optical zoom in'));
      toolbar.appendChild(zoom);
    }

    const help = document.createElement('div');
    help.className = 'viewer-help';
    help.textContent = ptzCapabilities.get(id)
      ? 'Mouse wheel or +/− = digital zoom · drag the image = digital pan · arrows = camera PTZ.'
      : 'Mouse wheel or +/− = digital zoom · drag the image = digital pan. Enable ONVIF PTZ for physical camera movement.';
    toolbar.appendChild(help);

    card.appendChild(toolbar);

    toolbar.addEventListener('click', async (event) => {
      const button = event.target.closest('[data-viewer-action]');
      if (!button) return;
      const action = button.dataset.viewerAction;
      try {
        if (action === 'zoom-in') setZoom(viewer, viewer.zoom + 0.25);
        else if (action === 'zoom-out') setZoom(viewer, viewer.zoom - 0.25);
        else if (action === 'reset') reset(viewer);
        else if (action === 'fullscreen') await body.requestFullscreen?.();
        else if (action === 'ptz-move') {
          if (button.textContent === '⌂') await ptzHome(id);
          else await physicalPtz(id, Number(button.dataset.pan), Number(button.dataset.tilt), 0);
        } else if (action === 'ptz-zoom-in') await physicalPtz(id, 0, 0, 1);
        else if (action === 'ptz-zoom-out') await physicalPtz(id, 0, 0, -1);
        else if (action === 'ptz-stop') {
          if (ptzCapabilities.get(id)) await api(`/api/ptz/${encodeURIComponent(id)}/stop`, { method: 'POST' });
          else showToast('Camera PTZ is disabled. Enable ONVIF PTZ in Settings → Cameras.', 'error');
        }
      } catch (error) {
        showToast(error.message || 'Camera control failed.', 'error');
      }
    });

    apply(viewer);
  }

  async function refresh() {
    await loadPtzCapabilities();
    grid.querySelectorAll('.cam').forEach(enhanceCard);
  }

  new MutationObserver(() => {
    grid.querySelectorAll('.cam').forEach(enhanceCard);
  }).observe(grid, { childList: true, subtree: true });

  refresh();
})();
