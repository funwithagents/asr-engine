"""Unit tests for terminal_typer.py and asr_to_terminal.py."""
from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from asr_mcp.asr_to_terminal import AsrToTerminal
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
# AsrToTerminal._contains_submit_word
# ---------------------------------------------------------------------------


class TestContainsSubmitWord:
    def setup_method(self):
        with patch("asr_mcp.asr_to_terminal.TerminalTyper"), \
             patch("asr_mcp.asr_to_terminal.AsrMcpClient"):
            self.atr = AsrToTerminal(submit_words=["validate", "submit"])

    def test_exact_match(self):
        assert self.atr._contains_submit_word("validate") is True

    def test_case_insensitive(self):
        assert self.atr._contains_submit_word("SUBMIT now") is True

    def test_substring_match(self):
        assert self.atr._contains_submit_word("please validate this") is True

    def test_no_match(self):
        assert self.atr._contains_submit_word("the sky is blue") is False

    def test_partial_word_no_false_positive(self):
        # "go" is not in our custom list so should not match
        assert self.atr._contains_submit_word("good morning") is False


# ---------------------------------------------------------------------------
# AsrToTerminal state machine
# ---------------------------------------------------------------------------


def _make_atr(typer_mock):
    """Build AsrToTerminal with a mocked TerminalTyper and AsrMcpClient."""
    with patch("asr_mcp.asr_to_terminal.TerminalTyper", return_value=typer_mock), \
         patch("asr_mcp.asr_to_terminal.AsrMcpClient"):
        atr = AsrToTerminal(submit_words=["validate"])
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
async def test_second_interim_backspaces_previous():
    """Second interim: backspace previous pending length, type new, update _pending."""
    typer = _typer_mock()
    atr = _make_atr(typer)
    atr._pending = "hello"

    await atr._on_event({"transcript": "hello world", "is_final": False})

    typer.backspace.assert_called_once_with(5)
    typer.type_text.assert_called_once_with("hello world")
    assert atr._pending == "hello world"


@pytest.mark.asyncio
async def test_final_no_submit_word_commits_text():
    """Final without submit word: backspace pending, type text, reset _pending."""
    typer = _typer_mock()
    atr = _make_atr(typer)
    atr._pending = "hello"

    await atr._on_event({"transcript": "hello world", "is_final": True})

    typer.backspace.assert_called_once_with(5)
    typer.type_text.assert_called_once_with("hello world")
    typer.send_enter.assert_not_called()
    assert atr._pending == ""


@pytest.mark.asyncio
async def test_final_submit_word_sends_enter():
    """Final with submit word: backspace pending, Enter sent, nothing typed, _pending reset."""
    typer = _typer_mock()
    atr = _make_atr(typer)
    atr._pending = "hello"

    await atr._on_event({"transcript": "the sky is blue validate", "is_final": True})

    typer.backspace.assert_called_once_with(5)
    typer.send_enter.assert_called_once()
    typer.type_text.assert_not_called()
    assert atr._pending == ""
