# LocalCam

Local NVR / web dashboard for JOOAN / CAM720 RTSP cameras on a private LAN.

## Included

- responsive dashboard for PC and phone
- local RTSP camera configuration
- live preview through FFmpeg snapshots
- manual start/stop recording
- continuous recorder with MKV segments and `-c copy`
- retention and minimum-free-space cleanup
- archive listing and browser playback/download
- editable storage, server and camera settings

## Windows setup

1. Install Python 3.11+ and FFmpeg.
2. Run `install.bat`.
3. Run `run.bat`.
4. Open `http://127.0.0.1:8765/` or `http://IP-UL-PC-ULUI:8765/` on the LAN.
5. Configure cameras in **Setări**.

The repository intentionally contains only `config.example.json`. The real `config.json` is ignored by Git.

## Example RTSP paths

Typical CAM720 paths confirmed for the author's hardware are `live/ch00_0` and `live/ch01_0`; replace the IP and credentials locally.

## Notes

The web server is intentionally LAN-only and has no cloud dependency. Do not expose port 8765 directly to the public internet without adding authentication and TLS.
