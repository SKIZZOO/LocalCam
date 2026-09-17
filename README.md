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
2. Double-click **`run.bat`**. It checks for Python and the project files, and offers to run setup if the local environment is missing.
3. If setup is needed, approve the prompt. `install.bat` creates `.venv` and installs the packages listed in `requirements.txt`.
4. If FFmpeg is missing, follow the official download page or configure its executable path in Settings.
5. When LocalCam starts, open the URL shown in the console.
6. Create the administrator account on first launch.
7. Go to **Settings → Cameras** and enter each camera's RTSP URL and credentials.
8. Go to **Settings → Storage** to choose recording and snapshot folders.

You can also run `install.bat` first and then launch `run.bat`.

## Confirmed RTSP pattern

The CAM720 hardware used during development exposes paths such as:

```text
rtsp://CAMERA_IP:554/live/ch00_0
rtsp://CAMERA_IP:554/live/ch01_0
```

Replace the example address and credentials with your own values locally. The repository intentionally contains placeholders only; actual camera paths can vary by model and firmware.

## Optional capabilities

The installer includes optional packages for additional features:

- `pywin32` — Windows Service support
- `onvif-zeep` — ONVIF PTZ support
- `psutil` — CPU and RAM monitoring

PTZ requires a compatible ONVIF service exposed by the camera. If it is unavailable, other LocalCam features can still be used.

## Windows Service

For an NVR-style installation that starts before a user signs in:

1. Run `install.bat`.
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
   ├── Settings, backup, and restore
   ├── Archive, timeline, and playback
   └── Health checks and system metrics
```

## Security

- Keep LocalCam on a trusted private LAN.
- Do not forward its web port directly to the public internet. Use a VPN for remote access.
- Never commit `config.json`, event databases, recordings, or backup archives containing credentials.
- Use unique, strong passwords for LocalCam accounts and cameras.

## Troubleshooting

- **Python not found:** Install Python 3.11+ from the official link above, then reopen `run.bat`.
- **Setup fails:** Read the error in the console, confirm internet access for package downloads, and rerun `install.bat`.
- **FFmpeg not found:** Install FFmpeg and add it to `PATH`, or set its executable path in LocalCam Settings.
- **Camera will not connect:** Verify the camera IP, RTSP path, credentials, network reachability, and that RTSP is enabled on the camera.
- **No recording space:** Check the configured storage folder, free disk space, and retention settings.

## License

MIT
