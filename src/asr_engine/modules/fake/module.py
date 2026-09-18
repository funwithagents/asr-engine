"""Scripted fake ASR module — a test double, not an ASR backend.

``FakeASRModule`` (registry key ``"fake"``) ignores the audio content and replays
a configured script of utterances: word-by-word interims spread evenly over each
utterance's ``[start_s, end_s]`` window, then the full text as a final at
``end_s``. Its clock is *audio time* (bytes consumed from the queue), so a fast
audio source replays a script faster than real time and a real-time source at
natural speed.

Use it in tests, as a fixture. For real speech recognition, install a provider
extra (e.g. ``pip install 'asr-engine[deepgram]'``) and select that module.
See specs/modules/fake.md.
"""

from __future__ import annotations

import asyncio
import logging

from asr_engine.audio import DEFAULT_AUDIO_FORMAT, AudioFormat
from asr_engine.modules.base import (
    ASRModule,
    ConnectedCallback,
    SpeechUtterance,
    UtteranceCallback,
)

log = logging.getLogger(__name__)

# Absorbs float rounding when an event lands exactly on a chunk boundary.
_CLOCK_EPSILON_S = 1e-9


def _number(entry: dict, key: str, index: int) -> float:
    value = entry.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(
            f"fake module: utterances[{index}].{key} is required and must be a number"
        )
    return float(value)


def _build_schedule(utterances: object) -> list[tuple[float, SpeechUtterance]]:
    """Validate the configured script and expand it into timed utterance events."""
    if not isinstance(utterances, list):
        raise ValueError("fake module: 'utterances' must be a list")

    schedule: list[tuple[float, SpeechUtterance]] = []
    previous_end = 0.0
    for index, entry in enumerate(utterances):
        if not isinstance(entry, dict):
            raise ValueError(f"fake module: utterances[{index}] must be an object")
        text = entry.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError(
                f"fake module: utterances[{index}].text is required and must be "
                f"a non-blank string"
            )
        start_s = _number(entry, "start_s", index)
        end_s = _number(entry, "end_s", index)
        if start_s < 0:
            raise ValueError(
                f"fake module: utterances[{index}].start_s must be >= 0, got {start_s}"
            )
        if end_s <= start_s:
            raise ValueError(
                f"fake module: utterances[{index}].end_s ({end_s}) must be greater "
                f"than start_s ({start_s})"
            )
        if start_s < previous_end:
            raise ValueError(
                f"fake module: utterances[{index}] starts at {start_s}, before the "
                f"previous utterance ends at {previous_end}; utterances must be in "
                f"order and must not overlap"
            )
        confidence = entry.get("confidence")

        words = text.split()
        step = (end_s - start_s) / len(words)
        for i in range(1, len(words)):
            interim = SpeechUtterance(" ".join(words[:i]), False, confidence)
            schedule.append((start_s + i * step, interim))
        schedule.append((end_s, SpeechUtterance(text, True, confidence)))
        previous_end = end_s
    return schedule


class FakeASRModule(ASRModule):
    """Scripted test double: replays configured utterances on an audio-time clock.

    Not an ASR backend — it never looks at the audio. See the module docstring.
    """

    SUPPORTED_SAMPLE_RATES = None
    SUPPORTED_CHANNELS = None
    SUPPORTED_ENCODINGS = None
    DEFAULT_SAMPLE_RATE = 16000
    DEFAULT_CHANNELS = 1
    DEFAULT_ENCODING = "linear16"

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self._schedule = _build_schedule(config.get("utterances", []))
        self._stop_event = asyncio.Event()

    async def start(
        self,
        audio_queue: asyncio.Queue[bytes],
        on_utterance: UtteranceCallback,
        on_connected: ConnectedCallback | None = None,
        *,
        audio_format: AudioFormat = DEFAULT_AUDIO_FORMAT,
    ) -> None:
        self._stop_event.clear()
        byte_rate = (
            audio_format.sample_rate
            * audio_format.channels
            * audio_format.bytes_per_sample
        )
        consumed = 0
        next_index = 0
        if on_connected:
            on_connected(True)
        try:
            while not self._stop_event.is_set():
                chunk = await self._next_chunk(audio_queue)
                if chunk is None:
                    break
                consumed += len(chunk)
                clock = consumed / byte_rate
                while (
                    next_index < len(self._schedule)
                    and self._schedule[next_index][0] <= clock + _CLOCK_EPSILON_S
                ):
                    await on_utterance(self._schedule[next_index][1])
                    next_index += 1
        finally:
            if on_connected:
                on_connected(False)

    async def _next_chunk(self, audio_queue: asyncio.Queue[bytes]) -> bytes | None:
        """Wait for the next chunk, or return None once stop() is called."""
        get_task = asyncio.ensure_future(audio_queue.get())
        stop_task = asyncio.ensure_future(self._stop_event.wait())
        try:
            await asyncio.wait(
                {get_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            for task in (get_task, stop_task):
                if not task.done():
                    task.cancel()
        if get_task.done() and not get_task.cancelled():
            return get_task.result()
        return None

    async def stop(self) -> None:
        self._stop_event.set()
