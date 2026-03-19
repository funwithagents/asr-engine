from __future__ import annotations

import asyncio
import logging

from asr_mcp.audio import AudioCapture, AudioSource
from asr_mcp.config import ASRConfig, AudioConfig
from asr_mcp.modules import REGISTRY
from asr_mcp.modules.base import ASRResult, ResultCallback

log = logging.getLogger(__name__)


class ASREngine:
    """Validates config, instantiates the ASR module, wires AudioCapture, and manages start/stop/status."""

    def __init__(
        self,
        audio_config: AudioConfig,
        asr_config: ASRConfig,
        on_result: ResultCallback,
        audio_source: AudioSource | None = None,
    ) -> None:
        if asr_config.type not in REGISTRY:
            available = ", ".join(sorted(REGISTRY)) or "(none)"
            raise ValueError(
                f"Unknown ASR type '{asr_config.type}'. Available: {available}"
            )
        self._audio_config = audio_config
        self._asr_module = REGISTRY[asr_config.type](config=asr_config.extra)
        self._on_result = on_result
        self._audio_source = audio_source
        self._running = False
        self._connected = False
        self._audio_capture: AudioSource | None = None
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start audio capture and the ASR module."""
        if self._audio_source is not None:
            self._audio_capture = self._audio_source
        else:
            loop = asyncio.get_running_loop()
            self._audio_capture = AudioCapture(self._audio_config.device, loop)
        audio_queue = self._audio_capture.start()
        self._task = asyncio.create_task(
            self._asr_module.start(audio_queue, self._handle_result, self.set_connected)
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

    def status(self) -> dict:
        """Return current engine state."""
        return {
            "running": self._running,
            "connected": self._connected,
        }

    def set_connected(self, state: bool) -> None:
        """Called by the ASR module to report connection state changes."""
        self._connected = state

    async def _handle_result(self, result: ASRResult) -> None:
        if result.is_final:
            log.info("ASR final: %s", result.transcript)
        else:
            log.info("ASR interim: %s", result.transcript)
        await self._on_result(result)
