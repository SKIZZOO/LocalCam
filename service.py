from __future__ import annotations

import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def run_console() -> None:
    from server import LocalCamServer
    app = LocalCamServer(BASE_DIR)
    app.start()
    try:
        while True:
            time.sleep(2)
    except KeyboardInterrupt:
        app.stop()


try:
    import servicemanager
    import win32event
    import win32service
    import win32serviceutil
except ImportError:
    servicemanager = None


if servicemanager is not None:
    class LocalCamWindowsService(win32serviceutil.ServiceFramework):
        _svc_name_ = 'LocalCamService'
        _svc_display_name_ = 'LocalCam NVR Service'
        _svc_description_ = 'Local NVR service for RTSP security cameras.'

        def __init__(self, args) -> None:
            super().__init__(args)
            self.stop_event = win32event.CreateEvent(None, 0, 0, None)
            self.app = None

        def SvcStop(self):  # noqa: N802
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            win32event.SetEvent(self.stop_event)
            if self.app:
                self.app.stop()

        def SvcDoRun(self):  # noqa: N802
            servicemanager.LogInfoMsg('LocalCam service starting')
            from server import LocalCamServer
            self.app = LocalCamServer(BASE_DIR)
            self.app.start()
            while True:
                rc = win32event.WaitForSingleObject(self.stop_event, 1000)
                if rc == win32event.WAIT_OBJECT_0:
                    break
            if self.app:
                self.app.stop()


if __name__ == '__main__':
    if servicemanager is None or sys.platform != 'win32':
        run_console()
    else:
        import win32serviceutil
        win32serviceutil.HandleCommandLine(LocalCamWindowsService)
