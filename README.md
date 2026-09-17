# LocalCam

Self-hosted LAN NVR for JOOAN / CAM720 cameras with RTSP support.

LocalCam provides a modern browser dashboard for live monitoring, recording, motion events, playback, storage management, and local configuration. It is designed to run on a Windows PC and can be opened from the PC or from a phone/tablet on the same LAN.

## Features

- Multi-camera live dashboard using a shared FFmpeg preview worker per stream
- Direct RTSP recording with stream copy (`-c copy`) to reduce CPU usage
- Continuous, motion-only, and manual recording modes
- Configurable recording segments
- Automatic retention cleanup and minimum-free-space protection
- 24-hour archive timeline with click-to-play segments
- Date/camera/search filters
- Browser playback with on-demand MP4 conversion
- Download individual recording segments
- Motion events stored in SQLite
- Event snapshots stored locally
- Event acknowledgement
- Camera health and RTSP connectivity probes
- Local web login with PBKDF2 password hashing
- Responsive interface for desktop and mobile
- Browser notifications for new motion events
- Configurable storage paths, FFmpeg path, server port/bind, live quality, motion sensitivity, cameras, and credentials
- Optional Windows startup task for a dedicated NVR PC
- No cloud dependency for the RTSP workflows
- No camera passwords committed to Git

## RTSP paths

The project supports RTSP URLs such as:

```text
rtsp://CAMERA_IP:554/live/ch00_0
rtsp://CAMERA_IP:554/live/ch01_0
```

The example configuration intentionally contains placeholders. Put real LAN camera addresses and RTSP credentials into the local `config.json` only.

## Requirements

- Windows 10/11
- Python 3.11+ when running from source
- FFmpeg available in `PATH` or configured in `config.json`
- Modern browser (Chrome, Edge, Firefox, Safari on mobile)
- Enough storage for the desired retention period

VLC is not required for the web application because live preview and recording are handled by FFmpeg.

## Quick start

1. Run `install.bat`.
2. Run `run.bat`.
3. Open the displayed local URL.
4. On first launch, create the LocalCam web password.
5. Open **Settings** and configure your camera streams and recording folder.

A typical storage layout is:

```text
G:\LocalCam\recordings
G:\LocalCam\snapshots
```

Do not commit `config.json` after entering passwords.

## Web access from a phone

By default the server binds to `0.0.0.0:8765`. On the PC, LocalCam shows the LAN address. From another device on the same home network, open:

```text
http://PC_LAN_IP:8765/
```

If Windows Firewall blocks access, run `allow_web_firewall.bat` once as Administrator and keep the network profile Private.

Do not port-forward the LocalCam port to the public internet. For remote access, use a VPN.

## Automatic startup

For a dedicated NVR PC, install the optional startup task:

```text
install_startup_task.bat
```

To remove it later, run:

```text
remove_startup_task.bat
```

## Build a standalone EXE

After installing dependencies, run:

```text
build_exe.bat
```

The generated executable is placed under `dist/LocalCam.exe`. FFmpeg must still be installed separately or supplied alongside the EXE and configured with its path.

## Architecture

```text
RTSP cameras
      |
      +--> shared FFmpeg preview worker --> MJPEG --> browser
      |
      +--> FFmpeg recorder (-c copy) --> MKV segments --> disk
      |
      +--> motion detector --> SQLite events --> snapshots
      |
      v
 LocalCam threaded web server
      |
      +--> authentication / settings / archive / health APIs
```

## Security

The web UI uses a local password and an HttpOnly session cookie. Camera passwords remain in local `config.json`, which is ignored by Git.

Keep the service on a trusted LAN. Do not expose port 8765 directly to the public internet.

## License

MIT
