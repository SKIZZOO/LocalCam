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
    status.textContent = 'Testing RTSP, then checking ONVIF and common stream paths…';
    try {
      const data = await api('/api/camera-assist', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ camera_id: id, url, username, password })
      });
      if (!data.ok) {
        throw new Error(data.error || 'No usable RTSP stream was found.');
      }
      urlField.value = data.suggested_url || data.url || url;
      urlField.focus();
      status.textContent = `RTSP stream found via ${data.method || 'camera probing'} using ${String(data.transport || '').toUpperCase()}. Click Save settings to keep it.`;
      showToast(`RTSP stream found via ${data.method || 'camera probing'}.`, 'success');
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
