"""Unit tests for terminal_typer.py and asr_to_terminal.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from asr_engine.asr_to_terminal import AsrToTerminal
from asr_engine.terminal_typer import TerminalTyper

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
# AsrToTerminal — segment-driven typing
# ---------------------------------------------------------------------------


def _make_atr(typer_mock):
    """Build AsrToTerminal with a mocked TerminalTyper and AsrResourceClient."""
    with (
        patch("asr_engine.asr_to_terminal.TerminalTyper", return_value=typer_mock),
        patch("asr_engine.asr_to_terminal.AsrResourceClient"),
    ):
        atr = AsrToTerminal()
    return atr


def _typer_mock():
    m = MagicMock()
    m.backspace = AsyncMock()
    m.type_text = AsyncMock()
    m.send_enter = AsyncMock()
    return m


@pytest.mark.asyncio
async def test_first_open_segment_no_backspace():
    """First open segment: no backspace (nothing pending), text typed, _pending set."""
    typer = _typer_mock()
    atr = _make_atr(typer)

    await atr._on_segment({"transcript": "hello", "is_final": False})

    typer.backspace.assert_called_once_with(0)
    typer.type_text.assert_called_once_with("hello")
    typer.send_enter.assert_not_called()
    assert atr._pending == "hello"


@pytest.mark.asyncio
async def test_growing_segment_types_suffix():
    """A growing segment types only the changed suffix (shared prefix kept)."""
    typer = _typer_mock()
    atr = _make_atr(typer)
    atr._pending = "hello"

    await atr._on_segment({"transcript": "hello world", "is_final": False})

    typer.backspace.assert_called_once_with(0)  # shared prefix "hello"
    typer.type_text.assert_called_once_with(" world")
    typer.send_enter.assert_not_called()
    assert atr._pending == "hello world"


@pytest.mark.asyncio
async def test_closed_segment_types_then_sends_enter_and_resets():
    """A closed segment types the diff, sends Enter, and clears _pending."""
    typer = _typer_mock()
    atr = _make_atr(typer)
    atr._pending = "hello"

    await atr._on_segment({"transcript": "hello world", "is_final": True})

    typer.backspace.assert_called_once_with(0)
    typer.type_text.assert_called_once_with(" world")
    typer.send_enter.assert_called_once()
    assert atr._pending == ""


@pytest.mark.asyncio
async def test_shrinking_transcript_backspaces_divergent_suffix():
    """When the new transcript diverges, backspace the non-common suffix."""
    typer = _typer_mock()
    atr = _make_atr(typer)
    atr._pending = "hello world"

    await atr._on_segment({"transcript": "hello there", "is_final": False})

    # common prefix "hello " (6 chars); erase "world" (5), type "there"
    typer.backspace.assert_called_once_with(5)
    typer.type_text.assert_called_once_with("there")
    assert atr._pending == "hello there"


@pytest.mark.asyncio
async def test_subscribes_to_segment_resource():
    """AsrToTerminal wires its resource client to asr://segment."""
    typer = _typer_mock()
    with (
        patch("asr_engine.asr_to_terminal.TerminalTyper", return_value=typer),
        patch("asr_engine.asr_to_terminal.AsrResourceClient") as MockClient,
    ):
        AsrToTerminal(server_url="http://x/mcp")

    _, kwargs = MockClient.call_args
    assert kwargs.get("resource_uri") == "asr://segment"


@pytest.mark.asyncio
async def test_start_stop_delegate_to_client():
    typer = _typer_mock()
    client = MagicMock()
    client.start = AsyncMock()
    client.stop = AsyncMock()
    with (
        patch("asr_engine.asr_to_terminal.TerminalTyper", return_value=typer),
        patch("asr_engine.asr_to_terminal.AsrResourceClient", return_value=client),
    ):
        atr = AsrToTerminal()
        await atr.start()
        await atr.stop()

    client.start.assert_awaited_once()
    client.stop.assert_awaited_once()
