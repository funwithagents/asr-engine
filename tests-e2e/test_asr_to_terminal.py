"""End-to-end tests: FileAudioSource → ASREngine → MCP server → AsrToTerminal.

Segmentation is owned by the server (engine.segmentation_mode config); AsrToTerminal
just consumes the asr://segment resource. See specs/engine.md.

These tests drive the full live pipeline (real MCP server subprocess + live
Deepgram) but inject an in-memory ``RecordingTyper`` instead of doing real OS
keystroke injection — so they run anywhere (macOS/CI included) with no xterm,
xdotool, or X11 display. They still need a Deepgram API key (see helpers).
"""

from __future__ import annotations

import asyncio
import re

import pytest
from helpers import (
    FIXTURE_BLUE,
    FIXTURE_BLUE_VALIDATE,
    default_provider,
    start_mcp_server,
    stop_mcp_server,
)

from asr_engine.asr_to_terminal import AsrToTerminal


class RecordingTyper:
    """In-memory KeystrokeSink that models a terminal input line.

    Faithfully applies the same type/backspace/Enter operations a real terminal
    would receive, so tests can assert the resulting text and that Enter fired —
    without any GUI, subprocess, or OS permission.
    """

    def __init__(self) -> None:
        self.line = ""  # current (uncommitted) input line
        self.committed: list[str] = []  # lines committed by Enter

    async def type_text(self, text: str) -> None:
        self.line += text

    async def backspace(self, n: int) -> None:
        if n:
            self.line = self.line[:-n]

    async def send_enter(self) -> None:
        self.committed.append(self.line)
        self.line = ""


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()


async def _wait_until(predicate, timeout: float = 30.0) -> None:
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.1)
    raise TimeoutError("condition not met within timeout")


@pytest.mark.asyncio
async def test_terminal_typing() -> None:
    """Feed the mp3 with an impossible trigger word: text is typed, never committed."""
    module_type, module_config = default_provider()
    port = 18101

    proc, config_path = await start_mcp_server(
        FIXTURE_BLUE,
        module_type,
        module_config,
        port,
        # Impossible trigger word so the segment never closes → text only, no Enter.
        engine_overrides={
            "segmentation_mode": "trigger_word",
            "segmentation": {"trigger_words": ["__no_submit__"]},
        },
    )
    typer = RecordingTyper()
    atr = AsrToTerminal(server_url=f"http://127.0.0.1:{port}/mcp", typer=typer)
    try:
        await atr.start()
        await _wait_until(lambda: "the sky is blue" in _normalize(typer.line))
    finally:
        await atr.stop()
        await stop_mcp_server(proc, config_path)

    assert "the sky is blue" in _normalize(typer.line)
    # Segment never closed → Enter never fired.
    assert typer.committed == []


@pytest.mark.asyncio
async def test_terminal_submit() -> None:
    """Feed the validate mp3; expect the segment to close and Enter to fire."""
    module_type, module_config = default_provider()
    port = 18102

    proc, config_path = await start_mcp_server(
        FIXTURE_BLUE_VALIDATE,
        module_type,
        module_config,
        port,
        trailing_silence_s=2.0,
        engine_overrides={
            "segmentation_mode": "trigger_word",
            "segmentation": {"trigger_words": ["validate"]},
        },
    )
    typer = RecordingTyper()
    atr = AsrToTerminal(server_url=f"http://127.0.0.1:{port}/mcp", typer=typer)
    try:
        await atr.start()
        await _wait_until(lambda: len(typer.committed) >= 1)
    finally:
        await atr.stop()
        await stop_mcp_server(proc, config_path)

    # A committed line means the segment closed and Enter fired.
    assert len(typer.committed) >= 1
    # The trigger word "validate" fires the action, not the text.
    assert "validate" not in _normalize(typer.committed[-1])


@pytest.mark.asyncio
async def test_asr_to_terminal_timeout() -> None:
    """Feed the mp3 in timeout mode; expect text typed then Enter after EOS timeout."""
    module_type, module_config = default_provider()
    port = 18103

    proc, config_path = await start_mcp_server(
        FIXTURE_BLUE,
        module_type,
        module_config,
        port,
        trailing_silence_s=3.0,
        engine_overrides={
            "segmentation_mode": "timeout",
            "segmentation": {
                "end_of_speech_timeout_s": 2.0,
                "initial_silence_timeout_s": 15.0,
            },
        },
    )
    typer = RecordingTyper()
    atr = AsrToTerminal(server_url=f"http://127.0.0.1:{port}/mcp", typer=typer)
    try:
        await atr.start()
        await _wait_until(lambda: len(typer.committed) >= 1)
    finally:
        await atr.stop()
        await stop_mcp_server(proc, config_path)

    assert len(typer.committed) >= 1, "Expected Enter to fire on EOS timeout"
    assert "the sky is blue" in _normalize(typer.committed[-1])
