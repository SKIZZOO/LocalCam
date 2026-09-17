from __future__ import annotations

import logging
import re
import signal
import sys
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
APP_VERSION = '0.10.1'


class QuietConsoleStream:
    """Hide successful high-frequency status polls, but keep errors and actions."""

    _routine_poll = re.compile(
        r'WEB "GET /api/(?:info|streams|auth/status)(?:\?[^ ]*)? HTTP/[0-9.]+" 200(?: |$)'
    )

    def __init__(self, stream):
        self.stream = stream
        self.pending = ''

    def write(self, text):
        self.pending += text
        while '\n' in self.pending:
            line, self.pending = self.pending.split('\n', 1)
            if not self._routine_poll.search(line):
                self.stream.write(line + '\n')
        return len(text)

    def flush(self):
        if self.pending:
            if not self._routine_poll.search(self.pending):
                self.stream.write(self.pending)
            self.pending = ''
        self.stream.flush()

    def __getattr__(self, name):
        return getattr(self.stream, name)


def main() -> None:
    # Keep routine browser polling out of the CMD window; errors, warnings,
    # startup messages, and user actions remain visible.
    sys.stdout = QuietConsoleStream(sys.stdout)
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
