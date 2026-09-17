# Windows installation

## Source installation

1. Install Python 3.11 or newer.
2. Install FFmpeg and make sure `ffmpeg.exe` is in `PATH`, or note its full path.
3. Double-click `install.bat`.
4. Double-click `run.bat`.
5. Open the LocalCam URL shown in the console.
6. On first run, create the LocalCam web password.
7. Open **Settings** and add the RTSP streams.

## LAN access

The default web bind is `0.0.0.0` and the default port is `8765`.

Open from another device on the same private LAN with:

```text
http://PC_LAN_IP:8765/
```

If Windows Firewall blocks LAN access, run `allow_web_firewall.bat` once as Administrator.

## Build a standalone EXE

Run `build_exe.bat` after `install.bat`.

The generated executable is written to:

```text
dist/LocalCam.exe
```

FFmpeg remains an external runtime dependency unless you choose to ship it separately and configure `ffmpeg_path`.
