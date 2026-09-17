from __future__ import annotations

import logging
import threading
import time
import webbrowser
from pathlib import Path

from core.config import load_config
from core.nvr import LocalCamServer
from core.preview import install_preview_tuning
import core.nvr as nvr

BASE_DIR = Path(__file__).resolve().parent
APP_VERSION = '0.9.4'


def main() -> None:
    logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(message)s')
    config = load_config()
    nvr.APP_VERSION = APP_VERSION
    install_preview_tuning()
    server = LocalCamServer(BASE_DIR)
    server.start()

    url = server.url()
    logging.info('LocalCam is running at %s', url)
    if config.get('web_auto_open', True):
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    try:
        while True:
            time.sleep(2)
    except KeyboardInterrupt:
        server.stop()


if __name__ == '__main__':
    main()
