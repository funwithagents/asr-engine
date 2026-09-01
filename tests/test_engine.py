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
    with patch.dict("asr_engine.engine.REGISTRY", {"mock": mock_class}):
        engine = ASREngine(
            make_config(**cfg_kwargs),
            on_speech_utterance=on_speech_utterance,
            on_speech_segment=on_speech_segment,
        )
    return engine, module


class _ScriptedModule:
    """Fake ASR module that replays a list of utterances, then blocks until stop."""

    def __init__(self, utterances: list[SpeechUtterance]) -> None:
        self._utterances = utterances
        self._stopped = asyncio.Event()

    async def start(
        self, audio_queue, on_utterance, on_connected=None, *, audio_format=None
    ):
        for u in self._utterances:
            await on_utterance(u)
        await self._stopped.wait()

    async def stop(self):
        self._stopped.set()


def make_scripted_engine(utterances: list[SpeechUtterance], **cfg_kwargs):
    """Engine wired to a _ScriptedModule and a fake audio source."""
    module = _ScriptedModule(utterances)
    audio_source = MagicMock()
    audio_source.start.return_value = asyncio.Queue()
    with patch.dict("asr_engine.engine.REGISTRY", {"mock": _mock_module_class(module)}):
        engine = ASREngine(make_config(**cfg_kwargs), audio_source=audio_source)
    return engine


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
    with patch.dict("asr_engine.engine.REGISTRY", {"mock": cls}):
        engine = ASREngine(_config_with_audio(sample_rate=48000))
    assert engine.audio_format.sample_rate == 48000


def test_engine_construction_errors_on_unsupported_format():
    cls = _restricted_module_class(MagicMock(), rates=frozenset({16000}))
    with patch.dict("asr_engine.engine.REGISTRY", {"mock": cls}):
        with pytest.raises(ValueError, match="sample_rate=44100"):
            ASREngine(_config_with_audio(sample_rate=44100))


def test_engine_fallback_policy_uses_module_default():
    cls = _restricted_module_class(MagicMock(), rates=frozenset({16000}))
    with patch.dict("asr_engine.engine.REGISTRY", {"mock": cls}):
        engine = ASREngine(
            _config_with_audio(sample_rate=44100, on_unsupported_format="fallback")
        )
    assert engine.audio_format.sample_rate == 16000


def test_known_asr_type_instantiates_module():
    mock_module = MagicMock()
    mock_class = _mock_module_class(mock_module)
    with patch.dict("asr_engine.engine.REGISTRY", {"fake": mock_class}):
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
    assert engine.status() == {"running": False, "connected": False}


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
async def test_segmentation_mode_property_restored_after_listen():
    """listen() runs in a different mode but the property reports ``utterance``
    again once it returns."""
    engine = make_scripted_engine(
        [SpeechUtterance("hi", True, None), SpeechUtterance("go", True, None)],
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
async def test_listen_returns_first_closed_segment():
    engine = make_scripted_engine(
        [
            SpeechUtterance("the sky", False, None),
            SpeechUtterance("the sky is blue", True, None),
            SpeechUtterance("submit", True, None),
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
    # on_update saw interim segments too.
    assert any(not s.is_final for s in updates)


@pytest.mark.asyncio
async def test_listen_uses_default_mode_when_none():
    """listen(None) falls back to listen_default_segmentation_mode."""
    engine = make_scripted_engine(
        [
            SpeechUtterance("hello there", True, None),
            SpeechUtterance("go", True, None),
        ],
        listen_default_segmentation_mode="trigger_word",
        trigger_words=["go"],
    )
    segment = await engine.listen()
    assert segment.end_reason == "trigger_word"
    assert segment.transcript == "hello there"


@pytest.mark.asyncio
async def test_listen_reverts_to_utterance_mode():
    engine = make_scripted_engine(
        [SpeechUtterance("hi", True, None), SpeechUtterance("go", True, None)],
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
