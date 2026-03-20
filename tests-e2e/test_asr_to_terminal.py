"""End-to-end tests: FileAudioSource → ASREngine → MCP server → AsrToTerminal.

Requires:
- xdotool installed
- A live X11 display (DISPLAY set)
- config.json with valid Deepgram API key
"""
from __future__ import annotations

import asyncio
import re
import subprocess
from pathlib import Path

import pytest

from asr_mcp.asr_to_terminal import AsrToTerminal

from helpers import load_api_key, start_mcp_server, stop_mcp_server

_FIXTURE_WAV = Path(__file__).parent / "fixtures" / "sample.wav"
_FIXTURE_SUBMIT_WAV = Path(__file__).parent / "fixtures" / "sample_submit.wav"


@pytest.mark.asyncio
async def test_e2e_terminal_typing() -> None:
    """Feed sample.wav; expect typed text to appear in xterm captured via cat."""
    api_key = load_api_key()
    port = 18101
    output_file = "/tmp/asr_e2e_typing.txt"

    xterm_proc = subprocess.Popen(
        ["xterm", "-e", f"cat > {output_file}"],
    )
    try:
        # Wait for the window
        window_id = subprocess.check_output(
            ["xdotool", "search", "--sync", "--pid", str(xterm_proc.pid)],
            timeout=10,
        ).decode().strip().splitlines()[-1]
        subprocess.run(["xdotool", "windowfocus", window_id], check=True)
        await asyncio.sleep(0.3)  # let focus settle

        proc, config_path = await start_mcp_server(
            _FIXTURE_WAV, "deepgram_v1", {"api_key": api_key, "model": "nova-3"}, port
        )

        final_event = asyncio.Event()

        atr = AsrToTerminal(
            server_url=f"http://127.0.0.1:{port}/mcp",
            trigger_words=["__no_submit__"],  # disable submit to just type text
            display_server="x11",
        )
        # Wrap _on_event to detect final
        original_on_event = atr._on_event

        async def _tracking_on_event(payload: dict) -> None:
            await original_on_event(payload)
            if payload.get("is_final") and payload.get("transcript"):
                final_event.set()

        atr._on_event = _tracking_on_event
        atr._client._subscriber._on_event = _tracking_on_event  # type: ignore[attr-defined]

        try:
            await atr.start()
            await asyncio.wait_for(final_event.wait(), timeout=30.0)
            await asyncio.sleep(0.5)  # let xdotool finish
            # Flush cat with Ctrl-D
            subprocess.run(
                ["xdotool", "key", "--clearmodifiers", "ctrl+d"],
                check=True,
            )
            await asyncio.sleep(0.3)
        finally:
            await atr.stop()
            await stop_mcp_server(proc, config_path)

        result = Path(output_file).read_text().strip().lower()
        normalized = re.sub(r"[^a-z0-9 ]", "", result)
        assert "the sky is blue" in normalized, (
            f"Expected 'the sky is blue' in typed text, got: {result!r}"
        )
    finally:
        xterm_proc.terminate()
        xterm_proc.wait()


@pytest.mark.asyncio
async def test_e2e_terminal_submit() -> None:
    """Feed sample_submit.wav; expect Enter fired (no text committed)."""
    api_key = load_api_key()
    port = 18102
    output_file = "/tmp/asr_e2e_submit.txt"

    xterm_proc = subprocess.Popen(
        ["xterm", "-e", f"bash -c 'read line; echo GOT:$line > {output_file}'"],
    )
    try:
        # Wait for the window
        window_id = subprocess.check_output(
            ["xdotool", "search", "--sync", "--pid", str(xterm_proc.pid)],
            timeout=10,
        ).decode().strip().splitlines()[-1]
        subprocess.run(["xdotool", "windowfocus", window_id], check=True)
        await asyncio.sleep(0.3)  # let focus settle

        proc, config_path = await start_mcp_server(
            _FIXTURE_SUBMIT_WAV, "deepgram_v1", {"api_key": api_key, "model": "nova-3"}, port,
            trailing_silence_s=2.0,
        )

        final_event = asyncio.Event()

        atr = AsrToTerminal(
            server_url=f"http://127.0.0.1:{port}/mcp",
            trigger_words=["validate"],
            display_server="x11",
        )
        original_on_event = atr._on_event

        async def _tracking_on_event(payload: dict) -> None:
            await original_on_event(payload)
            if payload.get("is_final") and payload.get("transcript"):
                final_event.set()

        atr._on_event = _tracking_on_event
        atr._client._subscriber._on_event = _tracking_on_event  # type: ignore[attr-defined]

        try:
            await atr.start()
            await asyncio.wait_for(final_event.wait(), timeout=30.0)
            await asyncio.sleep(1.0)  # let xdotool and bash finish
        finally:
            await atr.stop()
            await stop_mcp_server(proc, config_path)

        result = Path(output_file).read_text().strip()
        assert result.startswith("GOT:"), (
            f"Expected 'GOT:' prefix (Enter fired, no text), got: {result!r}"
        )
    finally:
        xterm_proc.terminate()
        xterm_proc.wait()


@pytest.mark.asyncio
async def test_e2e_asr_to_terminal_timeout() -> None:
    """Feed sample.wav in timeout mode; expect text typed then Enter sent after EOS timeout."""
    api_key = load_api_key()
    port = 18103
    output_file = "/tmp/asr_e2e_timeout.txt"

    xterm_proc = subprocess.Popen(
        ["xterm", "-e", f"bash -c 'read line; echo GOT:$line > {output_file}'"],
    )
    try:
        # Wait for the window
        window_id = subprocess.check_output(
            ["xdotool", "search", "--sync", "--pid", str(xterm_proc.pid)],
            timeout=10,
        ).decode().strip().splitlines()[-1]
        subprocess.run(["xdotool", "windowfocus", window_id], check=True)
        await asyncio.sleep(0.3)  # let focus settle

        proc, config_path = await start_mcp_server(
            _FIXTURE_WAV, "deepgram_v1", {"api_key": api_key, "model": "nova-3"}, port,
            trailing_silence_s=3.0,
        )

        final_event = asyncio.Event()

        atr = AsrToTerminal(
            server_url=f"http://127.0.0.1:{port}/mcp",
            display_server="x11",
            mode="timeout",
            end_of_speech_timeout_s=2.0,
            initial_silence_timeout_s=15.0,
        )
        original_on_event = atr._on_event

        async def _tracking_on_event(payload: dict) -> None:
            await original_on_event(payload)
            if payload.get("is_final") and payload.get("transcript"):
                final_event.set()

        atr._on_event = _tracking_on_event
        atr._client._subscriber._on_event = _tracking_on_event  # type: ignore[attr-defined]

        try:
            await atr.start()
            await asyncio.wait_for(final_event.wait(), timeout=30.0)
            # Wait for EOS timeout + Enter to be sent
            await asyncio.sleep(3.5)
        finally:
            await atr.stop()
            await stop_mcp_server(proc, config_path)

        result = Path(output_file).read_text().strip().lower()
        assert result.startswith("got:"), (
            f"Expected 'GOT:' prefix (Enter fired by timeout), got: {result!r}"
        )
        normalized = re.sub(r"[^a-z0-9 ]", "", result[4:])
        assert "the sky is blue" in normalized, (
            f"Expected 'the sky is blue' in output, got: {result!r}"
        )
    finally:
        xterm_proc.terminate()
        xterm_proc.wait()
