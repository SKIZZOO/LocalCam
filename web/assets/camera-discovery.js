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

  function channelKey(url) {
    const match = String(url || '').match(/ch(\d+)_(\d+)/i);
    return match ? Number(match[1]) : null;
  }

  function isMainFeed(url) {
    const source = String(url || '');
    const channel = source.match(/ch\d+_(\d+)/i);
    if (channel) return channel[1] === '0';
    const stream = source.match(/[?&]stream=(\d+)/i);
    if (stream) return stream[1] === '0';
    const substream = source.match(/[?&]substream=(\d+)/i);
    if (substream) return substream[1] === '0';
    return true;
  }

  function feedLabel(url) {
    const match = String(url || '').match(/ch(\d+)_(\d+)/i);
    if (!match) return 'Discovered feed';
    const channel = Number(match[1]) + 1;
    const stream = match[2] === '0' ? 'Main' : 'Sub';
    return `Channel ${channel} · ${stream}`;
  }

  function feedSort(a, b) {
    const aKey = String(a?.suggested_url || a?.url || '');
    const bKey = String(b?.suggested_url || b?.url || '');
    const aChannel = channelKey(aKey);
    const bChannel = channelKey(bKey);
    if (aChannel != null && bChannel != null && aChannel !== bChannel) return aChannel - bChannel;
    if (aChannel != null && bChannel == null) return -1;
    if (aChannel == null && bChannel != null) return 1;
    const aMain = isMainFeed(aKey) ? 0 : 1;
    const bMain = isMainFeed(bKey) ? 0 : 1;
    if (aMain !== bMain) return aMain - bMain;
    return aKey.localeCompare(bKey);
  }

  function renderDetectedFeeds(block, streams) {
    const existing = results.querySelector('.detected-feeds');
    existing?.remove();
    if (!streams?.length) return null;
    const ordered = [...streams].sort(feedSort);
    const mainCount = ordered.filter((feed) => isMainFeed(feed.suggested_url || feed.url)).length;
    const box = document.createElement('div');
    box.className = 'detected-feeds';
    box.innerHTML = `
      <div class="detected-feeds-head">
        <div><strong>Working camera feeds</strong><span>Multiple feeds can be selected. Main feeds are selected by default because they normally provide the best picture quality.</span></div>
        <span class="pill" data-preview-progress>PREVIEWS PENDING</span>
      </div>
      <div class="detected-feed-picker"></div>
      <div class="discovery-selection-panel">
        <strong>Add the feeds you want to use</strong>
        <p class="hint">LocalCam found ${ordered.length} working feed${ordered.length === 1 ? '' : 's'}. Main feeds are selected by default. Sub feeds are lower-quality alternatives and are left unchecked.</p>
        <div class="detected-selection-summary" aria-live="polite"></div>
        <div class="detected-selection-warning" aria-live="polite"></div>
        <div class="row-actions">
          <button type="button" class="button primary" data-use-detected>Use selected feeds</button>
          <button type="button" class="button ghost" data-dismiss-detected>Not now</button>
        </div>
      </div>`;
    const picker = box.querySelector('.detected-feed-picker');
    ordered.forEach((feed, index) => {
      const url = String(feed.suggested_url || feed.url || '');
      const main = isMainFeed(url);
      const selectedByDefault = mainCount ? main : index === 0;
      const card = document.createElement('div');
      card.className = 'detected-feed';
      card.dataset.feedUrl = url;
      card.innerHTML = `
        <div class="discovery-preview" data-preview-slot><div class="discovery-preview-empty">Capturing snapshot…</div></div>
        <div class="discovery-feed-info">
          <label class="detected-select">
            <input type="checkbox" data-detected-feed value="${esc(url)}" ${selectedByDefault ? 'checked' : ''}>
            <span><b>${main ? 'Main feed · recommended' : 'Sub feed · lower quality'}</b><strong>${esc(feedLabel(url))}</strong></span>
          </label>
          <small>${esc(url)}</small>
          <small>${esc(String(feed.method || 'RTSP'))} · ${esc(String(feed.transport || '').toUpperCase())}</small>
        </div>`;
      picker.appendChild(card);
    });
    const summary = box.querySelector('.detected-selection-summary');
    const warning = box.querySelector('.detected-selection-warning');
    const useButton = box.querySelector('[data-use-detected]');
    const updateSelectionState = () => {
      const selected = [...box.querySelectorAll('[data-detected-feed]:checked')];
      const selectedSub = selected.filter((input) => !isMainFeed(input.value));
      summary.textContent = `${selected.length} feed${selected.length === 1 ? '' : 's'} selected · ${mainCount ? 'main feeds are preselected' : 'no main feed was detected'}`;
      warning.textContent = selectedSub.length
        ? `You selected ${selectedSub.length} sub feed${selectedSub.length === 1 ? '' : 's'}. LocalCam recommends the main feeds for better image quality.`
        : 'Main feeds are the recommended choice for the best available quality.';
      useButton.disabled = selected.length === 0;
    };
    box.querySelectorAll('[data-detected-feed]').forEach((input) => input.addEventListener('change', updateSelectionState));
    box.querySelectorAll('.detected-feed').forEach((card) => card.addEventListener('click', (event) => {
      if (event.target.closest('input,button,a,label')) return;
      const input = card.querySelector('[data-detected-feed]');
      if (input) { input.checked = !input.checked; updateSelectionState(); }
    }));
    updateSelectionState();
    useButton?.addEventListener('click', () => {
      const selectedUrls = [...box.querySelectorAll('[data-detected-feed]:checked')].map((input) => input.value);
      const selected = ordered.filter((feed) => selectedUrls.includes(String(feed.suggested_url || feed.url || '')));
      if (!selected.length) { status.textContent = 'Select at least one feed.'; return; }
      applyDetectedSelection(block, selected);
      box.remove();
    });
    box.querySelector('[data-dismiss-detected]')?.addEventListener('click', () => {
      box.remove();
      status.textContent = 'Detection kept available in the results. No camera settings were changed.';
    });
    results.prepend(box);
    return box;
  }

  async function loadFeedPreviews(box, block, streams) {
    const total = streams.length;
    if (!total) return;
    const progress = box.querySelector('[data-preview-progress]');
    let completed = 0;
    status.textContent = `Found ${total} working feed${total === 1 ? '' : 's'}. Capturing snapshots (0/${total})…`;
    const credentials = {
      camera_id: block.querySelector('[data-k="id"]')?.value.trim() || '',
      username: block.querySelector('[data-k="username"]')?.value.trim() || '',
      password: block.querySelector('[data-k="password"]')?.value || ''
    };
    await Promise.all(streams.map(async (feed) => {
      const url = String(feed.suggested_url || feed.url || '');
      const card = [...box.querySelectorAll('.detected-feed')].find((node) => node.dataset.feedUrl === url);
      const slot = card?.querySelector('[data-preview-slot]');
      try {
        const data = await api('/api/camera-snapshot', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({...credentials, url})
        });
        if (!data.ok || !data.data) throw new Error(data.error || 'Preview unavailable.');
        if (slot) slot.innerHTML = `<img src="data:${data.mime || 'image/jpeg'};base64,${data.data}" alt="${esc(feedLabel(url))} snapshot">`;
      } catch (error) {
        if (slot) slot.innerHTML = `<div class="discovery-preview-empty">${esc(error.message || 'Preview unavailable')}</div>`;
      } finally {
        completed += 1;
        if (progress) { progress.textContent = `${completed}/${total} PREVIEWS`; progress.className = `pill ${completed === total ? 'good' : ''}`; }
        status.textContent = `Found ${total} working feed${total === 1 ? '' : 's'}. Capturing snapshots (${completed}/${total})…`;
      }
    }));
    status.textContent = `Found ${total} working feed${total === 1 ? '' : 's'}. Review the previews and select the feeds you want to use.`;
  }
  function applyDetectedSelection(block, feeds) {
    if (typeof state === 'undefined' || !state.settings) {
      showToast('Camera settings are still loading. Try again in a moment.', 'error');
      return;
    }
    const current = collectCameras();
    const sourceId = block.querySelector('[data-k="id"]')?.value.trim() || '';
    const source = current.find((camera) => camera.id === sourceId) || current[0];
    if (!source || !feeds?.length) return;

    const primary = feeds.find((feed) => isMainFeed(feed.suggested_url || feed.url)) || feeds[0];
    const primaryUrl = String(primary.suggested_url || primary.url || '').trim();
    if (!primaryUrl) return;

    // Remove additional cameras created by an earlier discovery selection.
    // This prevents stale ch01 sub/main feeds from remaining in the live view
    // after the user changes the selection.
    const generatedPrefix = `${source.id}-channel-`;
    const cleaned = current.filter((camera) => {
      if (camera === source) return true;
      if (String(camera.discovered_from || '') === source.id) return false;
      if (String(camera.id || '').startsWith(generatedPrefix)) return false;
      return !String(camera.name || '').startsWith(`${source.name} · Channel `);
    });
    source.url = primaryUrl;
    const existingUrls = new Set(cleaned.map((camera) => camera.url));
    const existingIds = new Set(cleaned.map((camera) => camera.id));
    let added = 0;

    const additions = feeds.filter((feed) => String(feed.suggested_url || feed.url || '').trim() !== primaryUrl);
    additions.forEach((feed) => {
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
        discovered_from: source.id,
        discovered_feed: true,
        ptz: { ...(source.ptz || {}) }
      };
      cleaned.push(copy);
      existingUrls.add(url);
      existingIds.add(id);
      added += 1;
    });

    state.settings.cameras = cleaned;
    renderCameraEditor(state.settings.cameras);
    ensureAutoButtons();
    const selectedCount = feeds.length;
    status.textContent = `Configured ${selectedCount} feed${selectedCount === 1 ? '' : 's'} for this camera${added ? ` and added ${added} separate camera feed${added === 1 ? '' : 's'}` : ''}. Main feeds are preferred. Click Save settings to activate them.`;
    showToast(`Selected ${selectedCount} feed${selectedCount === 1 ? '' : 's'}. Main feeds are preferred.`, 'success');
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
    button.textContent = 'Checking feeds…';
    status.textContent = 'Checking the configured URL and all ch00/ch01 main + sub feeds in parallel…';
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
      const box = renderDetectedFeeds(block, streams);
      status.textContent = `Found ${streams.length} working feed${streams.length === 1 ? '' : 's'} after checking ${data.candidates_checked || streams.length} RTSP paths. Loading snapshots…`;
      showToast(`Found ${streams.length} working camera feed${streams.length === 1 ? '' : 's'}.`, 'success');
      if (box) await loadFeedPreviews(box, block, streams);
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

  async function waitForDiscovery(jobId) {
    let lastScanned = 0;
    while (true) {
      const data = await api(`/api/camera-discovery/status?job=${encodeURIComponent(jobId)}`);
      const scanned = Number(data.scanned || 0);
      const total = Number(data.total || 0);
      const found = Number(data.found || 0);
      if (data.status === 'error') throw new Error(data.error || 'Network scan failed.');

      if (data.status === 'completed') {
        return data.result || {
          results: data.results || [],
          scanned_addresses: data.scanned_addresses,
          ports: data.ports
        };
      }

      // Update even when progress advances slowly so the UI always tells the
      // user that the scan is still alive.
      if (scanned !== lastScanned || data.phase) {
        status.textContent = data.phase || `Scanning… ${scanned}/${total} checks · ${found} candidate${found === 1 ? '' : 's'} found`;
        lastScanned = scanned;
      }
      await new Promise((resolve) => setTimeout(resolve, 350));
    }
  }

  scanButton.addEventListener('click', async () => {
    const subnet = subnetInput.value.trim();
    if (!subnet) {
      status.textContent = 'Enter a private IPv4 subnet, for example 192.168.1.0/24.';
      return;
    }
    status.textContent = 'Starting a gentle RTSP scan…';
    results.replaceChildren();
    scanButton.disabled = true;
    try {
      const data = await api('/api/camera-discovery', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({subnet})
      });

      if (data.status === 'completed') {
        status.textContent = data.phase || `Scan complete · ${data.results.length} RTSP candidate${data.results.length === 1 ? '' : 's'} found`;
        if (!data.results.length) {
          results.innerHTML = '<div class="muted">No open RTSP ports found. The scan completed without hammering the camera or router. Check the subnet, camera power, firewall, or VLAN settings.</div>';
          return;
        }
        data.results.forEach(addResult);
        ensureAutoButtons();
        return;
      }

      status.textContent = data.phase || 'Scanning RTSP ports…';
      const result = await waitForDiscovery(data.job_id);
      const rows = result.results || [];
      status.textContent = rows.length
        ? `Scan complete · checked ${result.scanned_addresses || 'the LAN'} addresses on ports ${(result.ports || [554, 8554, 10554]).join(', ')} · ${rows.length} RTSP candidate${rows.length === 1 ? '' : 's'} found`
        : `Scan complete · checked ${result.scanned_addresses || 'the LAN'} addresses · no RTSP ports found`;
      if (!rows.length) {
        results.innerHTML = '<div class="muted">No open RTSP ports found this time. Try again after a few seconds; LocalCam now throttles the scan to avoid triggering camera/network connection limits.</div>';
        return;
      }
      rows.forEach(addResult);
      ensureAutoButtons();
    } catch (error) {
      status.textContent = error?.message || 'Scan failed.';
      showToast(error?.message || 'Scan failed.', 'error');
    } finally {
      scanButton.disabled = false;
    }

})();
