// LAN camera discovery helper. The server checks a bounded set of common RTSP ports.
(() => {
  const camerasTab = document.getElementById('tab-cameras');
  const cameraEditor = document.getElementById('cameraEditor');
  if (!camerasTab || !cameraEditor || typeof api !== 'function') return;

  if (document.getElementById('cameraDiscovery')) return;

  const panel = document.createElement('div');
  panel.id = 'cameraDiscovery';
  panel.className = 'panel';
  panel.innerHTML = `
    <div class="panel-head">
      <div>
        <h3>Find cameras on your network</h3>
        <p class="hint">Scan a private IPv4 subnet for common RTSP ports. Open ports are candidates only; the exact RTSP path still depends on the camera.</p>
      </div>
      <span class="pill">LAN</span>
    </div>
    <div class="create-user">
      <label>Local subnet<input id="discoverySubnet" placeholder="192.168.1.0/24" autocomplete="off"></label>
      <button class="button secondary" id="discoverCameras" type="button">Scan network</button>
    </div>
    <p id="discoveryStatus" class="muted" aria-live="polite"></p>
    <div id="discoveryResults"></div>
  `;
  camerasTab.querySelector('.panel')?.insertBefore(panel, cameraEditor);

  const subnetInput = document.getElementById('discoverySubnet');
  const scanButton = document.getElementById('discoverCameras');
  const status = document.getElementById('discoveryStatus');
  const results = document.getElementById('discoveryResults');

  const host = location.hostname;
  if (/^\d{1,3}(?:\.\d{1,3}){3}$/.test(host)) {
    const parts = host.split('.');
    subnetInput.value = `${parts.slice(0, 3).join('.')}.0/24`;
  }

  const addResult = (item) => {
    const row = document.createElement('div');
    row.style.display = 'flex';
    row.style.alignItems = 'center';
    row.style.justifyContent = 'space-between';
    row.style.gap = '12px';
    row.style.padding = '10px 0';
    row.style.borderTop = '1px solid rgba(255,255,255,.08)';

    const label = document.createElement('span');
    label.textContent = `${item.host}:${item.port}`;

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
        status.textContent = 'Camera editor is not ready. Open Settings → Cameras and try again.';
        return;
      }
      url.value = `rtsp://${item.host}:${item.port}/`;
      url.focus();
      status.textContent = `Added ${item.host}:${item.port}. Enter the correct RTSP path and credentials, then use Test RTSP.`;
    });

    row.append(label, use);
    results.appendChild(row);
  };

  scanButton.addEventListener('click', async () => {
    const subnet = subnetInput.value.trim();
    status.textContent = 'Scanning…';
    results.replaceChildren();
    scanButton.disabled = true;
    try {
      const data = await api('/api/camera-discovery', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({subnet})
      });
      status.textContent = `Checked ${data.scanned_addresses} addresses on ports ${data.ports.join(', ')}. Open ports are candidates only.`;
      if (!data.results.length) {
        results.textContent = 'No open RTSP ports found. Check the subnet, camera power, and network/VLAN settings.';
        return;
      }
      data.results.forEach(addResult);
    } catch (error) {
      status.textContent = error?.message || 'Scan failed.';
    } finally {
      scanButton.disabled = false;
    }
  });
})();
