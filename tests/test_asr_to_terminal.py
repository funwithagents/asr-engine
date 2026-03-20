"""Unit tests for terminal_typer.py and asr_to_terminal.py."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from asr_mcp.asr_to_terminal import AsrToTerminal
from asr_mcp.end_of_utterance_detector import UtteranceResult
from asr_mcp.terminal_typer import TerminalTyper


# ---------------------------------------------------------------------------
# TerminalTyper — display server resolution
# ---------------------------------------------------------------------------


class TestTerminalTyperResolution:
    def test_auto_detect_x11(self, monkeypatch):
        monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
        t = TerminalTyper()
        assert t._display_server == "x11"

    def test_auto_detect_wayland(self, monkeypatch):
        monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
        t = TerminalTyper()
        assert t._display_server == "wayland"

    def test_explicit_arg_overrides_env(self, monkeypatch):
        monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
        t = TerminalTyper(display_server="x11")
        assert t._display_server == "x11"

    def test_missing_env_raises(self, monkeypatch):
        monkeypatch.delenv("XDG_SESSION_TYPE", raising=False)
        with pytest.raises(RuntimeError, match="Cannot detect display server"):
            TerminalTyper()

    def test_unknown_value_raises(self, monkeypatch):
        monkeypatch.delenv("XDG_SESSION_TYPE", raising=False)
        with pytest.raises(RuntimeError, match="Unsupported display server"):
            TerminalTyper(display_server="mir")


# ---------------------------------------------------------------------------
# AsrToTerminal state machine — typing logic
# ---------------------------------------------------------------------------


def _make_atr(typer_mock):
    """Build AsrToTerminal with a mocked TerminalTyper and AsrResourceClient."""
    with patch("asr_mcp.asr_to_terminal.TerminalTyper", return_value=typer_mock), \
         patch("asr_mcp.asr_to_terminal.AsrResourceClient"):
        atr = AsrToTerminal(trigger_words=["validate"])
    return atr


def _typer_mock():
    m = MagicMock()
    m.backspace = AsyncMock()
    m.type_text = AsyncMock()
    m.send_enter = AsyncMock()
    return m


@pytest.mark.asyncio
async def test_first_interim_no_backspace():
    """First interim: no backspace (nothing pending), text typed, _pending set."""
    typer = _typer_mock()
    atr = _make_atr(typer)

    await atr._on_event({"transcript": "hello", "is_final": False})

    typer.backspace.assert_called_once_with(0)
    typer.type_text.assert_called_once_with("hello")
    assert atr._pending == "hello"


@pytest.mark.asyncio
async def test_second_interim_types_suffix():
    """Second interim: only type the changed suffix (shared prefix is kept), update _pending."""
    typer = _typer_mock()
    atr = _make_atr(typer)
    atr._pending = "hello"

    await atr._on_event({"transcript": "hello world", "is_final": False})

    typer.backspace.assert_called_once_with(0)  # shared prefix "hello" → no backspaces
    typer.type_text.assert_called_once_with(" world")
    assert atr._pending == "hello world"


@pytest.mark.asyncio
async def test_final_commits_text_and_clears_pending():
    """Final: type suffix over shared prefix, reset _pending."""
    typer = _typer_mock()
    atr = _make_atr(typer)
    atr._pending = "hello"

    await atr._on_event({"transcript": "hello world", "is_final": True})

    typer.backspace.assert_called_once_with(0)  # shared prefix "hello" → no backspaces
    typer.type_text.assert_called_once_with(" world")
    typer.send_enter.assert_not_called()
    assert atr._pending == ""


@pytest.mark.asyncio
async def test_final_with_trigger_word_types_text_and_clears_pending():
    """Final with trigger word: types the text (Enter is handled by session loop, not _handle_event)."""
    typer = _typer_mock()
    atr = _make_atr(typer)
    atr._pending = "hello"

    await atr._on_event({"transcript": "the sky is blue validate", "is_final": True})

    # Should type the suffix (no trigger-word special case in _handle_event anymore)
    typer.backspace.assert_called_once_with(5)  # erase "hello" (no common prefix)
    typer.type_text.assert_called_once_with("the sky is blue validate")
    typer.send_enter.assert_not_called()
    assert atr._pending == ""


# ---------------------------------------------------------------------------
# AsrToTerminal session loop — Enter sending
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_loop_trigger_word_sends_enter():
    """Session loop fires Enter when EndOfUtteranceDetector signals trigger_word."""
    typer = _typer_mock()

    # Make wait() return once with trigger_word, then block forever on second call
    call_count = 0
    blocked = asyncio.Event()

    async def fake_wait():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return UtteranceResult(transcript="hello world", end_reason="trigger_word")
        await blocked.wait()  # block second iteration

    mock_detector = AsyncMock()
    mock_detector.wait = fake_wait
    mock_detector.on_result = AsyncMock()

    entered = asyncio.Event()
    original_send_enter = typer.send_enter

    async def _tracking_send_enter():
        await original_send_enter()
        entered.set()

    typer.send_enter = AsyncMock(side_effect=_tracking_send_enter)

    def _mock_client():
        c = MagicMock()
        c.start = AsyncMock()
        c.stop = AsyncMock()
        return c

    with patch("asr_mcp.asr_to_terminal.TerminalTyper", return_value=typer), \
         patch("asr_mcp.asr_to_terminal.AsrResourceClient", return_value=_mock_client()), \
         patch("asr_mcp.asr_to_terminal.EndOfUtteranceDetector", return_value=mock_detector):
        atr = AsrToTerminal(mode="trigger_word", trigger_words=["validate"])
        await atr.start()
        try:
            await asyncio.wait_for(entered.wait(), timeout=2.0)
        finally:
            blocked.set()
            await atr.stop()

    typer.send_enter.assert_called()


@pytest.mark.asyncio
async def test_session_loop_timeout_sends_enter():
    """Session loop fires Enter when EndOfUtteranceDetector signals end_of_speech_timeout."""
    typer = _typer_mock()

    call_count = 0
    blocked = asyncio.Event()

    async def fake_wait():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return UtteranceResult(transcript="the sky is blue", end_reason="end_of_speech_timeout")
        await blocked.wait()

    mock_detector = AsyncMock()
    mock_detector.wait = fake_wait
    mock_detector.on_result = AsyncMock()

    entered = asyncio.Event()
    original_send_enter = typer.send_enter

    async def _tracking_send_enter():
        await original_send_enter()
        entered.set()

    typer.send_enter = AsyncMock(side_effect=_tracking_send_enter)

    def _mock_client():
        c = MagicMock()
        c.start = AsyncMock()
        c.stop = AsyncMock()
        return c

    with patch("asr_mcp.asr_to_terminal.TerminalTyper", return_value=typer), \
         patch("asr_mcp.asr_to_terminal.AsrResourceClient", return_value=_mock_client()), \
         patch("asr_mcp.asr_to_terminal.EndOfUtteranceDetector", return_value=mock_detector):
        atr = AsrToTerminal(mode="timeout", end_of_speech_timeout_s=2.0)
        await atr.start()
        try:
            await asyncio.wait_for(entered.wait(), timeout=2.0)
        finally:
            blocked.set()
            await atr.stop()

    typer.send_enter.assert_called()


@pytest.mark.asyncio
async def test_start_stop_manages_session_loop_task():
    """start() launches _session_loop_task; stop() cancels and awaits it."""
    typer = _typer_mock()

    blocked = asyncio.Event()

    async def fake_wait():
        await blocked.wait()

    mock_detector = AsyncMock()
    mock_detector.wait = fake_wait
    mock_detector.on_result = AsyncMock()

    def _mock_client():
        c = MagicMock()
        c.start = AsyncMock()
        c.stop = AsyncMock()
        return c

    with patch("asr_mcp.asr_to_terminal.TerminalTyper", return_value=typer), \
         patch("asr_mcp.asr_to_terminal.AsrResourceClient", return_value=_mock_client()), \
         patch("asr_mcp.asr_to_terminal.EndOfUtteranceDetector", return_value=mock_detector):
        atr = AsrToTerminal()
        assert atr._session_loop_task is None
        await atr.start()
        assert atr._session_loop_task is not None
        assert not atr._session_loop_task.done()
        await atr.stop()
        assert atr._session_loop_task is None
