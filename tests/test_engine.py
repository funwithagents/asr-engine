"""Unit tests for ASREngine."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from asr_engine.config import (
    ASREngineConfig,
    AudioConfig,
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
    cls.SUPPORTED_SAMPLE_RATES = None
    cls.SUPPORTED_CHANNELS = None
    cls.SUPPORTED_ENCODINGS = None
    cls.DEFAULT_SAMPLE_RATE = 16000
    cls.DEFAULT_CHANNELS = 1
    cls.DEFAULT_ENCODING = "linear16"
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


def _restricted_module_class(instance, *, rates, encodings=frozenset({"linear16"})):
    """A mock ASRModule class with restricted format support, for reconcile tests."""
    cls = _mock_module_class(instance)
    cls.SUPPORTED_SAMPLE_RATES = rates
    cls.SUPPORTED_ENCODINGS = encodings
    cls.SUPPORTED_CHANNELS = frozenset({1})
    return cls


def _config_with_audio(**audio_kwargs) -> ASREngineConfig:
    return ASREngineConfig(
        sound_feedback=SoundFeedbackConfig(enabled=False),
        audio=AudioConfig(**audio_kwargs),
        module=ModuleConfig(type="mock", extra={}),
    )


def test_engine_reconciles_and_exposes_supported_format():
    cls = _restricted_module_class(MagicMock(), rates=frozenset({16000, 48000}))
    with patch.dict("asr_engine.modules.REGISTRY", {"mock": cls}):
        engine = ASREngine(_config_with_audio(sample_rate=48000))
    assert engine.audio_format.sample_rate == 48000


def test_engine_construction_errors_on_unsupported_format():
    cls = _restricted_module_class(MagicMock(), rates=frozenset({16000}))
    with patch.dict("asr_engine.modules.REGISTRY", {"mock": cls}):
        with pytest.raises(ValueError, match="sample_rate=44100"):
            ASREngine(_config_with_audio(sample_rate=44100))


def test_engine_fallback_policy_uses_module_default():
    cls = _restricted_module_class(MagicMock(), rates=frozenset({16000}))
    with patch.dict("asr_engine.modules.REGISTRY", {"mock": cls}):
        engine = ASREngine(
            _config_with_audio(sample_rate=44100, on_unsupported_format="fallback")
        )
    assert engine.audio_format.sample_rate == 16000


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
    Scripted16kVAD,
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
        audio=AudioConfig(sample_rate=sample_rate),
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


def test_resampling_without_soxr_fails_at_construction_with_an_install_hint():
    # A 24 kHz pipeline on a 16 kHz-only VAD needs the resampler…
    with patch("asr_engine.engine.importlib.util.find_spec", return_value=None):
        with pytest.raises(ImportError, match="soxr"):
            _vad_engine(vad_cls=Scripted16kVAD, sample_rate=24000)
        # …a rate the VAD supports does not, so soxr is not required for it.
        assert _vad_engine(vad_cls=Scripted16kVAD, sample_rate=16000) is not None


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
