# LocalCam

Self-hosted LAN NVR for JOOAN / CAM720 RTSP cameras with a modern browser dashboard.

## Features

- Multi-camera live monitoring with shared FFmpeg preview workers
- Direct RTSP recording with stream copy (`-c copy`) into MKV segments
- Continuous, motion-only, and manual recording modes
- Automatic reconnect and recorder watchdogs
- Retention and minimum-free-space cleanup
- 24-hour archive timeline with click-to-play segments
- Browser playback, seeking, playback speed controls, fullscreen, and downloads
- Motion events stored in SQLite with optional event snapshots
- Browser notifications for new motion events
- RTSP health probes
- Optional ONVIF PTZ control when the camera exposes ONVIF/PTZ
- Local multi-user authentication with Viewer / Operator / Administrator roles
- PBKDF2-SHA256 password hashing and HttpOnly sessions
- Login rate limiting and same-origin checks for mutating web requests
- Backup and restore of local configuration and event database
- Responsive desktop/mobile UI
- Windows Service support for automatic startup before user login
- Automatic service recovery configuration
- Standalone EXE build support with PyInstaller
- No cloud dependency for RTSP workflows
- No camera passwords committed to Git

## Confirmed RTSP pattern

The CAM720 hardware used during development exposes paths such as:

```text
rtsp://CAMERA_IP:554/live/ch00_0
rtsp://CAMERA_IP:554/live/ch01_0
```

Replace camera addresses and credentials locally. The repository intentionally contains placeholders only.

## Requirements

- Windows 10/11
- Python 3.11+ for source execution
- FFmpeg available in `PATH` or configured in Settings
- Modern Chromium/Firefox/Safari-class browser
- Enough disk capacity for the desired retention period

Optional capabilities are installed by `install.bat`:

- `pywin32` for the Windows Service
- `onvif-zeep` for ONVIF PTZ
- `psutil` for CPU/RAM monitoring

## Quick start

1. Run `install.bat`.
2. Run `run.bat`.
3. Open the displayed URL.
4. On first launch, create the administrator account.
5. Open **Settings → Cameras** and enter your RTSP URLs and credentials.
6. Open **Settings → Storage** and choose the recording/snapshot folders.

## Windows Service

For a real NVR-style installation that starts before Windows user login:

1. Run `install.bat`.
2. Run `install_service.bat` **as Administrator**.
3. The service is configured for automatic startup and Windows service recovery.
4. Use `start_service.bat` and `stop_service.bat` for manual control.
5. Use `uninstall_service.bat` to remove the service.

The service runs as a Windows service and does not require an interactive desktop session.

## Backup and restore

Administrators can use **Settings → Backup** to download a backup archive containing:

- `config.json`
- `localcam.sqlite3`
- backup metadata

The backup may contain camera credentials and must be stored securely. Restore replaces the local configuration and event database after validation.

## ONVIF PTZ

PTZ is optional. Enable **ONVIF PTZ** for a camera and enter the ONVIF endpoint/credentials. The UI exposes directional controls when PTZ is enabled. If the camera does not expose compatible ONVIF PTZ services, the rest of LocalCam continues to work normally.

## Build standalone applications

Run:

```text
build_exe.bat
```

PyInstaller outputs the desktop launcher and service build under `dist/`. FFmpeg remains an external dependency unless you package it separately.

## Architecture

```text
RTSP cameras
   |
   +--> FFmpeg preview workers --> MJPEG --> browser
   |
   +--> FFmpeg recorder (-c copy) --> MKV --> disk
   |
   +--> motion detector --> SQLite events --> snapshots
   |
   +--> optional ONVIF PTZ --> camera control
   |
   v
LocalCam server
   |
   +--> authentication / roles
   +--> settings / backup / restore
   +--> archive / timeline / playback
   +--> health / system metrics
```

## Security notes

Keep LocalCam on a trusted private LAN. Do not port-forward the web port to the public internet. For remote access, use a VPN.

Never commit `config.json`, event databases, recordings, or backup archives containing credentials.

## License

MIT
