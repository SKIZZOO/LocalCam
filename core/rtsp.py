from __future__ import annotations

import subprocess
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit


# FFmpeg accepts one rtsp_transport value per invocation. Consumer cameras vary,
# so LocalCam probes UDP first and TCP second instead of passing an invalid list.
RTSP_AUTO_TRANSPORT = 'udp'
RTSP_TRANSPORTS = ('udp', 'tcp')

# Common paths used when ONVIF cannot provide a URI. The /live/ch00_0 path is
# tried first because it is the known working pattern for this LocalCam camera.
# A candidate is accepted only when FFmpeg can actually read media from it.
COMMON_RTSP_PATHS = (
    '/live/ch00_0',
    '/live/ch00_1',
    '/stream1',
    '/stream2',
    '/live',
    '/live1',
    '/live0',
    '/video1',
    '/video2',
    '/videoMain',
    '/videoSub',
    '/h264Preview_01_main',
    '/h264Preview_01_sub',
    '/Streaming/Channels/101',
    '/Streaming/Channels/102',
    '/cam/realmonitor?channel=1&subtype=0',
    '/cam/realmonitor?channel=1&subtype=1',
    '/11',
    '/12',
)


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
    """Remove username/password from RTSP URLs before returning them to the UI."""
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


def _candidate_base(url: str) -> str | None:
    try:
        parsed = urlsplit(url)
        if parsed.scheme.lower() != 'rtsp' or not parsed.hostname:
            return None
        return f'rtsp://{parsed.hostname}:{parsed.port or 554}'
    except ValueError:
        return None


def _looks_like_root_path(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        return parsed.scheme.lower() == 'rtsp' and parsed.path in ('', '/') and not parsed.query
    except ValueError:
        return False


def _onvif_stream_uris(host: str, username: str, password: str, ports=(80, 8080, 8000, 8899)) -> list[str]:
    """Ask common ONVIF HTTP ports for media profile stream URIs."""
    try:
        from onvif import ONVIFCamera  # type: ignore
    except Exception:
        return []

    found: list[str] = []
    for port in ports:
        try:
            camera = ONVIFCamera(host, int(port), username, password, no_cache=True)
            media = camera.create_media_service()
            profiles = media.GetProfiles() or []
            for profile in profiles:
                token = getattr(profile, 'token', '')
                if not token:
                    continue
                response = media.GetStreamUri({
                    'StreamSetup': {
                        'Stream': 'RTP-Unicast',
                        'Transport': {'Protocol': 'RTSP'},
                    },
                    'ProfileToken': token,
                })
                uri = str(getattr(response, 'Uri', '') or '').strip()
                if uri.lower().startswith('rtsp://') and uri not in found:
                    found.append(uri)
            if found:
                return found
        except Exception:
            continue
    return found


def _run_probe(ffmpeg_path: str, target: str, transport: str, timeout_seconds: int) -> tuple[bool, str]:
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
        return False, 'FFmpeg executable not found'
    except subprocess.TimeoutExpired:
        return False, f'RTSP probe timed out using {transport.upper()}'

    if proc.returncode == 0:
        return True, ''
    return False, proc.stderr.strip() or f'RTSP probe failed using {transport.upper()}'


def test_rtsp(
    ffmpeg_path: str,
    url: str,
    username: str,
    password: str,
    timeout_seconds: int = 6,
) -> dict[str, Any]:
    """Probe an RTSP stream, trying UDP and then TCP with valid FFmpeg options."""
    target = with_credentials(url, username, password)
    errors: list[str] = []

    for transport in RTSP_TRANSPORTS:
        ok, error = _run_probe(ffmpeg_path, target, transport, timeout_seconds)
        if ok:
            return {'ok': True, 'error': '', 'transport': transport, 'url': redact_rtsp_url(target)}
        if error:
            errors.append(f'{transport.upper()}: {error}')

    return {
        'ok': False,
        'error': '\n'.join(errors),
        'transport': 'UDP, then TCP',
        'url': redact_rtsp_url(target),
    }


def _probe_candidates(
    ffmpeg_path: str,
    candidates: list[tuple[str, str]],
    username: str,
    password: str,
    timeout_seconds: int,
    seen: set[str],
    failures: list[str],
    checked: int,
) -> tuple[dict[str, Any] | None, int]:
    """Probe candidates in order and return on the first working stream."""
    for candidate, method in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        checked += 1
        result = test_rtsp(ffmpeg_path, candidate, username, password, timeout_seconds)
        if result.get('ok'):
            return {
                'ok': True,
                'url': redact_rtsp_url(candidate),
                'suggested_url': candidate,
                'transport': result.get('transport', ''),
                'method': method,
                'candidates_checked': checked,
            }, checked
        error = str(result.get('error', '')).splitlines()[-1:]
        if error:
            failures.append(f'{redact_rtsp_url(candidate)}: {error[0]}')
    return None, checked


def discover_and_test_rtsp(
    ffmpeg_path: str,
    url: str,
    username: str,
    password: str,
    timeout_seconds: int = 2,
) -> dict[str, Any]:
    """Find and validate a usable RTSP URL, prioritizing fast known paths."""
    original = url.strip()
    try:
        parsed = urlsplit(original)
        host = parsed.hostname or ''
    except ValueError:
        return {'ok': False, 'error': 'Invalid RTSP URL.'}

    if not host:
        return {'ok': False, 'error': 'Camera host/IP is required.'}

    seen: set[str] = set()
    failures: list[str] = []
    checked = 0

    # Probe common RTSP media paths FIRST. This avoids waiting for ONVIF SOAP/WSDL
    # discovery and makes the known /live/ch00_0 URL return almost immediately.
    if _looks_like_root_path(original):
        base = _candidate_base(original)
        if base:
            common_candidates = [(base + path, 'common path') for path in COMMON_RTSP_PATHS]
            result, checked = _probe_candidates(
                ffmpeg_path, common_candidates, username, password,
                timeout_seconds, seen, failures, checked,
            )
            if result:
                return result

        # ONVIF is a slow fallback only after the normal RTSP paths fail.
        onvif_candidates = [(uri, 'ONVIF') for uri in _onvif_stream_uris(host, username, password)]
        result, checked = _probe_candidates(
            ffmpeg_path, onvif_candidates, username, password,
            timeout_seconds, seen, failures, checked,
        )
        if result:
            return result
    else:
        result, checked = _probe_candidates(
            ffmpeg_path, [(original, 'configured URL')], username, password,
            timeout_seconds, seen, failures, checked,
        )
        if result:
            return result

    return {
        'ok': False,
        'url': redact_rtsp_url(original),
        'error': '\n'.join(failures[-8:]) or 'No usable RTSP stream was found.',
        'candidates_checked': checked,
    }
