from __future__ import annotations

import asyncio
import logging

from asr_mcp.audio import AudioCapture, AudioSource
from asr_mcp.config import ASRConfig, AudioConfig
from asr_mcp.modules import REGISTRY
from asr_mcp.modules.base import SpeechUtterance, UtteranceCallback
from asr_mcp.segmenter import SegmentCallback, Segmenter, SpeechSegment

log = logging.getLogger(__name__)

_DEFAULT_TRIGGER_WORDS: list[str] = ["submit"]


async def _noop_utterance(utterance: SpeechUtterance) -> None:
    pass


async def _noop_segment(segment: SpeechSegment) -> None:
    pass


class ASREngine:
    """Wires AudioCapture to the ASR module, owns segmentation, and manages lifecycle.

    Emits two independent streams to its consumers:

    - ``on_speech_utterance`` — one ``SpeechUtterance`` per ASR event (interim or
      final), passed through unchanged.
    - ``on_speech_segment`` — a ``SpeechSegment`` produced by the internal
      ``Segmenter`` according to the current segment mode.

    Both callbacks are public, settable, and default to a no-op. See specs/engine.md.
    """

    def __init__(
        self,
        audio_config: AudioConfig,
        asr_config: ASRConfig,
        on_speech_utterance: UtteranceCallback | None = None,
        on_speech_segment: SegmentCallback | None = None,
        audio_source: AudioSource | None = None,
    ) -> None:
        if asr_config.type not in REGISTRY:
            available = ", ".join(sorted(REGISTRY)) or "(none)"
            raise ValueError(
                f"Unknown ASR type '{asr_config.type}'. Available: {available}"
            )
        self._audio_config = audio_config
        self._asr_module = REGISTRY[asr_config.type](config=asr_config.extra)
        self.on_speech_utterance: UtteranceCallback = (
            on_speech_utterance or _noop_utterance
        )
        self.on_speech_segment: SegmentCallback = on_speech_segment or _noop_segment
        self._audio_source = audio_source
        self._running = False
        self._connected = False
        self._audio_capture: AudioSource | None = None
        self._task: asyncio.Task | None = None

        # Segmentation state (mode + params) so it can be saved/restored.
        self._segment_mode = "utterance"
        self._trigger_words: list[str] = list(_DEFAULT_TRIGGER_WORDS)
        self._initial_silence_timeout_s = 10.0
        self._end_of_speech_timeout_s = 5.0
        self._segmenter = self._build_segmenter()

    def _build_segmenter(self) -> Segmenter:
        return Segmenter(
            mode=self._segment_mode,
            trigger_words=self._trigger_words,
            initial_silence_timeout_s=self._initial_silence_timeout_s,
            end_of_speech_timeout_s=self._end_of_speech_timeout_s,
            emit=self._emit_segment,
        )

    async def _emit_segment(self, segment: SpeechSegment) -> None:
        # Read the callback dynamically so listen() can swap it at runtime.
        await self.on_speech_segment(segment)

    def set_segment_mode(
        self,
        mode: str,
        *,
        trigger_words: list[str] | None = None,
        initial_silence_timeout_s: float | None = None,
        end_of_speech_timeout_s: float | None = None,
    ) -> None:
        """Rebuild the segmenter with *mode* (omitted params keep current values)."""
        self._segment_mode = mode
        if trigger_words is not None:
            self._trigger_words = trigger_words
        if initial_silence_timeout_s is not None:
            self._initial_silence_timeout_s = initial_silence_timeout_s
        if end_of_speech_timeout_s is not None:
            self._end_of_speech_timeout_s = end_of_speech_timeout_s
        self._segmenter = self._build_segmenter()  # raises ValueError on bad mode
        if self._running:
            asyncio.ensure_future(self._segmenter.start())

    async def start(self) -> None:
        """Start audio capture, the ASR module, and the segmenter."""
        if self._audio_source is not None:
            self._audio_capture = self._audio_source
        else:
            loop = asyncio.get_running_loop()
            self._audio_capture = AudioCapture(self._audio_config.device, loop)
        audio_queue = self._audio_capture.start()
        await self._segmenter.start()
        self._task = asyncio.create_task(
            self._asr_module.start(
                audio_queue, self._handle_utterance, self.set_connected
            )
        )
        self._running = True

    async def stop(self) -> None:
        """Stop the ASR module, audio capture, and the segmenter."""
        await self._asr_module.stop()
        await self._segmenter.stop()
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

    async def _handle_utterance(self, utterance: SpeechUtterance) -> None:
        if utterance.is_final:
            log.info("ASR final: %s", utterance.transcript)
        else:
            log.info("ASR interim: %s", utterance.transcript)
        await self.on_speech_utterance(utterance)
        await self._segmenter.on_utterance(utterance)

    async def listen(
        self,
        *,
        mode: str,
        trigger_words: list[str],
        initial_silence_timeout_s: float,
        end_of_speech_timeout_s: float,
        on_update: SegmentCallback | None = None,
    ) -> SpeechSegment:
        """Single-shot capture: start, wait for the first closed segment, stop.

        Returns the closed ``SpeechSegment``. ``on_update`` (if given) receives
        every segment update, including interim ones. Raises ``ValueError`` if the
        engine is already running.
        """
        if self._running:
            raise ValueError("ASR is already running. Stop it before calling listen.")

        prev_on_segment = self.on_speech_segment
        prev_mode = self._segment_mode
        prev_trigger_words = self._trigger_words
        prev_initial = self._initial_silence_timeout_s
        prev_eos = self._end_of_speech_timeout_s

        loop = asyncio.get_running_loop()
        future: asyncio.Future[SpeechSegment] = loop.create_future()

        async def _on_segment(segment: SpeechSegment) -> None:
            if on_update is not None:
                await on_update(segment)
            if segment.is_final and not future.done():
                future.set_result(segment)

        self.on_speech_segment = _on_segment
        self.set_segment_mode(
            mode,
            trigger_words=trigger_words,
            initial_silence_timeout_s=initial_silence_timeout_s,
            end_of_speech_timeout_s=end_of_speech_timeout_s,
        )
        await self.start()
        try:
            return await future
        finally:
            await self.stop()
            self.on_speech_segment = prev_on_segment
            self.set_segment_mode(
                prev_mode,
                trigger_words=prev_trigger_words,
                initial_silence_timeout_s=prev_initial,
                end_of_speech_timeout_s=prev_eos,
            )
