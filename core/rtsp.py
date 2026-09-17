from __future__ import annotations

import subprocess
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit


RTSP_AUTO_TRANSPORT = 'tcp,udp'


def with_credentials(url: str, username: str, password: str) -> str:
    """Return an RTSP URL with safely URL-encoded credentials."""
    parsed = urlsplit(url)
    if parsed.scheme.lower() != 'rtsp' or '@' in parsed.netloc or not username:
        return url

    host = parsed.hostname or ''
    if parsed.port:
        host += f':{parsed.port}'
    userinfo = quote(username, safe='')
    if password:
        userinfo += ':' + quote(password, safe='')
    return urlunsplit((parsed.scheme, f'{userinfo}@{host}', parsed.path, parsed.query, parsed.fragment))


def test_rtsp(
    ffmpeg_path: str,
    url: str,
    username: str,
    password: str,
    timeout_seconds: int = 6,
) -> dict[str, Any]:
    """Probe an RTSP stream without writing media to disk.

    Cameras vary in whether they accept RTP over TCP or UDP. FFmpeg supports
    supplying multiple transports and will try them one at a time when one
    transport setup fails.
    """
    target = with_credentials(url, username, password)
    cmd = [
        ffmpeg_path,
        '-hide_banner',
        '-loglevel',
        'error',
        '-rtsp_transport',
        RTSP_AUTO_TRANSPORT,
        '-rw_timeout',
        str(timeout_seconds * 1_000_000),
        '-i',
        target,
        '-t',
        '1',
        '-f',
        'null',
        '-',
    ]
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds + 3,
            text=True,
            encoding='utf-8',
            errors='replace',
        )
    except FileNotFoundError:
        return {'ok': False, 'error': 'FFmpeg executable not found'}
    except subprocess.TimeoutExpired:
        return {'ok': False, 'error': 'RTSP probe timed out'}
    error = proc.stderr.strip() if proc.returncode else ''
    return {
        'ok': proc.returncode == 0,
        'error': error,
        'transport': 'auto (TCP, then UDP)',
    }
