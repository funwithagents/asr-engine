from __future__ import annotations

import asyncio
import logging

from asr_engine.audio import (
    AudioCapture,
    AudioFormat,
    AudioSource,
    FileAudioSource,
)
from asr_engine.config import ASREngineConfig
from asr_engine.modules import REGISTRY
from asr_engine.modules.base import (
    SpeechUtterance,
    UtteranceCallback,
    reconcile_audio_format,
)
from asr_engine.segmenter import (
    VALID_MODES,
    SegmentCallback,
    Segmenter,
    SpeechSegment,
)
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
        self._module_cls = REGISTRY[config.module.type]
        self._asr_module = self._module_cls(config=config.module.extra)

        # Reconcile the configured audio format against what the module supports.
        # Fails fast here (construction/startup) on an unsupported format under
        # the default "error" policy; "fallback" logs and uses module defaults.
        desired_format = AudioFormat(
            sample_rate=config.audio.sample_rate,
            channels=config.audio.channels,
            encoding=config.audio.encoding,
        )
        self._audio_format = reconcile_audio_format(
            desired_format,
            self._module_cls,
            on_unsupported=config.audio.on_unsupported_format,
        )
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

        # Segmentation state (mode + params). The always-on mode is always
        # "utterance"; dictation and listen switch it and revert (see specs).
        self._segment_mode = "utterance"
        self._listen_default_segment_mode = config.listen_default_segmentation_mode
        self._dictation_default_segment_mode = (
            config.dictation_default_segmentation_mode
        )
        self._trigger_words: list[str] = list(config.segmentation.trigger_words)
        self._initial_silence_timeout_s = config.segmentation.initial_silence_timeout_s
        self._end_of_speech_timeout_s = config.segmentation.end_of_speech_timeout_s
        self._segmenter = self._build_segmenter()

        # Dictation state, serialized with mode changes by one lock.
        self._segmentation_lock = asyncio.Lock()
        self._dictating = False
        self._dictation_end_on_final = False
        self._dictation_ending = False

    def _build_segmenter(self, mode: str | None = None) -> Segmenter:
        """Build a Segmenter for *mode* (default: the current mode).

        Raises ``ValueError`` on an unknown mode — callers rely on building the
        candidate *before* mutating state, so a bad mode leaves the engine intact.
        """
        return Segmenter(
            mode=self._segment_mode if mode is None else mode,
            trigger_words=self._trigger_words,
            initial_silence_timeout_s=self._initial_silence_timeout_s,
            end_of_speech_timeout_s=self._end_of_speech_timeout_s,
            emit=self._emit_segment,
        )

    async def _emit_segment(self, segment: SpeechSegment) -> None:
        # Read the callback dynamically so listen() can swap it at runtime.
        await self.on_speech_segment(segment)
        # Dictation auto-end: the first final segment ends an end-on-final
        # dictation. Scheduled as a separate task so the current emit (and the
        # segmenter operation that drove it) unwinds before the mode reverts.
        if (
            self._dictating
            and self._dictation_end_on_final
            and segment.is_final
            and not self._dictation_ending
        ):
            self._dictation_ending = True
            asyncio.ensure_future(self._auto_end_dictation())

    async def _set_segmentation_mode(self, mode: str) -> None:
        """Live, in-run mode swap — private; used by dictation only.

        Validates by building the candidate segmenter first, stops the old
        segmenter (so its timers can't fire after the switch), swaps, and starts
        the replacement if the engine is running.
        """
        candidate = self._build_segmenter(mode)  # raises ValueError on bad mode
        await self._segmenter.stop()
        self._segment_mode = mode
        self._segmenter = candidate
        if self._running:
            await self._segmenter.start()

    async def set_segmentation_params(
        self,
        *,
        trigger_words: list[str] | None = None,
        initial_silence_timeout_s: float | None = None,
        end_of_speech_timeout_s: float | None = None,
    ) -> None:
        """Update segmentation params (omitted params keep current values) and
        rebuild the ``Segmenter`` under the current mode, stopping the old one
        before swapping. Safe to call while running."""
        async with self._segmentation_lock:
            if trigger_words is not None:
                self._trigger_words = list(trigger_words)
            if initial_silence_timeout_s is not None:
                self._initial_silence_timeout_s = initial_silence_timeout_s
            if end_of_speech_timeout_s is not None:
                self._end_of_speech_timeout_s = end_of_speech_timeout_s
            candidate = self._build_segmenter()
            await self._segmenter.stop()
            self._segmenter = candidate
            if self._running:
                await self._segmenter.start()

    async def _start_with_segmentation_mode(self, segmentation_mode: str) -> None:
        """Start audio capture, the ASR module, and the segmenter in *mode*.

        The internal start primitive: ``start()`` passes ``"utterance"`` and
        ``listen`` passes its resolved mode, so the mode is set as part of
        starting rather than as a separate step. Raises ``ValueError`` on an
        unknown mode before any capture starts.
        """
        self._segmenter = self._build_segmenter(segmentation_mode)  # raises on bad mode
        self._segment_mode = segmentation_mode

        if self._audio_source is not None:
            self._audio_capture = self._audio_source
        elif self._audio_config.audio_file:
            self._audio_capture = FileAudioSource(
                self._audio_config.audio_file,
                audio_format=self._audio_format,
                trailing_silence_s=self._audio_config.trailing_silence_s,
            )
        else:
            loop = asyncio.get_running_loop()
            self._audio_capture = AudioCapture(
                self._audio_config.device, loop, audio_format=self._audio_format
            )
        audio_queue = self._audio_capture.start()
        await self._segmenter.start()
        self._task = asyncio.create_task(
            self._asr_module.start(
                audio_queue,
                self._handle_utterance,
                self.set_connected,
                audio_format=self._audio_format,
            )
        )
        self._running = True

    async def start(self) -> None:
        """Start the always-on pipeline in ``utterance`` mode."""
        await self._start_with_segmentation_mode("utterance")

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
        # A stopped engine is never dictating; reset the mode so the getter reads
        # "utterance" at rest (the next _start_with_segmentation_mode sets it again).
        self._dictating = False
        self._dictation_end_on_final = False
        self._dictation_ending = False
        self._segment_mode = "utterance"

    async def start_dictation(
        self,
        end_on_final_segment: bool = True,
        segmentation_mode: str | None = None,
    ) -> None:
        """Switch the always-on stream into an aggregating mode without stopping.

        Non-blocking: segments keep flowing through ``on_speech_segment``. When
        ``end_on_final_segment`` is true, the first closed segment ends the
        dictation and reverts to ``utterance``. Raises ``ValueError`` if the
        engine is not running, if already dictating, or on an unknown mode.
        """
        async with self._segmentation_lock:
            if not self._running:
                raise ValueError(
                    "ASR is not running. Start it before calling start_dictation."
                )
            if self._dictating:
                raise ValueError("Dictation is already in progress.")
            mode = (
                segmentation_mode
                if segmentation_mode is not None
                else self._dictation_default_segment_mode
            )
            await self._set_segmentation_mode(mode)  # raises on bad mode
            self._dictating = True
            self._dictation_end_on_final = end_on_final_segment

    async def stop_dictation(self) -> None:
        """End the active dictation and revert to ``utterance``; keep running.

        Raises ``ValueError`` if no dictation is in progress.
        """
        async with self._segmentation_lock:
            if not self._dictating:
                raise ValueError("No dictation in progress.")
            self._dictating = False
            self._dictation_end_on_final = False
            await self._set_segmentation_mode("utterance")

    async def _auto_end_dictation(self) -> None:
        """End an end-on-final dictation after its first final segment."""
        async with self._segmentation_lock:
            if not self._dictating:
                self._dictation_ending = False
                return
            self._dictating = False
            self._dictation_end_on_final = False
            await self._set_segmentation_mode("utterance")
        self._dictation_ending = False

    def set_dictation_default_segmentation_mode(self, mode: str) -> None:
        """Set the default mode ``start_dictation(None)`` falls back to."""
        if mode not in VALID_MODES:
            raise ValueError(
                f"Invalid segment mode '{mode}'. Must be one of {VALID_MODES}."
            )
        self._dictation_default_segment_mode = mode

    def set_listen_default_segmentation_mode(self, mode: str) -> None:
        """Set the default mode ``listen(None)`` falls back to."""
        if mode not in VALID_MODES:
            raise ValueError(
                f"Invalid segment mode '{mode}'. Must be one of {VALID_MODES}."
            )
        self._listen_default_segment_mode = mode

    @property
    def dictating(self) -> bool:
        """Whether a dictation session is currently active."""
        return self._dictating

    @property
    def audio_format(self) -> AudioFormat:
        """The reconciled audio format the capture layer and module use."""
        return self._audio_format

    @property
    def segmentation_mode(self) -> str:
        """The current segment mode (``utterance`` / ``trigger_word`` / ``timeout``).

        Reads ``utterance`` at rest and reflects the active mode while a dictation
        session or a ``listen`` is in progress (both revert to ``utterance`` when
        they end). Complements ``status()``.
        """
        return self._segment_mode

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
        when ``None``; ``"utterance"`` is allowed); the segmentation params are
        never changed. Plays the start/stop sound cues. ``on_update`` (if given)
        receives every segment update, including interim ones. Raises
        ``ValueError`` if the engine is already running or on an unknown mode.
        """
        if self._running:
            raise ValueError("ASR is already running. Stop it before calling listen.")

        if mode is None:
            mode = self._listen_default_segment_mode
        if mode not in VALID_MODES:
            raise ValueError(
                f"Invalid segment mode '{mode}'. Must be one of {VALID_MODES}."
            )

        prev_on_segment = self.on_speech_segment

        loop = asyncio.get_running_loop()
        future: asyncio.Future[SpeechSegment] = loop.create_future()

        async def _on_segment(segment: SpeechSegment) -> None:
            if on_update is not None:
                await on_update(segment)
            if segment.is_final and not future.done():
                future.set_result(segment)

        self.on_speech_segment = _on_segment
        await self._sound_feedback.play_start()
        try:
            # _start_with_segmentation_mode sets the mode as it starts — no
            # separate mode step; params stay as configured.
            await self._start_with_segmentation_mode(mode)
            return await future
        finally:
            await self.stop()  # resets the stored mode to "utterance"
            await self._sound_feedback.play_stop()
            self.on_speech_segment = prev_on_segment
