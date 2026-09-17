# Local Web Quick Start

1. Run `install.bat`.
2. Run `run.bat`.
3. Open `http://127.0.0.1:8765/` on the PC.
4. Create the first LocalCam password if prompted.
5. Open **Settings** and configure RTSP camera URLs and credentials.
6. From a phone or tablet on the same Wi-Fi, open `http://<PC-LAN-IP>:8765/`.

If the phone cannot connect, run `allow_web_firewall.bat` once as Administrator and make sure the Windows network profile is Private.

For remote access outside the home network, use a VPN. Do not expose port 8765 directly to the public internet.
