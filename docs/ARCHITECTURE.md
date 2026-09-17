# Architecture

LocalCam is local-first and keeps camera credentials on the NVR host.

```text
RTSP cameras
    |
    +---- PreviewWorker ----> in-memory JPEG frames ----> /live/<camera>.mjpg
    |
    +---- Recorder ----------> MKV segments ------------> record_root
    |
    +---- MotionDetector ----> SQLite events -----------> localcam.sqlite3
    |                             |
    |                             +---- event snapshots -> snapshot_root
    |
    +---- PTZController (optional ONVIF) ----> camera control

HTTP server
    |
    +---- local login / roles / sessions
    +---- dashboard / archive / events / settings
    +---- backup / restore
    +---- health / system metrics

Windows Service
    |
    +---- starts LocalCam before interactive logon
```

The browser never receives RTSP credentials. It asks the local server for a browser-friendly MJPEG stream while LocalCam handles RTSP authentication server-side.
