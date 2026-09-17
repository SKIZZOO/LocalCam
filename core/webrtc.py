from __future__ import annotations

import asyncio
import re
import subprocess
import threading
from fractions import Fraction
from typing import Any

from core.rtsp import RTSP_AUTO_TRANSPORT, RTSP_USER_AGENT, with_credentials


QUALITY_PRESETS = {
    'low': {'width': 640, 'fps': 15},
    'medium': {'width': 1280, 'fps': 20},
    'high': {'width': 1920, 'fps': 25},
    'ultra': {'width': 2560, 'fps': 30},
}


class FFmpegVideoTrack:
    """aiortc VideoStreamTrack backed by one low-latency FFmpeg RTSP decode."""

    def __init__(self, camera: dict[str, Any], ffmpeg: str, quality: str = 'high'):
        from aiortc import VideoStreamTrack
        from av import VideoFrame

        self._base = VideoStreamTrack()
        self._VideoFrame = VideoFrame
        preset = QUALITY_PRESETS.get(str(quality).lower(), QUALITY_PRESETS['high'])
        self.width = int(preset['width'])
        self.fps = int(preset['fps'])
        self.camera = camera
        self.ffmpeg = ffmpeg
        self.process: subprocess.Popen[bytes] | None = None
        self.stdout = None
        self.header: str | None = None
        self.height = 0
        self.frame_size = 0
        self.pts = 0
        self.lock = threading.RLock()
        self._start_process()

    def _target(self) -> str:
        return with_credentials(
            str(self.camera.get('url', '')),
            str(self.camera.get('username', '')),
            str(self.camera.get('password', '')),
        )

    def _start_process(self) -> None:
        with self.lock:
            if self.process and self.process.poll() is None:
                return
            cmd = [
                self.ffmpeg,
                '-hide_banner',
                '-loglevel', 'error',
                '-rtsp_transport', RTSP_AUTO_TRANSPORT,
                '-user_agent', RTSP_USER_AGENT,
                '-allowed_media_types', 'video',
                '-fflags', 'nobuffer',
                '-flags', 'low_delay',
                '-probesize', '3000000',
                '-analyzeduration', '1000000',
                '-timeout', '15000000',
                '-i', self._target(),
                '-an',
                '-vf', f'scale={self.width}:-2:flags=lanczos,fps={self.fps}:round=near',
                '-pix_fmt', 'yuv420p',
                '-f', 'yuv4mpegpipe',
                'pipe:1',
            ]
            self.process = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                bufsize=0,
            )
            self.stdout = self.process.stdout
            self.header = None
            self.height = 0
            self.frame_size = 0
            self.pts = 0

    def _read_exact(self, size: int) -> bytes:
        data = bytearray()
        while len(data) < size:
            chunk = self.stdout.read(size - len(data)) if self.stdout else b''
            if not chunk:
                raise EOFError('RTSP video stream ended')
            data.extend(chunk)
        return bytes(data)

    def _ensure_header(self) -> None:
        if self.header is not None:
            return
        line = self.stdout.readline() if self.stdout else b''
        if not line.startswith(b'YUV4MPEG2'):
            raise RuntimeError('FFmpeg did not produce a YUV4MPEG video stream')
        self.header = line.decode('ascii', 'replace')
        width = re.search(r'\\bW(\\d+)\\b', self.header)
        height = re.search(r'\\bH(\\d+)\\b', self.header)
        if not width or not height:
            raise RuntimeError('Live video dimensions were not reported by FFmpeg')
        self.width = int(width.group(1))
        self.height = int(height.group(1))
        self.frame_size = self.width * self.height * 3 // 2

    def _read_frame(self):
        self._ensure_header()
        marker = self.stdout.readline() if self.stdout else b''
        if not marker.startswith(b'FRAME'):
            raise EOFError('RTSP video frame marker missing')
        raw = self._read_exact(self.frame_size)
        frame = self._VideoFrame(self.width, self.height, 'yuv420p')
        y = self.width * self.height
        uv = (self.width // 2) * (self.height // 2)
        frame.planes[0].update(raw[:y])
        frame.planes[1].update(raw[y:y + uv])
        frame.planes[2].update(raw[y + uv:y + uv + uv])
        frame.pts = self.pts
        frame.time_base = Fraction(1, 90000)
        self.pts += max(1, round(90000 / self.fps))
        return frame

    async def recv(self):
        try:
            return await asyncio.to_thread(self._read_frame)
        except Exception:
            self.close()
            self._start_process()
            try:
                return await asyncio.to_thread(self._read_frame)
            except Exception as exc:
                raise RuntimeError(f'Live WebRTC video failed: {exc}') from exc

    def close(self) -> None:
        with self.lock:
            proc = self.process
            self.process = None
            self.stdout = None
        if proc:
            try:
                proc.terminate()
            except OSError:
                pass
            try:
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                except OSError:
                    pass

    def stop(self) -> None:
        self.close()
        self._base.stop()


class FFmpegAudioTrack:
    """aiortc AudioStreamTrack backed by decoded 48 kHz mono PCM."""

    SAMPLES = 960
    BYTES = SAMPLES * 2

    def __init__(self, camera: dict[str, Any], ffmpeg: str):
        from aiortc import AudioStreamTrack

        self._base = AudioStreamTrack()
        self.camera = camera
        self.ffmpeg = ffmpeg
        self.process: subprocess.Popen[bytes] | None = None
        self.stdout = None
        self.pts = 0
        self.lock = threading.RLock()
        self._start_process()

    def _target(self) -> str:
        return with_credentials(
            str(self.camera.get('url', '')),
            str(self.camera.get('username', '')),
            str(self.camera.get('password', '')),
        )

    def _start_process(self) -> None:
        with self.lock:
            if self.process and self.process.poll() is None:
                return
            cmd = [
                self.ffmpeg,
                '-hide_banner',
                '-loglevel', 'error',
                '-rtsp_transport', RTSP_AUTO_TRANSPORT,
                '-user_agent', RTSP_USER_AGENT,
                '-allowed_media_types', 'audio',
                '-fflags', 'nobuffer',
                '-flags', 'low_delay',
                '-probesize', '3000000',
                '-analyzeduration', '1000000',
                '-timeout', '15000000',
                '-i', self._target(),
                '-vn',
                '-map', '0:a:0',
                '-ar', '48000',
                '-ac', '1',
                '-f', 's16le',
                'pipe:1',
            ]
            self.process = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                bufsize=0,
            )
            self.stdout = self.process.stdout
            self.pts = 0

    def _read_frame(self):
        from av import AudioFrame

        data = bytearray()
        while len(data) < self.BYTES:
            chunk = self.stdout.read(self.BYTES - len(data)) if self.stdout else b''
            if not chunk:
                raise EOFError('RTSP audio stream ended')
            data.extend(chunk)
        frame = AudioFrame(format='s16', layout='mono', samples=self.SAMPLES)
        frame.planes[0].update(bytes(data))
        frame.sample_rate = 48000
        frame.pts = self.pts
        frame.time_base = Fraction(1, 48000)
        self.pts += self.SAMPLES
        return frame

    async def recv(self):
        try:
            return await asyncio.to_thread(self._read_frame)
        except Exception:
            self.close()
            self._start_process()
            try:
                return await asyncio.to_thread(self._read_frame)
            except Exception as exc:
                raise RuntimeError(f'Live WebRTC audio failed: {exc}') from exc

    def close(self) -> None:
        with self.lock:
            proc = self.process
            self.process = None
            self.stdout = None
        if proc:
            try:
                proc.terminate()
            except OSError:
                pass
            try:
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                except OSError:
                    pass

    def stop(self) -> None:
        self.close()
        self._base.stop()


class WebRTCManager:
    def __init__(self, logger):
        self.logger = logger
        self.available = False
        self.error = ''
        self._loop = None
        self._thread = None
        self._ready = threading.Event()
        self._peers: dict[str, Any] = {}
        try:
            import aiortc  # noqa: F401
            import av  # noqa: F401
            self.available = True
        except Exception as exc:
            self.error = str(exc)
            self.logger(f'WebRTC unavailable: {exc}')
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name='localcam-webrtc')
        self._thread.start()
        self._ready.wait(3)

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._ready.set()
        self._loop.run_forever()

    async def _wait_ice(self, pc) -> None:
        for _ in range(100):
            if pc.iceGatheringState == 'complete':
                return
            await asyncio.sleep(0.05)

    async def _offer(self, peer_id: str, stream, offer_type: str, offer_sdp: str, quality: str):
        from aiortc import RTCPeerConnection, RTCSessionDescription

        pc = RTCPeerConnection()
        video = FFmpegVideoTrack(stream.camera, stream.cfg['ffmpeg_path'], quality)
        audio = None
        pc.addTrack(video)
        try:
            audio = FFmpegAudioTrack(stream.camera, stream.cfg['ffmpeg_path'])
            pc.addTrack(audio)
        except Exception as exc:
            self.logger(f'{stream.name}: WebRTC audio unavailable: {exc}')

        self._peers[peer_id] = (pc, video, audio)

        @pc.on('connectionstatechange')
        async def connectionstatechange():
            if pc.connectionState in ('failed', 'closed', 'disconnected'):
                await self._close_peer(peer_id)

        await pc.setRemoteDescription(RTCSessionDescription(sdp=offer_sdp, type=offer_type))
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)
        await self._wait_ice(pc)
        return {
            'type': pc.localDescription.type,
            'sdp': pc.localDescription.sdp,
        }

    async def _close_peer(self, peer_id: str):
        value = self._peers.pop(peer_id, None)
        if not value:
            return
        pc, video, audio = value
        video.stop()
        if audio:
            audio.stop()
        try:
            await pc.close()
        except Exception:
            pass

    def offer(self, peer_id: str, stream, offer_type: str, offer_sdp: str, quality: str):
        if not self.available or not self._loop:
            raise RuntimeError(self.error or 'WebRTC is not available.')
        future = asyncio.run_coroutine_threadsafe(
            self._offer(peer_id, stream, offer_type, offer_sdp, quality),
            self._loop,
        )
        return future.result(timeout=20)

    def close_peer(self, peer_id: str):
        if not self._loop:
            return
        future = asyncio.run_coroutine_threadsafe(self._close_peer(peer_id), self._loop)
        try:
            future.result(timeout=5)
        except Exception:
            pass

    def close(self):
        for peer_id in list(self._peers):
            self.close_peer(peer_id)
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)
