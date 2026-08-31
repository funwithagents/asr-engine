"""End-to-end tests: McpToolClient — listen tool (trigger_word and timeout modes)."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest
from helpers import load_api_key, start_mcp_server, stop_mcp_server

from asr_mcp.tool_client import McpToolClient

_FIXTURE_WAV = Path(__file__).parent / "fixtures" / "sample.wav"
_FIXTURE_SUBMIT_WAV = Path(__file__).parent / "fixtures" / "sample_submit.wav"


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()


@pytest.mark.asyncio
async def test_e2e_listen_trigger_word() -> None:
    """listen tool with trigger_word mode: ends on 'validate', transcript excludes trigger utterance."""
    api_key = load_api_key()
    proc, config_path = await start_mcp_server(
        _FIXTURE_SUBMIT_WAV,
        "deepgram_v1",
        {"api_key": api_key, "model": "nova-3"},
        port=18003,
        engine_config={"auto_start": False},
        listen_config={
            "end_of_utterance_mode": "trigger_word",
            "trigger_words": ["validate"],
            "sound_feedback": True,
        },
    )
    try:
        client = McpToolClient("http://127.0.0.1:18003/mcp")
        result = await asyncio.wait_for(client.call_tool("listen"), timeout=30.0)
    finally:
        await stop_mcp_server(proc, config_path)

    assert result["end_reason"] == "trigger_word"
    # The trigger utterance is never included in the transcript.
    # Depending on how the ASR model segments "the sky is blue validate", the
    # pre-trigger finals may or may not be present (nova-3 often returns the
    # whole sentence as a single final, making the transcript empty).
    assert "validate" not in result["transcript"].lower()


@pytest.mark.asyncio
async def test_e2e_listen_streaming() -> None:
    """listen tool streams progress notifications for each committed final."""
    api_key = load_api_key()
    proc, config_path = await start_mcp_server(
        _FIXTURE_WAV,
        "deepgram_v1",
        {"api_key": api_key, "model": "nova-3"},
        port=18005,
        engine_config={"auto_start": False},
        listen_config={
            "end_of_utterance_mode": "timeout",
            "end_of_speech_timeout_s": 2.0,
            "sound_feedback": True,
        },
    )
    try:
        received_messages: list[str] = []

        async def _on_progress(
            progress: float, total: float | None, message: str | None
        ) -> None:
            if message is not None:
                received_messages.append(message)

        client = McpToolClient("http://127.0.0.1:18005/mcp")
        result = await asyncio.wait_for(
            client.call_tool("listen", progress_callback=_on_progress), timeout=30.0
        )
    finally:
        await stop_mcp_server(proc, config_path)

    assert len(received_messages) >= 1, "Expected at least one progress notification"
    assert _normalize(received_messages[-1]) == _normalize(result["transcript"])


@pytest.mark.asyncio
async def test_e2e_listen_timeout() -> None:
    """listen tool with timeout mode: ends after silence, returns full transcript."""
    api_key = load_api_key()
    proc, config_path = await start_mcp_server(
        _FIXTURE_WAV,
        "deepgram_v1",
        {"api_key": api_key, "model": "nova-3"},
        port=18004,
        engine_config={"auto_start": False},
        listen_config={
            "end_of_utterance_mode": "timeout",
            "end_of_speech_timeout_s": 2.0,
            "sound_feedback": True,
        },
    )
    try:
        client = McpToolClient("http://127.0.0.1:18004/mcp")
        result = await asyncio.wait_for(client.call_tool("listen"), timeout=30.0)
    finally:
        await stop_mcp_server(proc, config_path)

    assert result["end_reason"] == "end_of_speech_timeout"
    assert _normalize(result["transcript"]) == _normalize("the sky is blue")
