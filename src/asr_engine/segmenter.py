"""Segmenter: aggregates SpeechUtterances into SpeechSegments per segment mode.

Owned by ``ASREngine``. Replaces the former client-side ``EndOfUtteranceDetector``:
instead of a one-shot ``wait()``, it emits successive segments continuously via
an ``emit`` callback, so the always-on engine and the ``listen`` primitive share
the same logic. See specs/engine.md.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from asr_engine.modules.base import SpeechUtterance
from asr_engine.speech_utils import contains_trigger_word

log = logging.getLogger(__name__)

VALID_MODES = ("utterance", "trigger_word", "timeout")


@dataclass
class SpeechSegment:
    transcript: str
    is_final: bool
    end_reason: str | None  # None while open; else utterance/trigger_word/*_timeout
    utterances: list[SpeechUtterance] = field(default_factory=list)


SegmentCallback = Callable[[SpeechSegment], Awaitable[None]]


class Segmenter:
    """Aggregates utterances into segments according to *mode*.

    - ``utterance``: each final utterance is its own segment.
    - ``trigger_word``: accumulate finals until one contains a trigger word.
    - ``timeout``: accumulate finals; close after silence (two timers).

    In every mode an interim (open) segment is emitted as the segment grows:
    ``transcript`` is the committed finals joined by a space, followed by the
    current interim utterance. A closed segment carries ``is_final=True`` and the
    ``end_reason``, after which the segmenter resets and a fresh segment begins.
    """

    def __init__(
        self,
        mode: str,
        trigger_words: list[str],
        initial_silence_timeout_s: float,
        end_of_speech_timeout_s: float,
        emit: SegmentCallback,
    ) -> None:
        if mode not in VALID_MODES:
            raise ValueError(
                f"Invalid segment mode '{mode}'. Must be one of {VALID_MODES}."
            )
        self._mode = mode
        self._trigger_words = trigger_words
        self._initial_silence_timeout_s = initial_silence_timeout_s
        self._end_of_speech_timeout_s = end_of_speech_timeout_s
        self._emit = emit
        self._committed: list[SpeechUtterance] = []
        self._received_any_in_segment = False
        self._started = False
        self._eos_timer_task: asyncio.Task | None = None
        self._initial_silence_task: asyncio.Task | None = None

    # --- lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        """Begin the first segment (starts the initial-silence timer in timeout mode)."""
        self._started = True
        if self._mode == "timeout":
            self._start_initial_silence_timer()

    async def stop(self) -> None:
        """Cancel any running timers."""
        self._started = False
        self._cancel_eos_timer()
        self._cancel_initial_silence_timer()

    # --- feeding -------------------------------------------------------------

    async def on_utterance(self, utterance: SpeechUtterance) -> None:
        """Feed one utterance; may emit an interim and/or a final segment."""
        if self._mode == "timeout":
            self._cancel_initial_silence_timer()
            self._reset_eos_timer()
        self._received_any_in_segment = True

        if self._mode == "utterance":
            await self._on_utterance_mode(utterance)
        elif self._mode == "trigger_word":
            await self._on_trigger_word_mode(utterance)
        else:
            await self._on_timeout_mode(utterance)

    async def _on_utterance_mode(self, utterance: SpeechUtterance) -> None:
        if utterance.is_final:
            await self._emit(
                SpeechSegment(
                    transcript=utterance.transcript,
                    is_final=True,
                    end_reason="utterance",
                    utterances=[utterance],
                )
            )
            # Nothing committed to reset in this mode.
        else:
            await self._emit_open(utterance.transcript)

    async def _on_trigger_word_mode(self, utterance: SpeechUtterance) -> None:
        if not utterance.is_final:
            await self._emit_open(utterance.transcript)
            return
        if contains_trigger_word(utterance.transcript, self._trigger_words):
            await self._close_segment("trigger_word")
        else:
            self._committed.append(utterance)
            await self._emit_open(None)

    async def _on_timeout_mode(self, utterance: SpeechUtterance) -> None:
        if not utterance.is_final:
            await self._emit_open(utterance.transcript)
            return
        self._committed.append(utterance)
        await self._emit_open(None)

    # --- emission helpers ----------------------------------------------------

    def _committed_transcript(self) -> str:
        return " ".join(u.transcript for u in self._committed)

    def _open_transcript(self, current_interim: str | None) -> str:
        pieces = [u.transcript for u in self._committed]
        if current_interim:
            pieces.append(current_interim)
        return " ".join(pieces)

    async def _emit_open(self, current_interim: str | None) -> None:
        await self._emit(
            SpeechSegment(
                transcript=self._open_transcript(current_interim),
                is_final=False,
                end_reason=None,
                utterances=list(self._committed),
            )
        )

    async def _close_segment(self, end_reason: str) -> None:
        segment = SpeechSegment(
            transcript=self._committed_transcript(),
            is_final=True,
            end_reason=end_reason,
            utterances=list(self._committed),
        )
        # Emit before resetting so no timer (possibly this very task) is cancelled
        # mid-await; the reset then clears state for the next segment.
        await self._emit(segment)
        self._reset_segment()
        if self._mode == "timeout" and self._started:
            self._start_initial_silence_timer()

    def _reset_segment(self) -> None:
        self._committed = []
        self._received_any_in_segment = False
        self._cancel_eos_timer()

    # --- timers (timeout mode) ----------------------------------------------

    def _start_initial_silence_timer(self) -> None:
        self._cancel_initial_silence_timer()
        self._initial_silence_task = asyncio.get_event_loop().create_task(
            self._initial_silence_timer()
        )

    def _cancel_initial_silence_timer(self) -> None:
        if self._initial_silence_task is not None:
            self._initial_silence_task.cancel()
            self._initial_silence_task = None

    def _reset_eos_timer(self) -> None:
        self._cancel_eos_timer()
        self._eos_timer_task = asyncio.get_event_loop().create_task(self._eos_timer())

    def _cancel_eos_timer(self) -> None:
        if self._eos_timer_task is not None:
            self._eos_timer_task.cancel()
            self._eos_timer_task = None

    async def _initial_silence_timer(self) -> None:
        try:
            await asyncio.sleep(self._initial_silence_timeout_s)
            if not self._received_any_in_segment:
                await self._close_segment("initial_silence_timeout")
        except asyncio.CancelledError:
            pass

    async def _eos_timer(self) -> None:
        try:
            await asyncio.sleep(self._end_of_speech_timeout_s)
            await self._close_segment("end_of_speech_timeout")
        except asyncio.CancelledError:
            pass
