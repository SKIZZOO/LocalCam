// LAN camera discovery helper. The server checks a bounded set of common RTSP ports.
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
        <p class="hint">Scan a private IPv4 subnet for common RTSP ports. An open port only identifies a possible RTSP service; you still need the correct RTSP path and credentials.</p>
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

  function addResult(item) {
    const row = document.createElement('div');
    row.className = 'discovery-result';

    const info = document.createElement('div');
    info.innerHTML = `<strong>${item.host}:${item.port}</strong><small>RTSP service candidate</small>`;

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
      status.textContent = `Added ${item.host}:${item.port}. Enter the camera's RTSP path and credentials, then test the stream.`;
    });

    row.append(info, use);
    results.appendChild(row);
  }

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
    } catch (error) {
      status.textContent = error?.message || 'Scan failed.';
    } finally {
      scanButton.disabled = false;
    }
  });
})();
