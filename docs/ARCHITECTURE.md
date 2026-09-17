# Architecture

LocalCam is a local-first NVR. The browser application is the main user interface, while FFmpeg handles the media pipeline.

## Runtime flow

```text
RTSP cameras
    |
    +--> shared FFmpeg preview worker --> JPEG/MJPEG --> browser
    |
    +--> FFmpeg recorder (-c copy) --> MKV segments --> storage disk
    |
    +--> motion detector --> SQLite event --> optional JPEG snapshot
    |
    v
LocalCam threaded HTTP server
    |
    +--> authentication
    +--> settings API
    +--> archive/timeline API
    +--> health probes
```

Each camera stream has one shared preview worker. Multiple browser clients consume the same latest-frame buffer instead of creating a new RTSP decoder for every client.

## Recording

Recordings are stored as:

```text
<record_root>/<safe camera name>/<YYYY-MM-DD>/<timestamp>.mkv
```

The recorder uses RTSP over TCP and `-c copy`, so the camera video is not re-encoded for normal recording. This keeps the recording path efficient and preserves the source stream.

## Motion detection

The motion detector compares low-resolution grayscale frames from the preview worker. It uses a mean pixel-difference threshold plus a changed-pixel fraction threshold. Motion starts a SQLite event and can start a recording when the global recording mode is `motion`.

## Authentication

Local web authentication uses PBKDF2-SHA256 password hashes and random session identifiers stored in HttpOnly cookies. The session is kept in memory and expires automatically.

## Storage safety

The recorder removes files older than the configured retention period and then removes the oldest recordings if the configured minimum free space would otherwise be violated.
