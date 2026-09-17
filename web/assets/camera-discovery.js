// LAN camera discovery and RTSP path assistant.
(() => {
  const camerasTab = document.getElementById('tab-cameras');
  const cameraEditor = document.getElementById('cameraEditor');
  if (!camerasTab || !cameraEditor || typeof api !== 'function') return;

  let panel = document.getElementById('cameraDiscovery');
  if (!panel) {
    panel = document.createElement('div');
    panel.id = 'cameraDiscovery';
    panel.className = 'discovery-card';
    camerasTab.querySelector('.panel')?.insertBefore(panel, cameraEditor);
  }

  panel.innerHTML = `
    <div class="discovery-head">
      <div>
        <h3>Find cameras on your network</h3>
        <p class="hint">Scan a private IPv4 subnet for RTSP ports, then use ONVIF or common stream paths to find the actual video URL.</p>
      </div>
      <span class="pill">LAN</span>
    </div>
    <div class="discovery-actions">
      <label class="grow">Local subnet<input id="discoverySubnet" placeholder="192.168.1.0/24" autocomplete="off"></label>
      <button class="button primary" id="discoverCameras" type="button">Scan network</button>
    </div>
    <p id="discoveryStatus" class="muted" aria-live="polite">Ready to scan.</p>
    <div id="discoveryResults" class="discovery-results"></div>
  `;

  const subnetInput = document.getElementById('discoverySubnet');
  const scanButton = document.getElementById('discoverCameras');
  const status = document.getElementById('discoveryStatus');
  const results = document.getElementById('discoveryResults');

  const host = location.hostname;
  if (/^\d{1,3}(?:\.\d{1,3}){3}$/.test(host)) {
    const parts = host.split('.');
    subnetInput.value = `${parts.slice(0, 3).join('.')}.0/24`;
  }

  function cameraBlock(button) {
    return button.closest('.camera-block');
  }

  function ensureAutoButtons() {
    document.querySelectorAll('#cameraEditor .camera-block').forEach((block) => {
      const row = block.querySelector('.camera-row');
      if (!row || row.querySelector('[data-auto-rtsp]')) return;
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'icon-btn';
      button.dataset.autoRtsp = '1';
      button.textContent = 'Auto-detect RTSP';
      row.appendChild(button);
    });
  }

  function feedLabel(url) {
    const match = String(url || '').match(/ch(\\d+)_(\\d+)/i);
    if (!match) return 'Discovered feed';
    const channel = Number(match[1]) + 1;
    const stream = match[2] === '0' ? 'Main' : 'Sub';
    return `Channel ${channel} · ${stream}`;
  }

  function renderDetectedFeeds(block, streams, primary) {
    const existing = results.querySelector('.detected-feeds');
    existing?.remove();
    if (!streams?.length) return;

    const box = document.createElement('div');
    box.className = 'detected-feeds';
    box.innerHTML = `
      <div class="detected-feeds-head">
        <div><strong>Feed preview</strong><span>${streams.length} working feed${streams.length === 1 ? '' : 's'} found</span></div>
      </div>
    `;

    streams.forEach((feed, index) => {
      const card = document.createElement('div');
      card.className = 'detected-feed';
      const preview = feed.preview
        ? `<img src="data:${feed.preview_mime || 'image/jpeg'};base64,${feed.preview}" alt="${feedLabel(feed.suggested_url)} preview">`
        : '<div class="discovery-preview-empty">Preview unavailable</div>';
      card.innerHTML = `
        <div class="discovery-preview">${preview}</div>
        <div class="discovery-feed-info">
          <strong>${index === 0 ? 'Selected feed' : feedLabel(feed.suggested_url)}</strong>
          <small>${esc(feed.suggested_url || feed.url || '')}</small>
          <small>${esc(String(feed.method || 'RTSP'))} · ${esc(String(feed.transport || '').toUpperCase())}</small>
        </div>
      `;
      box.appendChild(card);
    });

    const primaryChannel = channelKey(primary.suggested_url);
    const extras = streams.slice(1).filter((feed) => {
      const url = feed.suggested_url;
      if (!url || url === primary.suggested_url) return false;
      // When the selected feed is ch00_0/ch00_1, don't ask the user to add
      // the other quality variant as a second camera; offer only other
      // channel numbers such as ch01_0/ch01_1.
      const feedChannel = channelKey(url);
      return primaryChannel == null || feedChannel == null || feedChannel !== primaryChannel;
    });
    if (extras.length) {
      const ask = document.createElement('div');
      ask.className = 'discovery-add-question';
      const checks = extras.map((feed, i) => `
        <label class="detected-choice">
          <input type="checkbox" data-add-feed="${i}" checked>
          <span><b>${esc(feedLabel(feed.suggested_url))}</b><small>${esc(feed.suggested_url)}</small></span>
        </label>`).join('');
      ask.innerHTML = `
        <div>
          <strong>This camera exposes additional feeds</strong>
          <p class="hint">Would you like to add the other working feeds as separate cameras too?</p>
        </div>
        <div class="detected-choices">${checks}</div>
        <div class="row-actions">
          <button type="button" class="button primary" data-add-detected>Yes, add selected</button>
          <button type="button" class="button ghost" data-dismiss-detected>Not now</button>
        </div>
      `;
      ask.querySelector('[data-add-detected]')?.addEventListener('click', () => {
        const picked = extras.filter((_, i) => ask.querySelector(`[data-add-feed="${i}"]`)?.checked);
        if (!picked.length) {
          status.textContent = 'No additional feeds were selected.';
          return;
        }
        addDetectedFeeds(block, picked);
        ask.remove();
      });
      ask.querySelector('[data-dismiss-detected]')?.addEventListener('click', () => {
        ask.remove();
        status.textContent = 'Additional feeds left untouched. You can re-run detection later.';
      });
      box.appendChild(ask);
    }

    results.prepend(box);
  }

  function addDetectedFeeds(block, feeds) {
    if (typeof state === 'undefined' || !state.settings) {
      showToast('Camera settings are still loading. Try again in a moment.', 'error');
      return;
    }
    const current = collectCameras();
    const sourceId = block.querySelector('[data-k="id"]')?.value.trim() || '';
    const source = current.find((camera) => camera.id === sourceId) || current[0];
    if (!source) return;

    const existingUrls = new Set(current.map((camera) => camera.url));
    const existingIds = new Set(current.map((camera) => camera.id));
    let added = 0;

    feeds.forEach((feed) => {
      const url = String(feed.suggested_url || '').trim();
      if (!url || existingUrls.has(url)) return;
      const label = feedLabel(url).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || `feed-${added + 1}`;
      const baseId = `${source.id}-${label}`;
      let id = baseId;
      let n = 2;
      while (existingIds.has(id)) id = `${baseId}-${n++}`;
      const copy = {
        ...source,
        id,
        name: `${source.name} · ${feedLabel(url)}`,
        url,
        ptz: { ...(source.ptz || {}) }
      };
      current.push(copy);
      existingUrls.add(url);
      existingIds.add(id);
      added += 1;
    });

    state.settings.cameras = current;
    renderCameraEditor(state.settings.cameras);
    ensureAutoButtons();
    status.textContent = added
      ? `Added ${added} additional feed${added === 1 ? '' : 's'}. Click Save settings to activate them.`
      : 'Those feeds are already in the camera list.';
    showToast(added ? `Added ${added} additional camera feed${added === 1 ? '' : 's'}.` : 'Feeds are already configured.', added ? 'success' : 'info');
  }

  async function autoDetect(button) {
    const block = cameraBlock(button);
    if (!block) return;
    const id = block.querySelector('[data-k="id"]')?.value.trim() || '';
    const url = block.querySelector('[data-k="url"]')?.value.trim() || '';
    const username = block.querySelector('[data-k="username"]')?.value.trim() || '';
    const password = block.querySelector('[data-k="password"]')?.value || '';
    const urlField = block.querySelector('[data-k="url"]');

    if (!urlField) return;
    if (!url) {
      status.textContent = 'Enter the camera IP/RTSP address first, for example rtsp://192.168.1.50:554/.';
      return;
    }

    button.disabled = true;
    const oldText = button.textContent;
    button.textContent = 'Detecting…';
    status.textContent = 'Testing RTSP, checking ch00/ch01 feeds, and capturing previews…';
    try {
      const data = await api('/api/camera-assist', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ camera_id: id, url, username, password })
      });
      if (!data.ok) {
        throw new Error(data.error || 'No usable RTSP stream was found.');
      }
      const streams = data.streams?.length ? data.streams : [data];
      const primary = streams[0];
      urlField.value = primary.suggested_url || primary.url || url;
      urlField.focus();
      renderDetectedFeeds(block, streams, primary);
      status.textContent = streams.length > 1
        ? `Found ${streams.length} working feeds. The first one is selected above.`
        : `RTSP stream found via ${data.method || 'camera probing'} using ${String(data.transport || '').toUpperCase()}.`;
      showToast(`Found ${streams.length} working camera feed${streams.length === 1 ? '' : 's'}.`, 'success');
    } catch (error) {
      status.textContent = error?.message || 'RTSP auto-detection failed.';
      showToast(error?.message || 'RTSP auto-detection failed.', 'error');
    } finally {
      button.disabled = false;
      button.textContent = oldText;
    }
  }

  function addResult(item) {
    const row = document.createElement('div');
    row.className = 'discovery-result';

    const info = document.createElement('div');
    info.innerHTML = `<strong>${item.host}:${item.port}</strong><small>RTSP service candidate</small>`;

    const actions = document.createElement('div');
    actions.className = 'row-actions';

    const use = document.createElement('button');
    use.type = 'button';
    use.className = 'button ghost';
    use.textContent = 'Use address';
    use.addEventListener('click', () => {
      const addButton = document.getElementById('addCamera');
      if (addButton) addButton.click();
      const blocks = [...document.querySelectorAll('#cameraEditor .camera-block')];
      const last = blocks[blocks.length - 1];
      const url = last?.querySelector('[data-k="url"]');
      if (!url) {
        status.textContent = 'Camera editor is not ready. Click Add camera first.';
        return;
      }
      url.value = `rtsp://${item.host}:${item.port}/`;
      url.focus();
      status.textContent = `Added ${item.host}:${item.port}. Use Auto-detect RTSP on that camera to find the stream path.`;
      ensureAutoButtons();
    });

    const detect = document.createElement('button');
    detect.type = 'button';
    detect.className = 'button ghost';
    detect.textContent = 'Find stream';
    detect.addEventListener('click', async () => {
      let target = [...document.querySelectorAll('#cameraEditor .camera-block')].at(-1);
      if (!target) {
        document.getElementById('addCamera')?.click();
        target = [...document.querySelectorAll('#cameraEditor .camera-block')].at(-1);
      }
      const url = target?.querySelector('[data-k="url"]');
      if (!target || !url) return;
      url.value = `rtsp://${item.host}:${item.port}/`;
      ensureAutoButtons();
      const auto = target.querySelector('[data-auto-rtsp]');
      if (auto) await autoDetect(auto);
    });

    actions.append(use, detect);
    row.append(info, actions);
    results.appendChild(row);
  }

  // Handle both the explicit Auto-detect button and the existing Test RTSP
  // button. Capture phase runs before app.js's normal Test RTSP handler, so
  // testing a root URL automatically invokes the path assistant instead of
  // returning the known-invalid result for rtsp://HOST:554/.
  cameraEditor.addEventListener('click', async (event) => {
    const autoButton = event.target.closest('[data-auto-rtsp]');
    const testButton = event.target.closest('[data-test-camera]');
    const button = autoButton || testButton;
    if (!button) return;
    if (testButton) {
      event.preventDefault();
      event.stopImmediatePropagation();
    }
    await autoDetect(button);
  }, true);

  new MutationObserver(ensureAutoButtons).observe(cameraEditor, { childList: true, subtree: true });
  ensureAutoButtons();

  scanButton.addEventListener('click', async () => {
    const subnet = subnetInput.value.trim();
    if (!subnet) {
      status.textContent = 'Enter a private IPv4 subnet, for example 192.168.1.0/24.';
      return;
    }
    status.textContent = 'Scanning the LAN…';
    results.replaceChildren();
    scanButton.disabled = true;
    try {
      const data = await api('/api/camera-discovery', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({subnet})
      });
      status.textContent = `Checked ${data.scanned_addresses} addresses on ports ${data.ports.join(', ')}.`;
      if (!data.results.length) {
        results.innerHTML = '<div class="muted">No open RTSP ports found. Check the subnet, camera power, firewall, or VLAN settings.</div>';
        return;
      }
      data.results.forEach(addResult);
      ensureAutoButtons();
    } catch (error) {
      status.textContent = error?.message || 'Scan failed.';
    } finally {
      scanButton.disabled = false;
    }
  });
})();
