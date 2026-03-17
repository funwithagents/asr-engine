"""Unit tests for ASREngine."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from asr_mcp.config import ASRConfig, AudioConfig
from asr_mcp.engine import ASREngine
from asr_mcp.modules.base import ASRResult


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def make_engine(on_result=None):
    """Return an ASREngine backed by a mock ASRModule, patching REGISTRY."""
    audio_config = AudioConfig(device=None)
    module = MagicMock()
    module.start = AsyncMock(return_value=None)
    module.stop = AsyncMock(return_value=None)
    mock_class = MagicMock(return_value=module)
    if on_result is None:
        on_result = AsyncMock()
    asr_config = ASRConfig(type="mock")
    with patch.dict("asr_mcp.engine.REGISTRY", {"mock": mock_class}):
        engine = ASREngine(audio_config, asr_config, on_result)
    return engine, module, on_result


# ---------------------------------------------------------------------------
# Constructor — validation
# ---------------------------------------------------------------------------

def test_unknown_asr_type_raises():
    audio_config = AudioConfig(device=None)
    asr_config = ASRConfig(type="no_such_module")
    with pytest.raises(ValueError, match="no_such_module"):
        ASREngine(audio_config, asr_config, AsyncMock())


def test_known_asr_type_instantiates_module():
    audio_config = AudioConfig(device=None)
    mock_module = MagicMock()
    mock_class = MagicMock(return_value=mock_module)
    asr_config = ASRConfig(type="fake", extra={"key": "val"})
    with patch.dict("asr_mcp.engine.REGISTRY", {"fake": mock_class}):
        engine = ASREngine(audio_config, asr_config, AsyncMock())
    mock_class.assert_called_once_with(config={"key": "val"})
    assert engine._asr_module is mock_module


# ---------------------------------------------------------------------------
# status() — before start
# ---------------------------------------------------------------------------

def test_initial_status():
    engine, _, _ = make_engine()
    assert engine.status() == {"running": False, "paused": False, "connected": False}


# ---------------------------------------------------------------------------
# set_connected()
# ---------------------------------------------------------------------------

def test_set_connected_true():
    engine, _, _ = make_engine()
    engine.set_connected(True)
    assert engine.status()["connected"] is True


def test_set_connected_false():
    engine, _, _ = make_engine()
    engine.set_connected(True)
    engine.set_connected(False)
    assert engine.status()["connected"] is False


# ---------------------------------------------------------------------------
# pause() / resume() — without audio capture
# ---------------------------------------------------------------------------

def test_pause_raises_when_already_paused():
    engine, _, _ = make_engine()
    engine._paused = True
    with pytest.raises(RuntimeError, match="already paused"):
        engine.pause()


def test_resume_raises_when_not_paused():
    engine, _, _ = make_engine()
    with pytest.raises(RuntimeError, match="not paused"):
        engine.resume()


def test_pause_sets_flag():
    engine, _, _ = make_engine()
    engine.pause()
    assert engine.status()["paused"] is True


def test_resume_clears_flag():
    engine, _, _ = make_engine()
    engine.pause()
    engine.resume()
    assert engine.status()["paused"] is False


# ---------------------------------------------------------------------------
# pause() / resume() — delegates to AudioCapture
# ---------------------------------------------------------------------------

def test_pause_calls_audio_capture_pause():
    engine, _, _ = make_engine()
    mock_capture = MagicMock()
    engine._audio_capture = mock_capture
    engine.pause()
    mock_capture.pause.assert_called_once()


def test_resume_calls_audio_capture_resume():
    engine, _, _ = make_engine()
    mock_capture = MagicMock()
    engine._audio_capture = mock_capture
    engine._paused = True
    engine.resume()
    mock_capture.resume.assert_called_once()


# ---------------------------------------------------------------------------
# start() / stop()
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_start_sets_running():
    engine, module, _ = make_engine()
    fake_queue: asyncio.Queue[bytes] = asyncio.Queue()

    with patch("asr_mcp.engine.AudioCapture") as MockCapture:
        instance = MockCapture.return_value
        instance.start.return_value = fake_queue

        await engine.start()

        assert engine.status()["running"] is True
        instance.start.assert_called_once()
        module.start.assert_called_once_with(fake_queue, engine._handle_result, engine.set_connected)


@pytest.mark.asyncio
async def test_stop_sets_not_running():
    engine, module, _ = make_engine()
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
    engine, _, _ = make_engine()
    fake_queue: asyncio.Queue[bytes] = asyncio.Queue()

    async def hang(_queue, _cb, _conn=None):
        await asyncio.sleep(9999)

    engine._asr_module.start = hang

    with patch("asr_mcp.engine.AudioCapture") as MockCapture:
        instance = MockCapture.return_value
        instance.start.return_value = fake_queue

        await engine.start()
        # Give the task a tick to start
        await asyncio.sleep(0)
        await engine.stop()

        assert engine._task.cancelled() or engine._task.done()


# ---------------------------------------------------------------------------
# _handle_result()
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handle_result_forwards_when_not_paused():
    on_result = AsyncMock()
    engine, _, _ = make_engine(on_result=on_result)
    result = ASRResult(transcript="hello", is_final=True, confidence=0.9)
    await engine._handle_result(result)
    on_result.assert_called_once_with(result)


@pytest.mark.asyncio
async def test_handle_result_discards_when_paused():
    on_result = AsyncMock()
    engine, _, _ = make_engine(on_result=on_result)
    engine._paused = True
    result = ASRResult(transcript="hello", is_final=True, confidence=0.9)
    await engine._handle_result(result)
    on_result.assert_not_called()


# ---------------------------------------------------------------------------
# Full pause/resume cycle — results discarded while paused
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_results_discarded_during_pause_cycle():
    on_result = AsyncMock()
    engine, _, _ = make_engine(on_result=on_result)
    result = ASRResult(transcript="test", is_final=False, confidence=None)

    await engine._handle_result(result)  # not paused → forwarded
    engine.pause()
    await engine._handle_result(result)  # paused → discarded
    engine.resume()
    await engine._handle_result(result)  # resumed → forwarded

    assert on_result.call_count == 2
