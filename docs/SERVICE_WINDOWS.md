# Windows Service

LocalCam can run as `LocalCamService` using pywin32.

## Install

Run `install_service.bat` as Administrator after running `install.bat`.

The installer:

- installs pywin32 in the virtual environment
- registers `LocalCamService`
- sets startup to `auto`
- configures service recovery to restart on failure
- starts the service

## Manual control

- `start_service.bat`
- `stop_service.bat`
- `uninstall_service.bat`

The service does not require an interactive desktop session, so the NVR can start before Windows user logon.

Use `run.bat` for development and troubleshooting instead of running both modes at the same time.
