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
