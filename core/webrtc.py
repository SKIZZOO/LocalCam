from __future__ import annotations

import asyncio
import re
import subprocess
import threading
from fractions import Fraction
from typing import Any

from core.rtsp import RTSP_AUTO_TRANSPORT, RTSP_USER_AGENT, with_credentials

try:
    from aiortc import AudioStreamTrack, VideoStreamTrack
except Exception:  # Optional until dependency installation completes.
    class VideoStreamTrack:  # type: ignore[no-redef]
        def stop(self):
            pass

    class AudioStreamTrack:  # type: ignore[no-redef]
        def stop(self):
            pass


QUALITY_PRESETS = {
    'low': {'width': 640, 'fps': 15},
    'medium': {'width': 1280, 'fps': 20},
    'high': {'width': 1920, 'fps': 25},
    'ultra': {'width': 2560, 'fps': 30},
}


class FFmpegVideoTrack(VideoStreamTrack):
    """aiortc VideoStreamTrack backed by a low-latency FFmpeg RTSP decode."""

    def __init__(self, camera: dict[str, Any], ffmpeg: str, quality: str = 'high'):
        from av import VideoFrame

        super().__init__()
        self._VideoFrame = VideoFrame
        preset = QUALITY_PRESETS.get(str(quality).lower(), QUALITY_PRESETS['high'])
        self.width = int(preset['width'])
        self.fps = int(preset['fps'])
        self.camera = camera
        self.ffmpeg = ffmpeg
        self.process: subprocess.Popen[bytes] | None = None
        self.stdout = None
        self.frame_size = self.width * ((self.width * 9 + 15) // 16) * 3 // 2
        self.height = 0
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
                '-loglevel', 'warning',
                '-rtsp_transport', RTSP_AUTO_TRANSPORT,
                '-user_agent', RTSP_USER_AGENT,
                '-timeout', '15000000',
                '-fflags', 'nobuffer',
                '-flags', 'low_delay',
                '-probesize', '3000000',
                '-analyzeduration', '1000000',
                '-i', self._target(),
                '-map', '0:v:0',
                '-an',
                '-vf', f'scale={self.width}:-2:flags=lanczos,fps={self.fps}:round=near',
                '-pix_fmt', 'yuv420p',
                '-f', 'rawvideo',
                'pipe:1',
            ]
            self.process = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                bufsize=0,
            )
            self.stdout = self.process.stdout
            self.frame_size = 0
            self.height = 0
            self.pts = 0
            threading.Thread(target=self._log_errors, args=(self.process,), daemon=True, name='localcam-webrtc-video-log').start()

    def _log_errors(self, proc):
        if not proc.stderr:
            return
        for line in proc.stderr:
            line = line.decode('utf-8', 'replace').strip()
            if line:
                self.camera and None
                # The caller's logger is intentionally not available here; FFmpeg
                # stderr is consumed so a dead/blocked stderr pipe cannot stall output.
        try:
            proc.stderr.close()
        except Exception:
            pass

    def _read_exact(self, size: int) -> bytes:
        data = bytearray()
        while len(data) < size:
            chunk = self.stdout.read(size - len(data)) if self.stdout else b''
            if not chunk:
                raise EOFError('RTSP video stream ended')
            data.extend(chunk)
        return bytes(data)

    def _read_frame(self):
        if not self.frame_size:
            # Height is even and matches FFmpeg's yuv420p output. Because scale=-2
            # rounds the height to the input aspect ratio, infer it from the first
            # frame is not possible from rawvideo; use 16:9, which matches the
            # camera stream family used by the project.
            self.height = max(2, (self.width * 9 // 16) // 2 * 2)
            self.frame_size = self.width * self.height * 3 // 2
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
        except Exception as exc:
            self.close()
            self._start_process()
            try:
                return await asyncio.to_thread(self._read_frame)
            except Exception as retry_exc:
                raise RuntimeError(f'Live WebRTC video failed: {retry_exc}') from retry_exc

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
        super().stop()


class FFmpegAudioTrack(AudioStreamTrack):
    """aiortc AudioStreamTrack backed by decoded 48 kHz mono PCM."""

    SAMPLES = 960
    BYTES = SAMPLES * 2

    def __init__(self, camera: dict[str, Any], ffmpeg: str):
        super().__init__()
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
        super().stop()


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
        from aiortc import RTCPeerConnection, RTCRtpSender, RTCSessionDescription

        if peer_id in self._peers:
            await self._close_peer(peer_id)

        pc = RTCPeerConnection()
        video = FFmpegVideoTrack(stream.camera, stream.cfg['ffmpeg_path'], quality)
        audio = None
        video_transceiver = pc.addTransceiver('video', direction='sendonly')
        video_codecs = [
            codec for codec in RTCRtpSender.getCapabilities('video').codecs
            if codec.mimeType.lower() in ('video/h264', 'video/rtx')
        ]
        if video_codecs:
            video_transceiver.setCodecPreferences(video_codecs)
        video_transceiver.sender.replaceTrack(video)
        try:
            audio = FFmpegAudioTrack(stream.camera, stream.cfg['ffmpeg_path'])
            audio_transceiver = pc.addTransceiver('audio', direction='sendonly')
            audio_codecs = [
                codec for codec in RTCRtpSender.getCapabilities('audio').codecs
                if codec.mimeType.lower() == 'audio/opus'
            ]
            if audio_codecs:
                audio_transceiver.setCodecPreferences(audio_codecs)
            audio_transceiver.sender.replaceTrack(audio)
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
