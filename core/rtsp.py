from __future__ import annotations

import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit


# TCP is preferred for compatibility with cameras that reject UDP with RTSP 461.
# Discovery still falls back to UDP when TCP does not work.
RTSP_AUTO_TRANSPORT = 'tcp'
RTSP_TRANSPORTS = ('tcp', 'udp')

# Some inexpensive camera RTSP servers behave differently depending on the
# client User-Agent. VLC/LIVE555 is known to work with several such cameras.
RTSP_USER_AGENT = 'LibVLC/3.0.21 (LIVE555 Streaming Media)'

# Common paths used when ONVIF cannot provide a URI. /live/ch00_0 is a common
# main-stream path on several low-cost camera families, so it is tested first.
CHANNEL_RTSP_PATHS = (
    '/live/ch00_0',
    '/live/ch00_1',
    '/live/ch01_0',
    '/live/ch01_1',
)

COMMON_RTSP_PATHS = (
    '/live/ch00_0',
    '/live/ch00_1',
    '/live/profile0',
    '/live/profile1',
    '/live/profile100',
    '/live/profile101',
    '/live/ch01_0',
    '/live/ch01_1',
    '/onvif1',
    '/h264_stream',
    '/h264',
    '/ch0_0.h264',
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


def _run_probe(ffmpeg_path: str, target: str, transport: str, timeout_seconds: int,
               user_agent: str | None = RTSP_USER_AGENT) -> tuple[bool, str]:
    cmd = [
        ffmpeg_path,
        '-hide_banner',
        '-loglevel',
        'error',
        '-rtsp_transport',
        transport,
        '-allowed_media_types',
        'video',
        # FFmpeg's RTSP demuxer uses -timeout for socket I/O timeouts.
        # Some Windows FFmpeg builds do not expose the generic -rw_timeout
        # option, so using it makes a valid RTSP URL fail before probing.
        '-timeout',
        str(timeout_seconds * 1_000_000),
        '-probesize',
        '5000000',
        '-analyzeduration',
        '2000000',
    ]
    if user_agent:
        cmd.extend(['-user_agent', user_agent])
    cmd.extend(['-i', target, '-t', '1', '-f', 'null', '-'])
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


def test_rtsp(ffmpeg_path: str, url: str, username: str, password: str,
              timeout_seconds: int = 6) -> dict[str, Any]:
    target = with_credentials(url, username, password)
    errors: list[str] = []
    for transport in RTSP_TRANSPORTS:
        # First mimic VLC/LIVE555 because VLC is known to open this camera's
        # stream. Then fall back to FFmpeg's native Lavf User-Agent.
        for user_agent in (RTSP_USER_AGENT, None):
            ok, error = _run_probe(ffmpeg_path, target, transport, timeout_seconds, user_agent)
            if ok:
                return {
                    'ok': True,
                    'error': '',
                    'transport': transport,
                    'user_agent': user_agent or 'Lavf/default',
                    'url': redact_rtsp_url(target),
                }
            if error:
                label = 'VLC' if user_agent else 'Lavf'
                errors.append(f'{transport.upper()} ({label}): {error}')
    return {
        'ok': False,
        'error': '\n'.join(errors),
        'transport': 'TCP, then UDP',
        'url': redact_rtsp_url(target),
    }


def _failure_for(candidate: str, result: dict[str, Any]) -> str | None:
    error = str(result.get('error', '')).splitlines()
    if not error:
        return None
    return f'{redact_rtsp_url(candidate)}: {error[-1]}'


def _probe_batch(ffmpeg_path: str, candidates: list[tuple[str, str]], username: str, password: str,
                 timeout_seconds: int, seen: set[str], failures: list[str], checked: int) -> tuple[dict[str, Any] | None, int]:
    pending = [(candidate, method) for candidate, method in candidates if candidate not in seen]
    if not pending:
        return None, checked
    for candidate, _ in pending:
        seen.add(candidate)

    executor = ThreadPoolExecutor(max_workers=min(4, len(pending)), thread_name_prefix='rtsp-probe')
    futures = {
        executor.submit(test_rtsp, ffmpeg_path, candidate, username, password, timeout_seconds): (candidate, method)
        for candidate, method in pending
    }
    try:
        for future in as_completed(futures):
            candidate, method = futures[future]
            checked += 1
            try:
                result = future.result()
            except Exception as exc:
                result = {'ok': False, 'error': str(exc)}
            if result.get('ok'):
                executor.shutdown(wait=False, cancel_futures=True)
                return {
                    'ok': True,
                    'url': redact_rtsp_url(candidate),
                    'suggested_url': candidate,
                    'transport': result.get('transport', ''),
                    'user_agent': result.get('user_agent', ''),
                    'method': method,
                    'candidates_checked': checked,
                }, checked
            failure = _failure_for(candidate, result)
            if failure:
                failures.append(failure)
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    return None, checked


def _probe_all(ffmpeg_path: str, candidates: list[tuple[str, str]], username: str, password: str,
              timeout_seconds: int, seen: set[str], failures: list[str], checked: int) -> tuple[list[dict[str, Any]], int]:
    pending = [(candidate, method) for candidate, method in candidates if candidate not in seen]
    if not pending:
        return [], checked
    for candidate, _ in pending:
        seen.add(candidate)
    found: list[dict[str, Any]] = []
    executor = ThreadPoolExecutor(max_workers=min(4, len(pending)), thread_name_prefix='rtsp-probe-all')
    futures = {
        executor.submit(test_rtsp, ffmpeg_path, candidate, username, password, timeout_seconds): (candidate, method)
        for candidate, method in pending
    }
    try:
        for future in as_completed(futures):
            candidate, method = futures[future]
            checked += 1
            try:
                result = future.result()
            except Exception as exc:
                result = {'ok': False, 'error': str(exc)}
            if result.get('ok'):
                found.append({
                    'ok': True,
                    'url': redact_rtsp_url(candidate),
                    'suggested_url': candidate,
                    'transport': result.get('transport', ''),
                    'user_agent': result.get('user_agent', ''),
                    'method': method,
                    'candidates_checked': checked,
                })
            else:
                failure = _failure_for(candidate, result)
                if failure:
                    failures.append(failure)
    finally:
        executor.shutdown(wait=True)
    return found, checked


def snapshot_rtsp(ffmpeg_path: str, url: str, username: str, password: str,
                 timeout_seconds: int = 5) -> dict[str, Any]:
    target = with_credentials(url, username, password)
    errors: list[str] = []
    for transport in RTSP_TRANSPORTS:
        cmd = [
            ffmpeg_path, '-hide_banner', '-loglevel', 'error',
            '-rtsp_transport', transport,
            '-user_agent', RTSP_USER_AGENT,
            '-timeout', str(timeout_seconds * 1_000_000),
            '-probesize', '3000000',
            '-analyzeduration', '1000000',
            '-i', target,
            '-map', '0:v:0',
            '-an',
            '-frames:v', '1',
            '-q:v', '4',
            '-f', 'mjpeg',
            'pipe:1',
        ]
        try:
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout_seconds + 3,
            )
        except FileNotFoundError:
            return {'ok': False, 'error': 'FFmpeg executable not found'}
        except subprocess.TimeoutExpired:
            errors.append(f'{transport.upper()}: snapshot timed out')
            continue
        if proc.returncode == 0 and proc.stdout:
            return {
                'ok': True,
                'mime': 'image/jpeg',
                'data': __import__('base64').b64encode(proc.stdout).decode('ascii'),
            }
        if proc.stderr:
            errors.append(proc.stderr.decode('utf-8', 'replace').strip().splitlines()[-1])
    return {'ok': False, 'error': '\n'.join(e for e in errors if e) or 'Could not capture a snapshot.'}


def _channel_candidates(base: str) -> list[str]:
    return [base + path for path in CHANNEL_RTSP_PATHS]


def discover_and_test_rtsp(ffmpeg_path: str, url: str, username: str, password: str,
                           timeout_seconds: int = 2) -> dict[str, Any]:
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
    found_streams: list[dict[str, Any]] = []

    # Always test an explicitly configured media URL first. This is important
    # when a user has already verified the exact URL in VLC.
    if not _looks_like_root_path(original):
        result, checked = _probe_batch(
            ffmpeg_path,
            [(original, 'configured URL')],
            username,
            password,
            timeout_seconds,
            seen,
            failures,
            checked,
        )
        if result:
            found_streams.append(result)

    base = _candidate_base(original)
    if base:
        # Probe all channel/stream variants together. Some cameras expose a
        # second feed as ch01_0/ch01_1 even when ch00_0 or ch00_1 is already
        # working, so detection must collect every successful candidate.
        channel_candidates = [(base + path, 'channel stream') for path in CHANNEL_RTSP_PATHS]
        channel_results, checked = _probe_all(
            ffmpeg_path,
            channel_candidates,
            username,
            password,
            timeout_seconds,
            seen,
            failures,
            checked,
        )
        found_streams.extend(channel_results)

        # If any of the known channel variants worked, we have the useful
        # information the user asked for. Do not keep probing every generic
        # RTSP path or start slow ONVIF/WSDL discovery; that made the UI sit on
        # "Detecting..." for minutes even though a working stream was already
        # found.
        if not channel_results:
            common = [(base + path, 'common path') for path in COMMON_RTSP_PATHS if base + path not in seen]
            result, checked = _probe_batch(
                ffmpeg_path,
                common,
                username,
                password,
                timeout_seconds,
                seen,
                failures,
                checked,
            )
            if result:
                found_streams.append(result)

    # ONVIF is only a final fallback when RTSP path probing found nothing.
    if not found_streams:
        onvif = [(uri, 'ONVIF') for uri in _onvif_stream_uris(host, username, password)]
        for start in range(0, len(onvif), 4):
            result, checked = _probe_batch(
                ffmpeg_path,
                onvif[start:start + 4],
                username,
                password,
                timeout_seconds,
                seen,
                failures,
                checked,
            )
            if result:
                found_streams.append(result)

    # De-duplicate successful URLs and attach previews in parallel so multiple
    # working feeds do not turn snapshot capture into another long serial wait.
    unique: list[dict[str, Any]] = []
    seen_success: set[str] = set()
    for item in found_streams:
        candidate = str(item.get('suggested_url') or item.get('url') or '')
        if not candidate or candidate in seen_success:
            continue
        seen_success.add(candidate)
        unique.append(dict(item))

    if unique:
        snapshot_timeout = min(5, max(2, timeout_seconds + 1))
        executor = ThreadPoolExecutor(max_workers=min(4, len(unique)), thread_name_prefix='rtsp-snapshot')
        futures = {
            executor.submit(
                snapshot_rtsp,
                ffmpeg_path,
                str(item.get('suggested_url') or item.get('url') or ''),
                username,
                password,
                snapshot_timeout,
            ): item
            for item in unique
        }
        try:
            for future in as_completed(futures):
                item = futures[future]
                try:
                    snap = future.result()
                except Exception as exc:
                    snap = {'ok': False, 'error': str(exc)}
                item['preview'] = snap.get('data', '')
                item['preview_mime'] = snap.get('mime', '')
        finally:
            executor.shutdown(wait=True)

    if unique:
        primary = dict(unique[0])
        primary['streams'] = unique
        return primary

    shown = failures[:12]
    if len(failures) > 12:
        shown.append(f'… {len(failures) - 12} more candidates also failed.')
    return {
        'ok': False,
        'url': redact_rtsp_url(original),
        'error': '\n'.join(shown) or 'No usable RTSP stream was found.',
        'candidates_checked': checked,
    }
