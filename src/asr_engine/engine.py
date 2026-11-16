from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, cast

from asr_engine.audio import (
    AudioCapture,
    AudioFormat,
    AudioSource,
    FileAudioSource,
    read_file_format,
)
from asr_engine.config import (
    AUDIO_SOURCE_TYPES,
    ASREngineConfig,
    AudioFormatConfig,
)
from asr_engine.format_converter import ConvertedAudioSource, check_convertible
from asr_engine.modules import resolve_module_class
from asr_engine.modules.base import (
    AudioQueue,
    SpeechUtterance,
    UtteranceCallback,
    resolve_module_audio_format,
)
from asr_engine.segmenter import (
    VALID_MODES,
    SegmentCallback,
    Segmenter,
    SpeechSegment,
)
from asr_engine.sound_feedback import NoOpSoundFeedback, SoundFeedback

if TYPE_CHECKING:
    from asr_engine.vad.base import VADModule
    from asr_engine.vad.gate import VadGate, VadState

log = logging.getLogger(__name__)


def _describe(fmt: AudioFormat) -> str:
    return f"{fmt.sample_rate} Hz, {fmt.channels} channel(s), {fmt.encoding}"


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
        self._config = config
        self._audio_config = config.audio_source
        # Raises ValueError (unknown type) or ImportError (provider extra missing).
        self._module_cls = resolve_module_class(config.module.type)
        self._asr_module = self._module_cls(config=config.module.extra)

        self._audio_source = audio_source
        # Two formats (specs/format-conversion.md): what the source produces
        # (audio_format) and what the module is fed (module_audio_format). Where
        # they differ, start() puts a converter between the two.
        self._audio_format, self._module_audio_format = self._resolve_formats()
        self._vad_module, self._vad_loaded = self._build_vad(), False
        self._vad_gate: VadGate | None = None  # the last run's gate, for vad_state

        self.on_speech_utterance: UtteranceCallback = (
            on_speech_utterance or _noop_utterance
        )
        self.on_speech_segment: SegmentCallback = on_speech_segment or _noop_segment
        self._running = False
        self._connected = False
        # The running source, under whatever wraps it (the format converter, the
        # VAD gate): stopping the outermost one stops the source.
        self._audio_capture: AudioSource | VadGate | None = None
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

    def _resolve_formats(self) -> tuple[AudioFormat, AudioFormat]:
        """Resolve the source's format and the module's (specs/format-conversion.md).

        The module's format comes first and never depends on the source: the
        module's defaults, unless ``engine.module.audio_format`` sets a field.

        The source's format depends on ``engine.audio_source.type``:

        - ``microphone``: ``engine.audio_source.audio_format``, each unset field
          following the module's format.
        - ``file``: the file's own rate and channel count, in the module's encoding.
        - ``custom``: what the injected source declares (its ``audio_format``
          attribute), else the module's format.

        Raises ``ValueError`` on a source type that does not match how the engine
        was built, a module field the module does not support, or a channel layout
        that cannot be converted.
        """
        requested = self._config.module.audio_format
        module_format = resolve_module_audio_format(
            self._asr_module,
            sample_rate=requested.sample_rate,
            channels=requested.channels,
            encoding=requested.encoding,
        )

        source = self._config.audio_source
        injected = self._audio_source
        if source.type not in AUDIO_SOURCE_TYPES:
            raise ValueError(
                f"engine.audio_source.type is required: one of "
                f"{', '.join(AUDIO_SOURCE_TYPES)} (got {source.type!r})."
            )
        if source.type == "custom" and injected is None:
            raise ValueError(
                "engine.audio_source.type is 'custom', but no source was given: "
                "pass one as ASREngine(config, audio_source=...). The MCP server "
                "cannot use a custom source; set the type to 'microphone' or 'file'."
            )
        if source.type != "custom" and injected is not None:
            raise ValueError(
                f"An audio source was passed to ASREngine, but "
                f"engine.audio_source.type is '{source.type}': set it to 'custom'."
            )
        if source.type != "microphone" and source.audio_format != AudioFormatConfig():
            raise ValueError(
                f"engine.audio_source.audio_format applies to type 'microphone' "
                f"only, not '{source.type}': a file has its own format, and a "
                f"custom source declares its own."
            )

        if source.type == "microphone":
            wanted = source.audio_format
            source_format = AudioFormat(
                sample_rate=wanted.sample_rate or module_format.sample_rate,
                channels=wanted.channels or module_format.channels,
                encoding=wanted.encoding or module_format.encoding,
            )
        elif source.type == "file":
            if not source.audio_file:
                raise ValueError(
                    "engine.audio_source.audio_file is required with type 'file'."
                )
            # A file's rate and channel count are facts, read from its header.
            rate, channels = read_file_format(source.audio_file)
            source_format = AudioFormat(
                sample_rate=rate, channels=channels, encoding=module_format.encoding
            )
        else:
            declared = getattr(injected, "audio_format", None)
            if declared is not None and not isinstance(declared, AudioFormat):
                raise TypeError(
                    f"{type(injected).__name__}.audio_format must be an "
                    f"AudioFormat (or absent), got {type(declared).__name__}."
                )
            source_format = declared if declared is not None else module_format
        check_convertible(source_format, module_format)
        return source_format, module_format

    def _build_vad(self) -> VADModule | None:
        """Resolve and check the optional VAD stage (specs/vad.md).

        Builds the VAD module (config validation only — the model is loaded on the
        first start) and applies the construction-time checks. With no
        ``engine.vad`` nothing VAD-related is imported.
        """
        vad_config = self._config.vad
        gating = self._module_cls.VAD_GATING
        module_name = self._config.module.type
        if vad_config is None:
            if gating == "required":
                raise ValueError(
                    f"ASR module '{module_name}' needs a VAD to know when an "
                    f"utterance ends: add an engine.vad block "
                    f'(e.g. "vad": {{"type": "silero"}}).'
                )
            return None
        if gating == "unsupported":
            raise ValueError(
                f"ASR module '{module_name}' does not support VAD gating "
                f"(it does not handle the end-of-speech marker): remove the "
                f"engine.vad block or use another module."
            )

        from asr_engine.vad import resolve_vad_class

        # Raises ValueError (unknown type) or ImportError (extra missing).
        vad_cls = resolve_vad_class(vad_config.type)
        return vad_cls(config=vad_config.extra)

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

        source: AudioSource
        if self._audio_source is not None:
            source = self._audio_source
        elif self._audio_config.type == "file":
            assert self._audio_config.audio_file is not None  # checked at init
            source = FileAudioSource(
                self._audio_config.audio_file,
                audio_format=self._audio_format,
                trailing_silence_s=self._audio_config.trailing_silence_s,
            )
        else:
            loop = asyncio.get_running_loop()
            source = AudioCapture(
                self._audio_config.device, loop, audio_format=self._audio_format
            )

        # Logged at every start, so the format a source is expected to produce
        # is never implicit.
        if self._audio_format == self._module_audio_format:
            log.info(
                "Audio format: %s, for the source and the '%s' module",
                _describe(self._audio_format),
                self._config.module.type,
            )
        else:
            log.info(
                "Audio format: %s from the source, converted to %s for the '%s' module",
                _describe(self._audio_format),
                _describe(self._module_audio_format),
                self._config.module.type,
            )
            # Before the VAD gate, so the gate and the module see the same audio.
            source = ConvertedAudioSource(
                source, self._audio_format, self._module_audio_format
            )

        audio_queue: AudioQueue
        vad_config = self._config.vad
        if self._vad_module is not None and vad_config is not None:
            from asr_engine.vad.gate import VadGate

            if not self._vad_loaded:
                # Blocking; the model then stays resident for the engine's
                # lifetime, so listen()'s start/stop cycles do not reload it.
                await asyncio.to_thread(self._vad_module.load)
                self._vad_loaded = True
            gate = VadGate(
                source,
                self._vad_module,
                self._module_audio_format,
                threshold=vad_config.threshold,
                min_speech_duration_ms=vad_config.min_speech_duration_ms,
                min_silence_duration_ms=vad_config.min_silence_duration_ms,
                speech_pad_ms=vad_config.speech_pad_ms,
                emit_end_of_speech=True,
            )
            # Stopping the gate stops the source it wraps.
            self._vad_gate = gate
            self._audio_capture = gate
            audio_queue = gate.start()
        else:
            self._audio_capture = source
            # A plain source's Queue[bytes]: the module only reads from it, so
            # widening the item type is safe.
            audio_queue = cast(AudioQueue, source.start())
        await self._segmenter.start()
        self._task = asyncio.create_task(
            self._asr_module.start(
                audio_queue,
                self._handle_utterance,
                self.set_connected,
                audio_format=self._module_audio_format,
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
        """The format the audio source produces.

        Valid right after construction. For a custom source it is what the source
        declared, or — when it declares nothing — the module's format, which the
        source must then produce.
        """
        return self._audio_format

    @property
    def module_audio_format(self) -> AudioFormat:
        """The format the ASR module is fed (after any conversion)."""
        return self._module_audio_format

    @property
    def segmentation_mode(self) -> str:
        """The current segment mode (``utterance`` / ``trigger_word`` / ``timeout``).

        Reads ``utterance`` at rest and reflects the active mode while a dictation
        session or a ``listen`` is in progress (both revert to ``utterance`` when
        they end). Complements ``status()``.
        """
        return self._segment_mode

    @property
    def vad_state(self) -> VadState | None:
        """The VAD gate's live state, or ``None`` when no VAD is configured.

        ``speaking`` says whether audio is reaching the ASR module right now, and
        ``failed_open`` whether that is because the VAD failed. Both are false
        before the first start and once stopped. Meant to be polled, e.g. by a UI.
        """
        if self._vad_module is None:
            return None
        if self._vad_gate is None:
            from asr_engine.vad.gate import VadState

            return VadState()
        return self._vad_gate.state()

    def status(self) -> dict:
        """Return current engine state."""
        return {
            "running": self._running,
            "connected": self._connected,
            "vad": self._config.vad.type if self._config.vad is not None else None,
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
