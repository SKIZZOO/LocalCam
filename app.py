from __future__ import annotations

import logging
import signal
import threading
import time
import webbrowser
from pathlib import Path

from core.config import load_config
from core.nvr import LocalCamServer
from core.preview import install_preview_tuning
import core.nvr as nvr
from core.process_guard import install_kill_on_exit_job

BASE_DIR = Path(__file__).resolve().parent
APP_VERSION = '0.9.5'


def main() -> None:
    logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(message)s')

    # Create the Windows kill-on-close Job Object before LocalCamServer is
    # constructed, because stream workers may start FFmpeg during init.
    # This also covers abrupt console-window closure and crashes.
    install_kill_on_exit_job()

    config = load_config()
    nvr.APP_VERSION = APP_VERSION
    install_preview_tuning()
    server = LocalCamServer(BASE_DIR)
    stopped = False
    stop_lock = threading.Lock()

    def stop_once(*_args) -> None:
        nonlocal stopped
        with stop_lock:
            if stopped:
                return
            stopped = True
        server.stop()

    # Graceful cleanup for Ctrl+C, termination, and Windows console close.
    signal.signal(signal.SIGINT, stop_once)
    if hasattr(signal, 'SIGTERM'):
        signal.signal(signal.SIGTERM, stop_once)
    if hasattr(signal, 'SIGBREAK'):
        signal.signal(signal.SIGBREAK, stop_once)

    server.start()

    url = server.url()
    logging.info('LocalCam is running at %s', url)
    if config.get('web_auto_open', True):
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    try:
        while True:
            time.sleep(2)
    except (KeyboardInterrupt, SystemExit):
        stop_once()
    finally:
        # Explicit shutdown first; the Job Object remains as a final safety
        # net for any FFmpeg descendant that survives unexpectedly.
        stop_once()


if __name__ == '__main__':
    main()
