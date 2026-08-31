"""Unit tests for ASREngine."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from asr_mcp.config import ASRConfig, AudioConfig
from asr_mcp.engine import ASREngine
from asr_mcp.modules.base import SpeechUtterance
from asr_mcp.segmenter import SpeechSegment

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def make_engine(on_speech_utterance=None, on_speech_segment=None):
    """Return an ASREngine backed by a mock ASRModule, patching REGISTRY."""
    audio_config = AudioConfig(device=None)
    module = MagicMock()
    module.start = AsyncMock(return_value=None)
    module.stop = AsyncMock(return_value=None)
    mock_class = MagicMock(return_value=module)
    asr_config = ASRConfig(type="mock")
    with patch.dict("asr_mcp.engine.REGISTRY", {"mock": mock_class}):
        engine = ASREngine(
            audio_config,
            asr_config,
            on_speech_utterance=on_speech_utterance,
            on_speech_segment=on_speech_segment,
        )
    return engine, module


class _ScriptedModule:
    """Fake ASR module that replays a list of utterances, then blocks until stop."""

    def __init__(self, utterances: list[SpeechUtterance]) -> None:
        self._utterances = utterances
        self._stopped = asyncio.Event()

    async def start(self, audio_queue, on_utterance, on_connected=None):
        for u in self._utterances:
            await on_utterance(u)
        await self._stopped.wait()

    async def stop(self):
        self._stopped.set()


def make_scripted_engine(utterances: list[SpeechUtterance]):
    """Engine wired to a _ScriptedModule and a fake audio source."""
    audio_config = AudioConfig(device=None)
    module = _ScriptedModule(utterances)
    audio_source = MagicMock()
    audio_source.start.return_value = asyncio.Queue()
    with patch.dict(
        "asr_mcp.engine.REGISTRY", {"mock": MagicMock(return_value=module)}
    ):
        engine = ASREngine(
            audio_config, ASRConfig(type="mock"), audio_source=audio_source
        )
    return engine


# ---------------------------------------------------------------------------
# Constructor — validation
# ---------------------------------------------------------------------------


def test_unknown_asr_type_raises():
    audio_config = AudioConfig(device=None)
    asr_config = ASRConfig(type="no_such_module")
    with pytest.raises(ValueError, match="no_such_module"):
        ASREngine(audio_config, asr_config)


def test_known_asr_type_instantiates_module():
    audio_config = AudioConfig(device=None)
    mock_module = MagicMock()
    mock_class = MagicMock(return_value=mock_module)
    asr_config = ASRConfig(type="fake", extra={"key": "val"})
    with patch.dict("asr_mcp.engine.REGISTRY", {"fake": mock_class}):
        engine = ASREngine(audio_config, asr_config)
    mock_class.assert_called_once_with(config={"key": "val"})
    assert engine._asr_module is mock_module


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

    with patch("asr_mcp.engine.AudioCapture") as MockCapture:
        instance = MockCapture.return_value
        instance.start.return_value = fake_queue

        await engine.start()

        assert engine.status()["running"] is True
        instance.start.assert_called_once()
        module.start.assert_called_once_with(
            fake_queue, engine._handle_utterance, engine.set_connected
        )
        await engine.stop()


@pytest.mark.asyncio
async def test_stop_sets_not_running():
    engine, module = make_engine()
    fake_queue: asyncio.Queue[bytes] = asyncio.Queue()

    with patch("asr_mcp.engine.AudioCapture") as MockCapture:
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

    async def hang(audio_queue, on_utterance, on_connected=None):
        await asyncio.sleep(9999)

    engine._asr_module.start = hang

    with patch("asr_mcp.engine.AudioCapture") as MockCapture:
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
# set_segment_mode
# ---------------------------------------------------------------------------


def test_set_segment_mode_invalid_raises():
    engine, _ = make_engine()
    with pytest.raises(ValueError, match="Invalid segment mode"):
        engine.set_segment_mode("nonsense")


@pytest.mark.asyncio
async def test_set_segment_mode_switches_behaviour():
    segments: list[SpeechSegment] = []

    async def on_segment(seg):
        segments.append(seg)

    engine, _ = make_engine(on_speech_segment=on_segment)
    engine.set_segment_mode("trigger_word", trigger_words=["stop"])

    # Two finals accumulate; neither closes the segment.
    await engine._handle_utterance(SpeechUtterance("the sky", True, None))
    await engine._handle_utterance(SpeechUtterance("is blue", True, None))
    assert all(not s.is_final for s in segments)
    assert segments[-1].transcript == "the sky is blue"

    # Trigger word closes it, excluding the trigger utterance.
    await engine._handle_utterance(SpeechUtterance("stop", True, None))
    closed = [s for s in segments if s.is_final]
    assert len(closed) == 1
    assert closed[0].transcript == "the sky is blue"
    assert closed[0].end_reason == "trigger_word"


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
        ]
    )
    updates: list[SpeechSegment] = []

    segment = await engine.listen(
        mode="trigger_word",
        trigger_words=["submit"],
        initial_silence_timeout_s=10.0,
        end_of_speech_timeout_s=5.0,
        on_update=lambda s: updates.append(s) or asyncio.sleep(0),
    )

    assert segment.is_final is True
    assert segment.transcript == "the sky is blue"
    assert segment.end_reason == "trigger_word"
    assert engine.status()["running"] is False
    # on_update saw interim segments too.
    assert any(not s.is_final for s in updates)


@pytest.mark.asyncio
async def test_listen_rejects_running_engine():
    engine, _ = make_engine()
    fake_queue: asyncio.Queue[bytes] = asyncio.Queue()
    with patch("asr_mcp.engine.AudioCapture") as MockCapture:
        MockCapture.return_value.start.return_value = fake_queue
        await engine.start()
        try:
            with pytest.raises(ValueError, match="already running"):
                await engine.listen(
                    mode="trigger_word",
                    trigger_words=["submit"],
                    initial_silence_timeout_s=10.0,
                    end_of_speech_timeout_s=5.0,
                )
        finally:
            await engine.stop()
