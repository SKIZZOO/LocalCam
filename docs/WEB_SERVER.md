# LocalCam web server

Run `install.bat`, then `run.bat`. The server listens on `0.0.0.0:8765` by default. From another device on the same private LAN, open `http://IP-UL-PC-ULUI:8765/`.

Browser live view uses FFmpeg to create a low-resolution MJPEG preview from RTSP. Recording uses RTSP over TCP and `-c copy` into MKV segments to reduce CPU load.

The archive page lists MKV segments, exposes a browser playback endpoint, and allows download. Playback is converted on demand to fragmented MP4.

Do not port-forward port 8765 to the internet. The current web UI is intended for a trusted private LAN and does not provide HTTPS or strong authentication.
