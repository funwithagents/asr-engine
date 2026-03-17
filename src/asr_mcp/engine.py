from __future__ import annotations

import asyncio

from asr_mcp.audio import AudioCapture
from asr_mcp.config import ASRConfig, AudioConfig
from asr_mcp.modules import REGISTRY
from asr_mcp.modules.base import ASRResult, ResultCallback


class ASREngine:
    """Validates config, instantiates the ASR module, wires AudioCapture, and manages pause/resume/status."""

    def __init__(
        self,
        audio_config: AudioConfig,
        asr_config: ASRConfig,
        on_result: ResultCallback,
    ) -> None:
        if asr_config.type not in REGISTRY:
            available = ", ".join(sorted(REGISTRY)) or "(none)"
            raise ValueError(
                f"Unknown ASR type '{asr_config.type}'. Available: {available}"
            )
        self._audio_config = audio_config
        self._asr_module = REGISTRY[asr_config.type](config=asr_config.extra)
        self._on_result = on_result
        self._paused = False
        self._running = False
        self._connected = False
        self._audio_capture: AudioCapture | None = None
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start audio capture and the ASR module."""
        loop = asyncio.get_running_loop()
        self._audio_capture = AudioCapture(self._audio_config.device, loop)
        audio_queue = self._audio_capture.start()
        self._task = asyncio.create_task(
            self._asr_module.start(audio_queue, self._handle_result)
        )
        self._running = True

    async def stop(self) -> None:
        """Stop the ASR module and audio capture."""
        await self._asr_module.stop()
        if self._audio_capture is not None:
            self._audio_capture.stop()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._running = False

    def pause(self) -> None:
        """Pause audio capture (stop feeding the queue)."""
        if self._paused:
            raise RuntimeError("already paused")
        if self._audio_capture is not None:
            self._audio_capture.pause()
        self._paused = True

    def resume(self) -> None:
        """Resume audio capture after a pause."""
        if not self._paused:
            raise RuntimeError("not paused")
        if self._audio_capture is not None:
            self._audio_capture.resume()
        self._paused = False

    def status(self) -> dict:
        """Return current engine state."""
        return {
            "running": self._running,
            "paused": self._paused,
            "connected": self._connected,
        }

    def set_connected(self, state: bool) -> None:
        """Called by the ASR module to report connection state changes."""
        self._connected = state

    async def _handle_result(self, result: ASRResult) -> None:
        if self._paused:
            return
        await self._on_result(result)
