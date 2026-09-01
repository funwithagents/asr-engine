from __future__ import annotations

import asyncio
import logging

from asr_engine.audio import AudioCapture, AudioSource
from asr_engine.config import ASREngineConfig
from asr_engine.modules import REGISTRY
from asr_engine.modules.base import SpeechUtterance, UtteranceCallback
from asr_engine.segmenter import SegmentCallback, Segmenter, SpeechSegment
from asr_engine.sound_feedback import NoOpSoundFeedback, SoundFeedback

log = logging.getLogger(__name__)


async def _noop_utterance(utterance: SpeechUtterance) -> None:
    pass


async def _noop_segment(segment: SpeechSegment) -> None:
    pass


class ASREngine:
    """Wires AudioCapture to the ASR module, owns segmentation, sound feedback,
    and lifecycle. Constructed from a single ``ASREngineConfig`` so it is usable
    directly (``import asr_engine``) without the MCP server.

    Emits two independent streams to its consumers:

    - ``on_speech_utterance`` — one ``SpeechUtterance`` per ASR event (interim or
      final), passed through unchanged.
    - ``on_speech_segment`` — a ``SpeechSegment`` produced by the internal
      ``Segmenter`` according to the current segment mode.

    Both callbacks are public, settable, and default to a no-op. See specs/engine.md.
    """

    def __init__(
        self,
        config: ASREngineConfig,
        *,
        on_speech_utterance: UtteranceCallback | None = None,
        on_speech_segment: SegmentCallback | None = None,
        audio_source: AudioSource | None = None,
    ) -> None:
        if config.module.type not in REGISTRY:
            available = ", ".join(sorted(REGISTRY)) or "(none)"
            raise ValueError(
                f"Unknown ASR type '{config.module.type}'. Available: {available}"
            )

        self._config = config
        self._audio_config = config.audio
        self._asr_module = REGISTRY[config.module.type](config=config.module.extra)
        self.on_speech_utterance: UtteranceCallback = (
            on_speech_utterance or _noop_utterance
        )
        self.on_speech_segment: SegmentCallback = on_speech_segment or _noop_segment
        self._audio_source = audio_source
        self._running = False
        self._connected = False
        self._audio_capture: AudioSource | None = None
        self._task: asyncio.Task | None = None

        if config.sound_feedback.enabled:
            self._sound_feedback: SoundFeedback | NoOpSoundFeedback = SoundFeedback(
                output_device=config.sound_feedback.output_device
            )
        else:
            self._sound_feedback = NoOpSoundFeedback()

        # Segmentation state (mode + params) so it can be saved/restored.
        self._segment_mode = config.segmentation_mode
        self._listen_default_segment_mode = config.listen_default_segmentation_mode
        self._trigger_words: list[str] = list(config.segmentation.trigger_words)
        self._initial_silence_timeout_s = config.segmentation.initial_silence_timeout_s
        self._end_of_speech_timeout_s = config.segmentation.end_of_speech_timeout_s
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

    def set_segmentation_mode(self, mode: str) -> None:
        """Switch the segment *mode* only, keeping the current segmentation params.

        Rebuilds the internal ``Segmenter``, discarding any in-progress segment.
        Raises ``ValueError`` on an unknown mode. Safe to call while running.
        """
        self._segment_mode = mode
        self._segmenter = self._build_segmenter()  # raises ValueError on bad mode
        if self._running:
            asyncio.ensure_future(self._segmenter.start())

    def set_segmentation_params(
        self,
        *,
        trigger_words: list[str] | None = None,
        initial_silence_timeout_s: float | None = None,
        end_of_speech_timeout_s: float | None = None,
    ) -> None:
        """Update segmentation params (omitted params keep current values) and
        rebuild the ``Segmenter`` under the current mode."""
        if trigger_words is not None:
            self._trigger_words = trigger_words
        if initial_silence_timeout_s is not None:
            self._initial_silence_timeout_s = initial_silence_timeout_s
        if end_of_speech_timeout_s is not None:
            self._end_of_speech_timeout_s = end_of_speech_timeout_s
        self._segmenter = self._build_segmenter()
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
        mode: str | None = None,
        *,
        on_update: SegmentCallback | None = None,
    ) -> SpeechSegment:
        """Single-shot capture: start, wait for the first closed segment, stop.

        Takes only a *mode* (falling back to ``listen_default_segmentation_mode``
        when ``None``); the segmentation params are never changed. Plays the
        start/stop sound cues. ``on_update`` (if given) receives every segment
        update, including interim ones. Raises ``ValueError`` if the engine is
        already running.
        """
        if self._running:
            raise ValueError("ASR is already running. Stop it before calling listen.")

        if mode is None:
            mode = self._listen_default_segment_mode

        prev_on_segment = self.on_speech_segment
        prev_mode = self._segment_mode

        loop = asyncio.get_running_loop()
        future: asyncio.Future[SpeechSegment] = loop.create_future()

        async def _on_segment(segment: SpeechSegment) -> None:
            if on_update is not None:
                await on_update(segment)
            if segment.is_final and not future.done():
                future.set_result(segment)

        self.on_speech_segment = _on_segment
        self.set_segmentation_mode(mode)  # mode only — params untouched
        await self._sound_feedback.play_start()
        try:
            await self.start()
            return await future
        finally:
            await self.stop()
            await self._sound_feedback.play_stop()
            self.on_speech_segment = prev_on_segment
            self.set_segmentation_mode(prev_mode)
