# Windows installation

## Source installation

1. Install Python 3.11+.
2. Install FFmpeg and make sure `ffmpeg.exe` is in `PATH`, or set the full path in LocalCam Settings.
3. Run `install.bat`.
4. Run `run.bat` for interactive console mode.
5. Open the displayed local URL and complete first-run administrator setup.

## Windows Service

For a headless NVR that starts before a user logs in:

1. Run `install.bat`.
2. Open Command Prompt **as Administrator**.
3. Run `install_service.bat`.
4. Confirm that `LocalCamService` is running in Windows Services.

The installer configures automatic startup and service recovery.

## Firewall

If another device on the same private LAN cannot open the dashboard, run `allow_web_firewall.bat` once as Administrator.

## Web access

On the LocalCam PC:

```text
http://127.0.0.1:8765/
```

On another device on the same LAN:

```text
http://PC_LAN_IP:8765/
```

Keep the Windows network profile set to **Private**.

## Standalone build

Run `build_exe.bat` after `install.bat`. PyInstaller outputs the application and service builds under `dist/`.
