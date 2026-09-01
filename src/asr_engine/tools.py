from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from asr_engine.engine import ASREngine
from asr_engine.segmenter import SpeechSegment

log = logging.getLogger(__name__)

# progress, total, message
ProgressCallback = Callable[[float, float | None, str | None], Awaitable[None]]


class AsrTools:
    """Transport-agnostic ASR control operations over an ``ASREngine``.

    Defines ``start`` / ``stop`` / ``is_running`` / ``listen`` once, independent
    of any transport, so they can be called directly or wrapped by the MCP
    server. Knows nothing about FastMCP, ``Context``, HTTP, or resources. See
    specs/tools.md.
    """

    def __init__(self, engine: ASREngine) -> None:
        self._engine = engine
        self._listen_lock = asyncio.Lock()

    async def start(self) -> dict:
        """Start audio capture and ASR streaming."""
        await self._engine.start()
        return {"status": "running"}

    async def stop(self) -> dict:
        """Stop audio capture and ASR streaming."""
        await self._engine.stop()
        return {"status": "stopped"}

    def is_running(self) -> dict:
        """Return the current ASR engine status."""
        return self._engine.status()

    async def listen(
        self,
        mode: str | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> dict:
        """Single-shot capture returning ``{"transcript", "end_reason"}``.

        ``mode`` is passed straight to ``engine.listen`` (``None`` uses the
        engine's ``listen_default_segmentation_mode``); segmentation params are
        never touched. ``on_progress`` is called once per newly committed final.
        """
        if self._listen_lock.locked():
            raise ValueError("A listen session is already in progress.")
        if self._engine.status()["running"]:
            raise ValueError("ASR is already running. Stop it before calling listen.")

        async with self._listen_lock:
            reported = 0

            async def _relay(segment: SpeechSegment) -> None:
                # Report progress only when a new final is committed.
                nonlocal reported
                committed = len(segment.utterances)
                if committed > reported and on_progress is not None:
                    reported = committed
                    await on_progress(committed, None, segment.transcript)

            segment = await self._engine.listen(mode, on_update=_relay)
            return {"transcript": segment.transcript, "end_reason": segment.end_reason}

    async def start_dictation(
        self,
        end_on_final_segment: bool = True,
        segmentation_mode: str | None = None,
    ) -> dict:
        """Start a non-blocking dictation session on the running engine.

        Pass-through to ``engine.start_dictation`` (which owns the state and the
        ``ValueError``s). The aggregated segments arrive via ``on_speech_segment``
        / the ``asr://segment`` resource, not this return value.
        """
        await self._engine.start_dictation(
            end_on_final_segment=end_on_final_segment,
            segmentation_mode=segmentation_mode,
        )
        return {
            "status": "dictating",
            "mode": self._engine.segmentation_mode,
            "end_on_final_segment": end_on_final_segment,
        }

    async def stop_dictation(self) -> dict:
        """End the active dictation; the engine keeps running."""
        await self._engine.stop_dictation()
        return {"status": "dictation_stopped"}

    def is_dictation_running(self) -> dict:
        """Whether a dictation is active, plus the current segmentation mode."""
        return {
            "dictating": self._engine.dictating,
            "segmentation_mode": self._engine.segmentation_mode,
        }

    def set_dictation_default_segmentation_mode(self, mode: str) -> dict:
        """Set the default mode ``start_dictation(None)`` falls back to."""
        self._engine.set_dictation_default_segmentation_mode(mode)
        return {"dictation_default_segmentation_mode": mode}

    def set_listen_default_segmentation_mode(self, mode: str) -> dict:
        """Set the default mode ``listen(None)`` falls back to."""
        self._engine.set_listen_default_segmentation_mode(mode)
        return {"listen_default_segmentation_mode": mode}
