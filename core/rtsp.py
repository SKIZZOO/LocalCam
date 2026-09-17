from __future__ import annotations

import subprocess
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit


# FFmpeg's rtsp_transport option accepts one transport at a time, not a
# comma-separated fallback list. The cameras reported by LocalCam commonly
# reject interleaved TCP, so UDP is the default transport for live/recording
# sessions. The probe below can still try multiple transports explicitly.
RTSP_AUTO_TRANSPORT = 'udp'
RTSP_TRANSPORTS = ('udp', 'tcp')


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


def redact_rtsp_url(url: str) -> str:
    """Remove username/password from an RTSP URL for safe logging."""
    try:
        parsed = urlsplit(url)
        if parsed.scheme.lower() != 'rtsp' or '@' not in parsed.netloc:
            return url
        host = parsed.hostname or ''
        if parsed.port:
            host += f':{parsed.port}'
        return urlunsplit((parsed.scheme, f'***@{host}', parsed.path, parsed.query, parsed.fragment))
    except ValueError:
        return '<redacted RTSP URL>'


def test_rtsp(
    ffmpeg_path: str,
    url: str,
    username: str,
    password: str,
    timeout_seconds: int = 6,
) -> dict[str, Any]:
    """Probe an RTSP stream without writing media to disk.

    Try UDP first because many consumer cameras reject RTP interleaved over
    TCP. If UDP fails, try TCP as a compatibility fallback. Each FFmpeg
    invocation receives exactly one valid rtsp_transport value.
    """
    target = with_credentials(url, username, password)
    last_error = ''

    for transport in RTSP_TRANSPORTS:
        cmd = [
            ffmpeg_path,
            '-hide_banner',
            '-loglevel',
            'error',
            '-rtsp_transport',
            transport,
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
            last_error = f'RTSP probe timed out using {transport.upper()}'
            continue

        if proc.returncode == 0:
            return {
                'ok': True,
                'error': '',
                'transport': transport,
            }

        last_error = proc.stderr.strip() or f'RTSP probe failed using {transport.upper()}'

    return {
        'ok': False,
        'error': last_error,
        'transport': 'UDP, then TCP',
        'url': redact_rtsp_url(target),
    }
