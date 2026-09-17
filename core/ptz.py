from __future__ import annotations

import threading
import time
from typing import Any


class PTZController:
    """Optional ONVIF PTZ controller. The dependency is imported lazily."""

    def __init__(self, logger=None) -> None:
        self.logger = logger or (lambda message: None)
        self.lock = threading.RLock()
        self._cache: dict[str, tuple[Any, Any]] = {}

    def _load(self):
        try:
            from onvif import ONVIFCamera  # type: ignore
            return ONVIFCamera
        except Exception as exc:
            raise RuntimeError('Optional ONVIF support is not installed. Run install.bat again.') from exc

    def _connect(self, camera_id: str, cfg: dict[str, Any]):
        settings = cfg.get('ptz') or {}
        if not settings.get('enabled'):
            raise RuntimeError('PTZ is not enabled for this camera.')
        host = str(settings.get('host') or '').strip()
        port = int(settings.get('port') or 80)
        username = str(settings.get('username') or cfg.get('username') or '')
        password = str(settings.get('password') or cfg.get('password') or '')
        if not host:
            raise RuntimeError('ONVIF host is required.')
        key = f'{host}:{port}:{username}:{camera_id}'
        with self.lock:
            cached = self._cache.get(key)
            if cached:
                return cached
            ONVIFCamera = self._load()
            cam = ONVIFCamera(host, port, username, password, no_cache=True)
            media = cam.create_media_service()
            profiles = media.GetProfiles()
            if not profiles:
                raise RuntimeError('ONVIF returned no media profiles.')
            ptz = cam.create_ptz_service()
            self._cache[key] = (ptz, profiles[0])
            return ptz, profiles[0]

    def test(self, camera_id: str, cfg: dict[str, Any]) -> dict[str, Any]:
        ptz, profile = self._connect(camera_id, cfg)
        return {'ok': True, 'profile_token': getattr(profile, 'token', '')}

    def move(self, camera_id: str, cfg: dict[str, Any], pan: float = 0.0, tilt: float = 0.0, zoom: float = 0.0, seconds: float = 0.5) -> None:
        ptz, profile = self._connect(camera_id, cfg)
        request = ptz.create_type('ContinuousMove')
        request.ProfileToken = profile.token
        request.Velocity = {'PanTilt': {'x': max(-1.0, min(1.0, float(pan))), 'y': max(-1.0, min(1.0, float(tilt)))}, 'Zoom': {'x': max(-1.0, min(1.0, float(zoom)))}}
        ptz.ContinuousMove(request)
        if seconds > 0:
            time.sleep(min(5.0, max(0.05, float(seconds))))
            self.stop(camera_id, cfg)

    def stop(self, camera_id: str, cfg: dict[str, Any]) -> None:
        ptz, profile = self._connect(camera_id, cfg)
        request = ptz.create_type('Stop')
        request.ProfileToken = profile.token
        request.PanTilt = True
        request.Zoom = True
        ptz.Stop(request)

    def home(self, camera_id: str, cfg: dict[str, Any]) -> None:
        ptz, profile = self._connect(camera_id, cfg)
        request = ptz.create_type('GotoHomePosition')
        request.ProfileToken = profile.token
        ptz.GotoHomePosition(request)
