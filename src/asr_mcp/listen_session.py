"""ListenSession: accumulates ASR results and signals end-of-utterance."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from asr_mcp.modules.base import ASRResult
from asr_mcp.speech_utils import contains_trigger_word

log = logging.getLogger(__name__)


@dataclass
class ListenResult:
    transcript: str
    end_reason: str  # "trigger_word" | "end_of_speech_timeout" | "initial_silence_timeout"


class ListenSession:
    """Accumulates final ASR results and signals when the utterance is complete.

    Two modes:
    - ``trigger_word``: ends when a final result containing a trigger word is received.
    - ``timeout``: ends after ``end_of_speech_timeout_s`` of silence following the first
      event, or ``initial_silence_timeout_s`` if no event arrives at all.
    """

    def __init__(
        self,
        mode: str,
        trigger_words: list[str],
        initial_silence_timeout_s: float,
        end_of_speech_timeout_s: float,
        on_final_committed: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self._mode = mode
        self._trigger_words = trigger_words
        self._initial_silence_timeout_s = initial_silence_timeout_s
        self._end_of_speech_timeout_s = end_of_speech_timeout_s
        self._on_final_committed = on_final_committed
        self._committed: list[str] = []
        self._done: asyncio.Event = asyncio.Event()
        self._result: ListenResult | None = None
        self._received_any = False
        self._eos_timer_task: asyncio.Task | None = None
        self._initial_silence_task: asyncio.Task | None = None

    async def on_result(self, result: ASRResult) -> None:
        """Feed an ASR result into the session."""
        self._received_any = True

        # Reset end-of-speech timer on every event (both modes)
        if self._eos_timer_task is not None:
            self._eos_timer_task.cancel()
        self._eos_timer_task = asyncio.get_event_loop().create_task(
            self._eos_timer()
        )

        if not result.is_final:
            return  # Interim: reset timer only

        if self._mode == "trigger_word" and contains_trigger_word(result.transcript, self._trigger_words):
            self._result = ListenResult(
                transcript=" ".join(self._committed),
                end_reason="trigger_word",
            )
            self._done.set()
        else:
            self._committed.append(result.transcript)
            if self._on_final_committed is not None:
                await self._on_final_committed(" ".join(self._committed))

    async def wait(self) -> ListenResult:
        """Wait until the session ends and return the accumulated result."""
        if self._mode == "timeout":
            self._initial_silence_task = asyncio.get_event_loop().create_task(
                self._initial_silence_timer()
            )
            await self._done.wait()
        else:
            # trigger_word mode: just wait for done
            await self._done.wait()

        # Cancel any running timers
        if self._eos_timer_task is not None:
            self._eos_timer_task.cancel()
        if self._initial_silence_task is not None:
            self._initial_silence_task.cancel()

        assert self._result is not None
        self._result.transcript = " ".join(self._committed)
        log.info("Listen session ended: end_reason=%s transcript=%r", self._result.end_reason, self._result.transcript)
        return self._result

    async def _initial_silence_timer(self) -> None:
        """Fire if no ASR event arrives within initial_silence_timeout_s."""
        try:
            await asyncio.sleep(self._initial_silence_timeout_s)
            if not self._received_any and not self._done.is_set():
                self._result = ListenResult(transcript="", end_reason="initial_silence_timeout")
                self._done.set()
        except asyncio.CancelledError:
            pass

    async def _eos_timer(self) -> None:
        """Fire after end_of_speech_timeout_s of silence."""
        try:
            await asyncio.sleep(self._end_of_speech_timeout_s)
            if not self._done.is_set():
                self._result = ListenResult(
                    transcript=" ".join(self._committed),
                    end_reason="end_of_speech_timeout",
                )
                self._done.set()
        except asyncio.CancelledError:
            pass
