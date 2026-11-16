"""Unit tests for ASREngine."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest
import soundfile as sf

from asr_engine.audio import AudioFormat
from asr_engine.config import (
    ASREngineConfig,
    AudioConfig,
    AudioFormatConfig,
    ModuleConfig,
    SegmentationConfig,
    SoundFeedbackConfig,
)
from asr_engine.engine import ASREngine
from asr_engine.modules.base import SpeechUtterance
from asr_engine.segmenter import SpeechSegment

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def make_config(
    module_type: str = "mock",
    *,
    extra: dict | None = None,
    listen_default_segmentation_mode: str = "trigger_word",
    dictation_default_segmentation_mode: str = "trigger_word",
    trigger_words: list[str] | None = None,
) -> ASREngineConfig:
    """Build an ASREngineConfig with sound feedback disabled (no real audio)."""
    seg = SegmentationConfig()
    if trigger_words is not None:
        seg.trigger_words = trigger_words
    return ASREngineConfig(
        listen_default_segmentation_mode=listen_default_segmentation_mode,
        dictation_default_segmentation_mode=dictation_default_segmentation_mode,
        segmentation=seg,
        sound_feedback=SoundFeedbackConfig(enabled=False),
        module=ModuleConfig(type=module_type, extra=extra or {}),
    )


def _mock_module_class(instance):
    """A MagicMock standing in for an ASRModule *class*, carrying the format
    capabilities the engine reads at construction (``None`` = accepts anything)."""
    cls = MagicMock(return_value=instance)
    # The engine reads the capabilities off the instance it built.
    instance.supported_sample_rates = None
    instance.default_sample_rate = 16000
    instance.SUPPORTED_CHANNELS = None
    instance.SUPPORTED_ENCODINGS = None
    instance.DEFAULT_CHANNELS = 1
    instance.DEFAULT_ENCODING = "linear16"
    return cls


def make_engine(on_speech_utterance=None, on_speech_segment=None, **cfg_kwargs):
    """Return an ASREngine backed by a mock ASRModule, patching REGISTRY."""
    module = MagicMock()
    module.start = AsyncMock(return_value=None)
    module.stop = AsyncMock(return_value=None)
    mock_class = _mock_module_class(module)
    with patch.dict("asr_engine.modules.REGISTRY", {"mock": mock_class}):
        engine = ASREngine(
            make_config(**cfg_kwargs),
            on_speech_utterance=on_speech_utterance,
            on_speech_segment=on_speech_segment,
        )
    return engine, module


# ---------------------------------------------------------------------------
# Constructor — validation
# ---------------------------------------------------------------------------


def test_unknown_asr_type_raises():
    with pytest.raises(ValueError, match="no_such_module"):
        ASREngine(make_config(module_type="no_such_module"))


def test_an_old_and_a_new_module_name_start_the_same_module():
    def engine_for(module_type: str) -> ASREngine:
        return ASREngine(
            ASREngineConfig.from_dict(
                {
                    "module": {"type": module_type, "api_key": "test-key"},
                    "sound_feedback": {"enabled": False},
                }
            )
        )

    old, new = engine_for("deepgram_v1"), engine_for("deepgram-v1")
    assert type(old._asr_module) is type(new._asr_module)
    assert type(new._asr_module).__name__ == "DeepgramV1Module"


def _restricted_module_class(
    instance,
    *,
    rates: frozenset[int] | None,
    default_rate: int = 16000,
    channels: frozenset[int] | None = frozenset({1}),
    encodings: frozenset[str] | None = frozenset({"linear16"}),
):
    """A mock ASRModule class with restricted format support."""
    cls = _mock_module_class(instance)
    instance.supported_sample_rates = rates
    instance.default_sample_rate = default_rate
    instance.SUPPORTED_CHANNELS = channels
    instance.SUPPORTED_ENCODINGS = encodings
    return cls


def _engine_with_audio(
    cls,
    *,
    module_format: AudioFormatConfig | None = None,
    audio_file: str | None = None,
    audio_source=None,
    **source_format,
) -> ASREngine:
    """An engine whose ``engine.audio.audio_format`` is *source_format* and whose
    ``engine.module.audio_format`` is *module_format*."""
    config = ASREngineConfig(
        sound_feedback=SoundFeedbackConfig(enabled=False),
        audio=AudioConfig(
            audio_file=audio_file, audio_format=AudioFormatConfig(**source_format)
        ),
        module=ModuleConfig(
            type="mock", audio_format=module_format or AudioFormatConfig(), extra={}
        ),
    )
    with patch.dict("asr_engine.modules.REGISTRY", {"mock": cls}):
        return ASREngine(config, audio_source=audio_source)


def test_with_nothing_set_the_source_produces_the_modules_default_format():
    cls = _restricted_module_class(
        MagicMock(), rates=frozenset({24000}), default_rate=24000
    )
    engine = _engine_with_audio(cls)
    assert engine.module_audio_format == AudioFormat(sample_rate=24000)
    assert engine.audio_format == engine.module_audio_format


def test_the_module_format_does_not_depend_on_the_source():
    cls = _restricted_module_class(
        MagicMock(),
        rates=frozenset({16000, 48000}),
        encodings=frozenset({"linear16", "mulaw"}),
    )
    # The module supports the source's 48 kHz mulaw, yet is fed its own defaults.
    engine = _engine_with_audio(cls, sample_rate=48000, channels=2, encoding="mulaw")
    assert engine.audio_format == AudioFormat(48000, 2, "mulaw")
    assert engine.module_audio_format == AudioFormat(16000, 1, "linear16")


def test_the_module_audio_format_sets_what_the_module_is_fed():
    cls = _restricted_module_class(
        MagicMock(),
        rates=frozenset({8000, 16000, 48000}),
        encodings=frozenset({"linear16", "mulaw"}),
    )
    asked = AudioFormatConfig(sample_rate=8000, encoding="mulaw")
    # A source with a rate of its own keeps it, and takes the rest from the module.
    engine = _engine_with_audio(cls, sample_rate=48000, module_format=asked)
    assert engine.module_audio_format == AudioFormat(8000, 1, "mulaw")
    assert engine.audio_format == AudioFormat(48000, 1, "mulaw")
    # A source with nothing set follows the module entirely.
    engine = _engine_with_audio(cls, module_format=asked)
    assert engine.audio_format == AudioFormat(8000, 1, "mulaw")


@pytest.mark.parametrize(
    "asked, message",
    [
        (AudioFormatConfig(sample_rate=44100), "audio_format.sample_rate=44100"),
        (AudioFormatConfig(channels=2), "audio_format.channels=2"),
        (AudioFormatConfig(encoding="mulaw"), "audio_format.encoding='mulaw'"),
    ],
)
def test_a_module_format_the_module_does_not_support_fails_at_construction(
    asked: AudioFormatConfig, message: str
):
    cls = _restricted_module_class(MagicMock(), rates=frozenset({16000}))
    with pytest.raises(ValueError, match=f"engine.module.{message}"):
        _engine_with_audio(cls, module_format=asked)


def test_a_channel_layout_that_cannot_be_converted_fails_at_construction():
    cls = _restricted_module_class(MagicMock(), rates=None, channels=frozenset({2}))
    cls.return_value.DEFAULT_CHANNELS = 2
    with pytest.raises(ValueError, match="Cannot convert 6 audio channels to 2"):
        _engine_with_audio(cls, channels=6)


def _write_wav(path, *, rate: int, channels: int = 1, seconds: float = 0.5) -> str:
    """A WAV of a 440 Hz tone at half scale, the same on every channel."""
    t = np.arange(round(rate * seconds)) / rate
    tone = (np.sin(2 * np.pi * 440 * t) * 16000).astype(np.int16)
    sf.write(str(path), np.repeat(tone.reshape(-1, 1), channels, axis=1), rate)
    return str(path)


def test_a_file_source_takes_its_rate_and_channels_from_the_file(tmp_path):
    wav = _write_wav(tmp_path / "tone.wav", rate=44100, channels=2)
    cls = _restricted_module_class(MagicMock(), rates=frozenset({16000}))
    engine = _engine_with_audio(cls, audio_file=wav)
    assert engine.audio_format == AudioFormat(sample_rate=44100, channels=2)
    assert engine.module_audio_format == AudioFormat(sample_rate=16000, channels=1)


def test_a_file_that_contradicts_the_configured_rate_or_channels_is_an_error(tmp_path):
    wav = _write_wav(tmp_path / "tone.wav", rate=44100, channels=2)
    cls = _restricted_module_class(MagicMock(), rates=None, channels=None)
    with pytest.raises(ValueError, match="expected sample rate 16000.*got 44100"):
        _engine_with_audio(cls, audio_file=wav, sample_rate=16000)
    with pytest.raises(ValueError, match=r"expected 1 channel\(s\).*got 2"):
        _engine_with_audio(cls, audio_file=wav, channels=1)
    # Stating the file's own values is accepted.
    engine = _engine_with_audio(cls, audio_file=wav, sample_rate=44100, channels=2)
    assert engine.audio_format == AudioFormat(sample_rate=44100, channels=2)


def test_a_missing_audio_file_fails_at_construction(tmp_path):
    cls = _restricted_module_class(MagicMock(), rates=None)
    with pytest.raises(FileNotFoundError, match="nope.wav"):
        _engine_with_audio(cls, audio_file=str(tmp_path / "nope.wav"))


def test_an_injected_source_is_not_described_by_the_audio_file(tmp_path):
    # The injected source overrides audio_file, so the file's rate says nothing.
    wav = _write_wav(tmp_path / "tone.wav", rate=44100)
    cls = _restricted_module_class(MagicMock(), rates=frozenset({16000}))
    engine = _engine_with_audio(cls, audio_file=wav, audio_source=MagicMock())
    assert engine.audio_format == AudioFormat(sample_rate=16000)


def test_known_asr_type_instantiates_module():
    mock_module = MagicMock()
    mock_class = _mock_module_class(mock_module)
    with patch.dict("asr_engine.modules.REGISTRY", {"fake": mock_class}):
        engine = ASREngine(make_config(module_type="fake", extra={"key": "val"}))
    mock_class.assert_called_once_with(config={"key": "val"})
    assert engine._asr_module is mock_module


def test_engine_starts_in_utterance_mode():
    """The always-on mode is always ``utterance`` at construction (no config mode)."""
    engine, _ = make_engine(trigger_words=["stop"])
    assert engine._segment_mode == "utterance"
    assert engine.segmentation_mode == "utterance"
    assert engine.dictating is False


# ---------------------------------------------------------------------------
# status() / set_connected()
# ---------------------------------------------------------------------------


def test_initial_status():
    engine, _ = make_engine()
    assert engine.status() == {"running": False, "connected": False, "vad": None}


def test_set_connected():
    engine, _ = make_engine()
    engine.set_connected(True)
    assert engine.status()["connected"] is True
    engine.set_connected(False)
    assert engine.status()["connected"] is False


# ---------------------------------------------------------------------------
# start() / stop()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_sets_running():
    engine, module = make_engine()
    fake_queue: asyncio.Queue[bytes] = asyncio.Queue()

    with patch("asr_engine.engine.AudioCapture") as MockCapture:
        instance = MockCapture.return_value
        instance.start.return_value = fake_queue

        await engine.start()

        assert engine.status()["running"] is True
        instance.start.assert_called_once()
        module.start.assert_called_once_with(
            fake_queue,
            engine._handle_utterance,
            engine.set_connected,
            audio_format=engine.audio_format,
        )
        await engine.stop()


@pytest.mark.asyncio
async def test_stop_sets_not_running():
    engine, module = make_engine()
    fake_queue: asyncio.Queue[bytes] = asyncio.Queue()

    with patch("asr_engine.engine.AudioCapture") as MockCapture:
        instance = MockCapture.return_value
        instance.start.return_value = fake_queue

        await engine.start()
        await engine.stop()

        assert engine.status()["running"] is False
        module.stop.assert_called_once()
        instance.stop.assert_called_once()


@pytest.mark.asyncio
async def test_stop_cancels_task():
    """stop() cancels the ASR module task."""
    engine, _ = make_engine()
    fake_queue: asyncio.Queue[bytes] = asyncio.Queue()

    async def hang(audio_queue, on_utterance, on_connected=None, *, audio_format=None):
        await asyncio.sleep(9999)

    engine._asr_module.start = hang

    with patch("asr_engine.engine.AudioCapture") as MockCapture:
        instance = MockCapture.return_value
        instance.start.return_value = fake_queue

        await engine.start()
        await asyncio.sleep(0)
        await engine.stop()

        assert engine._task is not None
        assert engine._task.cancelled() or engine._task.done()


# ---------------------------------------------------------------------------
# _handle_utterance — fires both callbacks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_utterance_fires_utterance_and_segment_callbacks():
    on_utterance = AsyncMock()
    segments: list[SpeechSegment] = []

    async def on_segment(seg):
        segments.append(seg)

    engine, _ = make_engine(
        on_speech_utterance=on_utterance, on_speech_segment=on_segment
    )
    # Default mode is "utterance": each final closes a segment.
    utterance = SpeechUtterance(transcript="hello", is_final=True, confidence=0.9)
    await engine._handle_utterance(utterance)

    on_utterance.assert_called_once_with(utterance)
    assert len(segments) == 1
    assert segments[0].transcript == "hello"
    assert segments[0].is_final is True
    assert segments[0].end_reason == "utterance"


# ---------------------------------------------------------------------------
# default-mode setters
# ---------------------------------------------------------------------------


def test_set_dictation_default_segmentation_mode_invalid_raises():
    engine, _ = make_engine()
    with pytest.raises(ValueError, match="Invalid segment mode"):
        engine.set_dictation_default_segmentation_mode("nonsense")


def test_set_listen_default_segmentation_mode_invalid_raises():
    engine, _ = make_engine()
    with pytest.raises(ValueError, match="Invalid segment mode"):
        engine.set_listen_default_segmentation_mode("nonsense")


def test_default_mode_setters_update_the_stored_defaults():
    engine, _ = make_engine()
    engine.set_dictation_default_segmentation_mode("timeout")
    engine.set_listen_default_segmentation_mode("utterance")
    assert engine._dictation_default_segment_mode == "timeout"
    assert engine._listen_default_segment_mode == "utterance"
    # Setting a default never changes the current (at-rest) mode.
    assert engine.segmentation_mode == "utterance"


@pytest.mark.asyncio
async def test_segmentation_mode_property_restored_after_listen(fake_engine_factory):
    """listen() runs in a different mode but the property reports ``utterance``
    again once it returns."""
    engine = fake_engine_factory(
        [
            {"text": "hi", "start_s": 0.0, "end_s": 0.1},
            {"text": "go", "start_s": 0.2, "end_s": 0.3},
        ],
        trigger_words=["go"],
    )
    assert engine.segmentation_mode == "utterance"
    await engine.listen(mode="trigger_word")
    assert engine.segmentation_mode == "utterance"


# ---------------------------------------------------------------------------
# dictation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_dictation_requires_running_engine():
    engine, _ = make_engine()
    with pytest.raises(ValueError, match="ASR is not running"):
        await engine.start_dictation(segmentation_mode="trigger_word")


@pytest.mark.asyncio
async def test_start_dictation_rejects_second_session():
    engine, _ = make_engine()
    engine._running = True
    await engine.start_dictation(segmentation_mode="trigger_word")
    with pytest.raises(ValueError, match="already in progress"):
        await engine.start_dictation(segmentation_mode="timeout")


@pytest.mark.asyncio
async def test_stop_dictation_requires_active_session():
    engine, _ = make_engine()
    with pytest.raises(ValueError, match="No dictation in progress"):
        await engine.stop_dictation()


@pytest.mark.asyncio
async def test_dictation_switches_mode_and_reverts_on_stop():
    segments: list[SpeechSegment] = []

    async def on_segment(seg):
        segments.append(seg)

    engine, _ = make_engine(on_speech_segment=on_segment, trigger_words=["stop"])
    engine._running = True
    await engine.start_dictation(
        end_on_final_segment=False, segmentation_mode="trigger_word"
    )
    assert engine.dictating is True
    assert engine.segmentation_mode == "trigger_word"

    # Two finals accumulate under trigger_word mode; neither closes the segment.
    await engine._handle_utterance(SpeechUtterance("the sky", True, None))
    await engine._handle_utterance(SpeechUtterance("is blue", True, None))
    assert all(not s.is_final for s in segments)

    # Trigger word closes the segment, excluding the trigger utterance.
    await engine._handle_utterance(SpeechUtterance("stop", True, None))
    closed = [s for s in segments if s.is_final]
    assert len(closed) == 1
    assert closed[0].transcript == "the sky is blue"
    assert closed[0].end_reason == "trigger_word"

    await engine.stop_dictation()
    assert engine.dictating is False
    assert engine.segmentation_mode == "utterance"


@pytest.mark.asyncio
async def test_dictation_default_mode_used_when_none():
    engine, _ = make_engine(dictation_default_segmentation_mode="timeout")
    engine._running = True
    await engine.start_dictation(end_on_final_segment=False)
    assert engine.segmentation_mode == "timeout"


@pytest.mark.asyncio
async def test_dictation_end_on_final_segment_auto_reverts():
    """With end_on_final_segment, the first final segment ends the dictation."""
    engine, _ = make_engine()
    engine._running = True
    # utterance mode: the first final utterance closes a segment.
    await engine.start_dictation(
        end_on_final_segment=True, segmentation_mode="utterance"
    )
    assert engine.dictating is True

    await engine._handle_utterance(SpeechUtterance("hello", True, None))
    # The auto-end runs as a scheduled task; let it complete.
    for _ in range(5):
        await asyncio.sleep(0)
        if not engine.dictating:
            break

    assert engine.dictating is False
    assert engine.segmentation_mode == "utterance"


@pytest.mark.asyncio
async def test_set_segmentation_mode_invalid_leaves_state_intact():
    """A bad mode raises without mutating the current mode or segmenter."""
    engine, _ = make_engine()
    engine._running = True
    before = engine._segmenter
    with pytest.raises(ValueError, match="Invalid segment mode"):
        await engine._set_segmentation_mode("nonsense")
    assert engine.segmentation_mode == "utterance"
    assert engine._segmenter is before


# ---------------------------------------------------------------------------
# listen()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_listen_returns_first_closed_segment(fake_engine_factory):
    engine = fake_engine_factory(
        [
            {"text": "the sky is blue", "start_s": 0.0, "end_s": 0.4},
            {"text": "submit", "start_s": 0.5, "end_s": 0.6},
        ],
        trigger_words=["submit"],
    )
    updates: list[SpeechSegment] = []

    async def on_update(s):
        updates.append(s)

    segment = await engine.listen(mode="trigger_word", on_update=on_update)

    assert segment.is_final is True
    assert segment.transcript == "the sky is blue"
    assert segment.end_reason == "trigger_word"
    assert engine.status()["running"] is False
    # on_update saw the word-by-word interims as open segments first.
    assert [s.transcript for s in updates if not s.is_final][:3] == [
        "the",
        "the sky",
        "the sky is",
    ]


@pytest.mark.asyncio
async def test_listen_uses_default_mode_when_none(fake_engine_factory):
    """listen(None) falls back to listen_default_segmentation_mode."""
    engine = fake_engine_factory(
        [
            {"text": "hello there", "start_s": 0.0, "end_s": 0.2},
            {"text": "go", "start_s": 0.3, "end_s": 0.4},
        ],
        listen_default_segmentation_mode="trigger_word",
        trigger_words=["go"],
    )
    segment = await engine.listen()
    assert segment.end_reason == "trigger_word"
    assert segment.transcript == "hello there"


@pytest.mark.asyncio
async def test_listen_reverts_to_utterance_mode(fake_engine_factory):
    engine = fake_engine_factory(
        [
            {"text": "hi", "start_s": 0.0, "end_s": 0.1},
            {"text": "go", "start_s": 0.2, "end_s": 0.3},
        ],
        trigger_words=["go"],
    )
    assert engine._segment_mode == "utterance"
    await engine.listen(mode="trigger_word")
    assert engine._segment_mode == "utterance"


@pytest.mark.asyncio
async def test_set_segmentation_params_updates_trigger_words():
    engine, _ = make_engine()
    await engine.set_segmentation_params(trigger_words=["stop"])
    assert engine._trigger_words == ["stop"]
    assert engine._segmenter._trigger_words == ["stop"]


@pytest.mark.asyncio
async def test_listen_rejects_running_engine():
    engine, _ = make_engine()
    fake_queue: asyncio.Queue[bytes] = asyncio.Queue()
    with patch("asr_engine.engine.AudioCapture") as MockCapture:
        MockCapture.return_value.start.return_value = fake_queue
        await engine.start()
        try:
            with pytest.raises(ValueError, match="already running"):
                await engine.listen(mode="trigger_word")
        finally:
            await engine.stop()


# ---------------------------------------------------------------------------
# VAD gating (specs/vad.md) — on a deterministic VAD and a hand-fed source
# ---------------------------------------------------------------------------

from asr_engine.audio import (  # noqa: E402
    DEFAULT_AUDIO_FORMAT,
    END_OF_SPEECH,
)
from asr_engine.config import VadConfig  # noqa: E402
from asr_engine.modules.base import ASRModule  # noqa: E402
from tests.vad.scripted import (  # noqa: E402
    HandFedSource,
    ScriptedVAD,
    loud,
    pcm,
    quiet,
    settle,
)


class RecordingModule(ASRModule):
    """An ASR module that records every item it reads from its audio queue."""

    SUPPORTED_SAMPLE_RATES = None
    SUPPORTED_CHANNELS = None
    SUPPORTED_ENCODINGS = None
    DEFAULT_SAMPLE_RATE = 16000
    DEFAULT_CHANNELS = 1
    DEFAULT_ENCODING = "linear16"
    VAD_GATING = "supported"

    instances: list["RecordingModule"] = []

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.items: list = []
        RecordingModule.instances.append(self)

    async def start(
        self,
        audio_queue,
        on_utterance,
        on_connected=None,
        *,
        audio_format=DEFAULT_AUDIO_FORMAT,
    ) -> None:
        self.audio_format = audio_format
        while True:
            self.items.append(await audio_queue.get())

    async def stop(self) -> None:
        pass


class UngatedModule(RecordingModule):
    VAD_GATING = "unsupported"


class BatchLikeModule(RecordingModule):
    VAD_GATING = "required"


# Open after 30 ms of speech, close after 50 ms of silence, 20 ms of pre-roll —
# the same figures as tests/vad/test_gate.py, on the scripted VAD's 10 ms frames.
_VAD = VadConfig(
    type="scripted",
    min_speech_duration_ms=30,
    min_silence_duration_ms=50,
    speech_pad_ms=20,
)


def _vad_engine(
    module_cls: type = RecordingModule,
    *,
    vad: VadConfig | None = _VAD,
    vad_cls: type = ScriptedVAD,
    sample_rate: int = 16000,
    source: HandFedSource | None = None,
    module_extra: dict | None = None,
    **engine_kwargs,
) -> ASREngine:
    config = ASREngineConfig(
        sound_feedback=SoundFeedbackConfig(enabled=False),
        audio=AudioConfig(audio_format=AudioFormatConfig(sample_rate=sample_rate)),
        module=ModuleConfig(type="recording", extra=module_extra or {}),
        vad=vad,
    )
    modules = {"recording": module_cls} if module_cls is not None else {}
    with (
        patch.dict("asr_engine.modules.REGISTRY", modules),
        patch.dict("asr_engine.vad.REGISTRY", {"scripted": vad_cls}),
    ):
        return ASREngine(config, audio_source=source, **engine_kwargs)


@pytest.mark.asyncio
async def test_vad_module_receives_only_speech_then_one_marker_per_utterance():
    source = HandFedSource()
    engine = _vad_engine(source=source)
    module = engine._asr_module
    assert isinstance(module, RecordingModule)
    first = pcm(quiet(200), loud(100), quiet(300, seed=3))
    second = pcm(quiet(200, seed=4), loud(60, seed=5), quiet(300, seed=6))

    await engine.start()
    assert engine.status() == {"running": True, "connected": False, "vad": "scripted"}
    source.queue.put_nowait(first)
    source.queue.put_nowait(second)
    await settle()
    await engine.stop()

    markers = [n for n, item in enumerate(module.items) if item is END_OF_SPEECH]
    assert len(markers) == 2 and markers[-1] == len(module.items) - 1
    # Pre-roll from 180 ms, hangover to 350 ms / 310 ms (see tests/vad/test_gate.py).
    assert b"".join(module.items[: markers[0]]) == first[180 * 32 : 350 * 32]
    assert b"".join(module.items[markers[0] + 1 : -1]) == second[180 * 32 : 310 * 32]
    assert source.stops == 1  # stopping the engine stops the wrapped source


def test_vad_with_a_module_that_does_not_handle_the_marker_is_a_config_error():
    with pytest.raises(ValueError, match="'recording' does not support VAD gating"):
        _vad_engine(UngatedModule)


def test_a_module_that_requires_vad_is_a_config_error_without_one():
    with pytest.raises(ValueError, match="'recording' needs a VAD.*engine.vad"):
        _vad_engine(BatchLikeModule, vad=None)
    # …and builds with one.
    assert _vad_engine(BatchLikeModule).status()["vad"] == "scripted"


def test_unknown_vad_type_fails_at_construction():
    with pytest.raises(ValueError, match="Unknown VAD type 'nope'"):
        _vad_engine(vad=VadConfig(type="nope"))


def test_vad_module_config_is_validated_at_construction():
    with pytest.raises(ValueError, match="boom"):
        _vad_engine(vad=VadConfig(type="scripted", extra={"boom": 1}))


@pytest.mark.asyncio
async def test_vad_model_is_loaded_once_across_start_stop_cycles():
    source = HandFedSource()
    engine = _vad_engine(source=source)
    vad = engine._vad_module
    assert isinstance(vad, ScriptedVAD) and vad.loads == 0  # not at construction

    utterance = pcm(quiet(200), loud(100), quiet(300, seed=3))
    module = engine._asr_module
    assert isinstance(module, RecordingModule)
    for cycle in (1, 2):
        await engine.start()
        source.queue.put_nowait(utterance)
        await settle()
        await engine.stop()
        # Each run gates from a clean state: one utterance, one marker.
        assert module.items.count(END_OF_SPEECH) == cycle
    assert vad.loads == 1
    assert (source.starts, source.stops) == (2, 2)


@pytest.mark.asyncio
async def test_fake_module_runs_its_script_on_gated_audio_only():
    """``fake`` + VAD end to end: its audio-time clock counts only what the gate
    lets through, so silence does not advance the script."""
    utterances: list[SpeechUtterance] = []

    async def collect(u: SpeechUtterance) -> None:
        utterances.append(u)

    source = HandFedSource()
    config = ASREngineConfig(
        sound_feedback=SoundFeedbackConfig(enabled=False),
        module=ModuleConfig(
            type="fake",
            extra={"utterances": [{"text": "hello", "start_s": 0.0, "end_s": 0.2}]},
        ),
        vad=_VAD,
    )
    with patch.dict("asr_engine.vad.REGISTRY", {"scripted": ScriptedVAD}):
        engine = ASREngine(config, audio_source=source, on_speech_utterance=collect)

    await engine.start()
    source.queue.put_nowait(pcm(quiet(1000)))  # five times the script's length
    await settle()
    assert utterances == []  # gated out: the fake's clock has not moved

    source.queue.put_nowait(pcm(loud(300), quiet(200)))
    await settle()
    await engine.stop()
    assert [(u.transcript, u.is_final) for u in utterances] == [("hello", True)]


def test_no_vad_configured_imports_nothing_vad_related():
    import subprocess
    import sys

    code = (
        "import sys\n"
        "from unittest.mock import MagicMock\n"
        # Stub sounddevice as tests/conftest.py does, so PortAudio isn't needed.
        "sys.modules['sounddevice'] = MagicMock()\n"
        "from asr_engine import ASREngine, ASREngineConfig\n"
        "ASREngine(ASREngineConfig.from_dict({'module': {'type': 'fake'},"
        " 'sound_feedback': {'enabled': False}}))\n"
        "bad = [m for m in sys.modules if m.startswith(('asr_engine.vad',"
        " 'onnxruntime', 'soxr'))]\n"
        "assert not bad, bad\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.asyncio
async def test_vad_state_is_none_without_vad_and_follows_the_gate_with_it():
    assert _vad_engine(vad=None).vad_state is None

    from asr_engine.vad.gate import VadState

    source = HandFedSource()
    engine = _vad_engine(source=source)
    assert engine.vad_state == VadState()  # before the first start

    await engine.start()
    source.queue.put_nowait(pcm(quiet(200), loud(100)))  # speech, still going
    await settle()
    assert engine.vad_state == VadState(speaking=True)
    source.queue.put_nowait(pcm(quiet(300, seed=3)))  # the gate closes
    await settle()
    assert engine.vad_state == VadState()
    await engine.stop()


# ---------------------------------------------------------------------------
# Format conversion (specs/format-conversion.md) — on a hand-fed source
# ---------------------------------------------------------------------------


def _tone(ms: float, rate: int) -> np.ndarray:
    """*ms* of a 440 Hz tone at half scale: speech to the scripted VAD."""
    t = np.arange(round(rate * ms / 1000)) / rate
    return (np.sin(2 * np.pi * 440 * t) * 16000).astype("<i2")


def _converting_engine(
    source: HandFedSource, *, vad: VadConfig | None = None
) -> ASREngine:
    """A 44.1 kHz source feeding a recording module, which is fed its 16 kHz
    default."""
    config = ASREngineConfig(
        sound_feedback=SoundFeedbackConfig(enabled=False),
        audio=AudioConfig(audio_format=AudioFormatConfig(sample_rate=44100)),
        module=ModuleConfig(type="recording"),
        vad=vad,
    )
    with (
        patch.dict("asr_engine.modules.REGISTRY", {"recording": RecordingModule}),
        patch.dict("asr_engine.vad.REGISTRY", {"scripted": ScriptedVAD}),
    ):
        return ASREngine(config, audio_source=source)


def _feed(source: HandFedSource, samples: np.ndarray, chunk_samples: int) -> None:
    data = samples.astype("<i2").tobytes()
    for i in range(0, len(data), chunk_samples * 2):
        source.queue.put_nowait(data[i : i + chunk_samples * 2])


@pytest.mark.asyncio
async def test_the_module_is_fed_the_source_audio_converted_to_its_own_format():
    source = HandFedSource()
    engine = _converting_engine(source)
    assert engine.audio_format.sample_rate == 44100
    await engine.start()
    module = engine._asr_module
    assert isinstance(module, RecordingModule)
    _feed(source, _tone(1000, 44100), 4410)
    await settle()

    assert module.audio_format == AudioFormat(sample_rate=16000)
    received = np.frombuffer(b"".join(module.items), dtype="<i2")
    # One second of 44.1 kHz audio arrives as one second at 16 kHz (less the
    # resampler's hold), still a 440 Hz tone.
    assert 16000 * 0.9 <= len(received) <= 16000
    spectrum = np.abs(np.fft.rfft(received * np.hanning(len(received))))
    peak_hz = np.fft.rfftfreq(len(received), 1 / 16000)[int(spectrum.argmax())]
    assert peak_hz == pytest.approx(440, abs=2)

    await engine.stop()
    assert source.stops == 1  # stopping the engine stops the wrapped source


@pytest.mark.asyncio
async def test_matching_formats_reach_the_module_untouched():
    source = HandFedSource()
    engine = _vad_engine(source=source, vad=None)  # 16 kHz both sides
    await engine.start()
    chunk = pcm(loud(100))
    source.queue.put_nowait(chunk)
    await settle()
    # The very object the source produced: no converter stands in between.
    module = engine._asr_module
    assert isinstance(module, RecordingModule)
    assert module.items[0] is chunk
    await engine.stop()


@pytest.mark.asyncio
async def test_the_vad_gate_runs_on_the_converted_audio():
    source = HandFedSource()
    engine = _converting_engine(source, vad=_VAD)
    await engine.start()
    module, vad = engine._asr_module, engine._vad_module
    assert isinstance(module, RecordingModule) and isinstance(vad, ScriptedVAD)
    silence = np.zeros(round(44100 * 0.3), dtype="<i2")
    _feed(source, np.concatenate([silence, _tone(300, 44100), silence]), 4410)
    await settle()

    # The gate analysed the module's 16 kHz audio directly, with no second
    # resampling of its own.
    assert set(vad.rates) == {16000}
    assert module.items[-1] is END_OF_SPEECH
    speech = b"".join(module.items[:-1])
    # 300 ms of tone, plus the 20 ms pre-roll and the 50 ms hangover, at 16 kHz.
    assert len(speech) / 2 / 16000 == pytest.approx(0.37, abs=0.03)
    await engine.stop()


@pytest.mark.asyncio
async def test_each_start_logs_the_formats_in_use(caplog: pytest.LogCaptureFixture):
    with caplog.at_level("INFO", logger="asr_engine.engine"):
        same = _vad_engine(source=HandFedSource(), vad=None)
        await same.start()
        await same.stop()
        converting = _converting_engine(HandFedSource())
        await converting.start()
        await converting.stop()
    # What an injected source must produce is spelled out, converted or not.
    assert (
        "Audio format: 16000 Hz, 1 channel(s), linear16, for the source and the "
        "'recording' module" in caplog.text
    )
    assert (
        "Audio format: 44100 Hz, 1 channel(s), linear16 from the source, converted "
        "to 16000 Hz, 1 channel(s), linear16 for the 'recording' module" in caplog.text
    )
