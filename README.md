<div align="center">

# LocalCam

### Self-hosted LAN video recorder for JOOAN / CAM720 RTSP cameras

A local-first network video recorder with a modern browser dashboard, recording archive, motion events, and optional camera controls.

[Features](#features) · [Quick start](#quick-start) · [Windows service](#windows-service) · [Security](#security)

</div>

---

## At a glance

| | |
|---|---|
| **Platform** | Windows 10 / 11 |
| **Runtime** | Python 3.11+ |
| **Video engine** | FFmpeg |
| **Storage** | Local disk; MKV recordings and SQLite events |
| **Access** | Browser on your private LAN |
| **License** | MIT |

## Features

- Multi-camera live monitoring with shared FFmpeg preview workers
- Direct RTSP recording using stream copy (`-c copy`) into MKV segments
- Continuous, motion-only, and manual recording modes
- Automatic reconnect and recorder watchdogs
- Retention and minimum-free-space cleanup
- 24-hour archive timeline with click-to-play segments
- Browser playback, seeking, playback-speed controls, fullscreen, and downloads
- Motion events stored in SQLite, with optional event snapshots
- Browser notifications for new motion events
- RTSP health probes
- LAN camera discovery for common RTSP ports
- Optional ONVIF PTZ control, when supported by the camera
- Local multi-user authentication with Viewer, Operator, and Administrator roles
- PBKDF2-SHA256 password hashing, HttpOnly sessions, login rate limiting, and same-origin checks for mutating requests
- Backup and restore for local configuration and event database
- Responsive desktop and mobile interface
- Windows Service support for startup before user login, with automatic recovery configuration
- Standalone EXE build support through PyInstaller
- No cloud dependency for RTSP workflows; no camera passwords committed to Git

## Quick start

### 1. Install prerequisites

- Windows 10 or 11
- Python 3.11 or newer
- FFmpeg available on `PATH` or configured in LocalCam Settings
- A modern browser (Chromium, Firefox, or Safari)
- Sufficient local disk space for your recording-retention needs

Python: <https://www.python.org/downloads/windows/>  
FFmpeg: <https://ffmpeg.org/download.html>

### 2. Set up and launch

1. Download or clone this repository and open the `LocalCam` folder.
2. Double-click **`run.bat`**.
3. If the LocalCam Python environment is missing, `run.bat` asks whether you want to install it.
4. If the environment exists but runtime Python packages are missing, `run.bat` asks whether you want to repair them.
5. LocalCam creates `config.json` automatically when needed.
6. If FFmpeg is missing, install it and add it to `PATH`, or configure its executable path in Settings.
7. LocalCam opens the browser automatically when startup completes.
8. Create the administrator account on first launch.
9. Go to **Settings → Cameras**. You can scan the LAN for common RTSP ports and then complete the camera's RTSP path and credentials.
10. Go to **Settings → Storage** to choose recording and snapshot folders.

The normal user workflow is now a single launcher: **`run.bat`**.

To update to the latest GitHub `main` version, stop LocalCam and double-click **`update.bat`**. It uses Git when available, falls back to downloading the latest GitHub ZIP when Git is unavailable, preserves `config.json`, `.venv`, recordings, snapshots, and the local database, then starts the updated `run.bat` automatically.

## Confirmed RTSP pattern

The CAM720 hardware used during development exposes paths such as:

```text
rtsp://CAMERA_IP:554/live/ch00_0
rtsp://CAMERA_IP:554/live/ch01_0
```

Replace the example address and credentials with your own values locally. The repository intentionally contains placeholders only; actual camera paths can vary by model and firmware.

## Camera discovery

Open **Settings → Cameras** and use **Find cameras on your network**. LocalCam scans a bounded private IPv4 subnet for the common RTSP ports `554`, `8554`, and `10554`. An open port is only a candidate: the correct RTSP path, username, and password still depend on the camera model and firmware.

## Optional capabilities

- `onvif-zeep` — ONVIF PTZ support
- `psutil` — CPU and RAM monitoring
- `pywin32` — Windows Service support (installed by the service installer when needed)
- `PyInstaller` and `pytest` are development/build dependencies rather than normal runtime requirements.

PTZ requires a compatible ONVIF service exposed by the camera. If it is unavailable, other LocalCam features can still be used.

## Windows Service

For an NVR-style installation that starts before a user signs in:

1. Run **`run.bat`** once so the LocalCam environment and runtime packages are installed.
2. Run **`install_service.bat` as Administrator**.
3. The service is configured for automatic startup and Windows service recovery.
4. Use `start_service.bat` and `stop_service.bat` for manual control.
5. Use `uninstall_service.bat` to remove the service.

The service runs outside an interactive desktop session. Follow the project’s service instructions before relying on unattended recording.

## Backup and restore

Administrators can use **Settings → Backup** to download an archive containing:

- `config.json`
- `localcam.sqlite3`
- Backup metadata

Backups may contain camera credentials. Store them securely. Restoring a backup replaces the local configuration and event database after validation.

## Build standalone applications

Run:

```text
build_exe.bat
```

PyInstaller places the desktop launcher and service build under `dist/`. **FFmpeg remains an external dependency** unless you package it separately.

## Architecture

```text
RTSP cameras
   ├── FFmpeg preview workers ── MJPEG ── browser
   ├── FFmpeg recorder (-c copy) ── MKV segments ── disk
   ├── Motion detector ── SQLite events ── optional snapshots
   └── Optional ONVIF PTZ ── camera control

LocalCam server
   ├── Authentication and roles
   ├── LAN camera discovery and RTSP health checks
   ├── Settings, backup, and restore
   ├── Archive, timeline, and playback
   └── System metrics
```

## Security

- Keep LocalCam on a trusted private LAN.
- Do not forward its web port directly to the public internet. Use a VPN for remote access.
- Never commit `config.json`, event databases, recordings, or backup archives containing credentials.
- Use unique, strong passwords for LocalCam accounts and cameras.

## Troubleshooting

- **Python not found:** Install Python 3.11+ from the official link above, then reopen `run.bat`.
- **Run launcher reports missing packages:** choose **Y** to let `run.bat` install or repair the runtime dependencies.
- **FFmpeg not found:** Install FFmpeg and add it to `PATH`, or set its executable path in LocalCam Settings.
- **Camera will not connect:** Verify the camera IP, RTSP path, credentials, network reachability, and that RTSP is enabled on the camera. Port discovery can only identify open candidate ports.
- **No recording space:** Check the configured storage folder, free disk space, and retention settings.
- **Browser shows an old layout:** stop the old LocalCam process, start the current `run.bat`, then refresh the browser with `Ctrl+F5`.

## License

MIT
