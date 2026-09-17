from __future__ import annotations

import copy
import hashlib
import json
import os
import secrets
import shutil
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / 'config.json'
EXAMPLE_PATH = BASE_DIR / 'config.example.json'

DEFAULT_CONFIG: dict[str, Any] = {
    'app_name': 'LocalCam',
    'version': '0.8.0',
    'ffmpeg_path': 'ffmpeg',
    'record_root': str(BASE_DIR / 'recordings'),
    'snapshot_root': str(BASE_DIR / 'snapshots'),
    'record_mode': 'manual',
    'segment_minutes': 10,
    'min_free_gb': 20,
    'max_retention_days': 30,
    'web_enabled': True,
    'web_bind': '0.0.0.0',
    'web_port': 8765,
    'web_live_fps': 8,
    'web_live_width': 1280,
    'web_auto_open': True,
    'web_auth_enabled': True,
    'notifications_enabled': True,
    'web_password_hash': '',
    'web_secret': '',
    'web_session_hours': 12,
    'service_name': 'LocalCamService',
    'motion': {
        'enabled': True,
        'interval_seconds': 0.5,
        'threshold': 8.0,
        'min_changed_fraction': 0.012,
        'cooldown_seconds': 15.0,
        'save_event_snapshots': True,
    },
    'ptz_defaults': {'enabled': False, 'port': 80},
    'cameras': [],
}


def merge_defaults(defaults: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(defaults)
    for key, value in data.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge_defaults(result[key], value)
        else:
            result[key] = value
    return result


def resolve_ffmpeg(value: str | None) -> str:
    """Return a runnable FFmpeg path when it is available.

    Config normally stores just ``ffmpeg`` so Windows package-manager installs can
    be picked up automatically after PATH changes.  We resolve that command at
    runtime instead of passing a missing executable name directly to Popen.
    """
    configured = os.path.expandvars(os.path.expanduser(str(value or 'ffmpeg').strip())) or 'ffmpeg'

    direct = Path(configured)
    try:
        if direct.is_file():
            return str(direct.resolve())
    except OSError:
        pass

    found = shutil.which(configured)
    if found:
        return found

    # A custom path may be stale after an FFmpeg reinstall. Fall back to the
    # normal executable name before reporting the original configured value.
    if configured.lower() != 'ffmpeg':
        found = shutil.which('ffmpeg') or shutil.which('ffmpeg.exe')
        if found:
            return found

    return configured


def hash_password(password: str, salt: str | None = None, iterations: int = 390_000) -> str:
    salt = salt or secrets.token_hex(16)
    derived = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt.encode('utf-8'), iterations)
    return f'pbkdf2_sha256${iterations}${salt}${derived.hex()}'


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt, expected = encoded.split('$', 3)
        if algorithm != 'pbkdf2_sha256':
            return False
        derived = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt.encode('utf-8'), int(iterations))
        return secrets.compare_digest(derived.hex(), expected)
    except (ValueError, TypeError):
        return False


def ensure_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        source = EXAMPLE_PATH if EXAMPLE_PATH.exists() else None
        if source:
            CONFIG_PATH.write_text(source.read_text(encoding='utf-8'), encoding='utf-8')
        else:
            CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, indent=2), encoding='utf-8')
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        data = {}
    cfg = merge_defaults(DEFAULT_CONFIG, data if isinstance(data, dict) else {})
    changed = False
    if not cfg.get('web_secret'):
        cfg['web_secret'] = secrets.token_urlsafe(32)
        changed = True
    # One-time migration: older LocalCam installs used 24/7 recording by
    # default. New installs and upgrades are manual so opening/refreshing the
    # web UI can never start a recording by itself.
    if cfg.get('version') != DEFAULT_CONFIG['version']:
        if cfg.get('record_mode') == 'continuous':
            cfg['record_mode'] = 'manual'
        cfg['version'] = DEFAULT_CONFIG['version']
        changed = True
    if changed:
        save_config(cfg)
    return cfg


def load_config() -> dict[str, Any]:
    cfg = ensure_config()
    cfg['ffmpeg_path'] = resolve_ffmpeg(cfg.get('ffmpeg_path'))
    return cfg


def save_config(config: dict[str, Any]) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(config, indent=2, ensure_ascii=False)
    tmp = CONFIG_PATH.with_suffix('.tmp')
    tmp.write_text(payload, encoding='utf-8')
    last_error = None
    for _ in range(5):
        try:
            tmp.replace(CONFIG_PATH)
            return
        except OSError as exc:
            last_error = exc
            import time
            time.sleep(0.2)
    # Windows can briefly hold the target file open (for example while Defender
    # or another reader scans it). Fall back to a direct write rather than
    # reporting a transient rename failure as a settings error.
    try:
        CONFIG_PATH.write_text(payload, encoding='utf-8')
        try:
            tmp.unlink()
        except OSError:
            pass
        return
    except OSError as exc:
        raise OSError(f'Could not save config.json after retries: {exc}') from last_error or exc
