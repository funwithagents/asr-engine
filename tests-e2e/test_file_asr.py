"""End-to-end tests: FileAudioSource → ASREngine → MCP server → MCP client."""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from asr_mcp.client import AsrMcpClient, McpToolClient

from helpers import load_api_key, start_mcp_server, stop_mcp_server

_FIXTURE_WAV = Path(__file__).parent / "fixtures" / "sample.wav"
_FIXTURE_SUBMIT_WAV = Path(__file__).parent / "fixtures" / "sample_submit.wav"
_EXPECTED = "the sky is blue"


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()


async def _run_e2e(
    module_type: str,
    module_config: dict,
    port: int,
    trailing_silence_s: float = 0.0,
) -> None:
    proc, config_path = await start_mcp_server(
        _FIXTURE_WAV, module_type, module_config, port, trailing_silence_s
    )

    final_event = asyncio.Event()
    last_final_transcript: list[str] = []

    async def _on_event(payload: dict) -> None:
        if payload.get("transcript") and payload.get("is_final"):
            last_final_transcript.clear()
            last_final_transcript.append(payload["transcript"])
            final_event.set()

    server_url = f"http://127.0.0.1:{port}/mcp"
    client = AsrMcpClient(server_url, _on_event)

    try:
        await client.start()
        await asyncio.wait_for(final_event.wait(), timeout=30.0)
    finally:
        await client.stop()
        await stop_mcp_server(proc, config_path)

    assert len(last_final_transcript) > 0, "No final result received"
    assert _normalize(last_final_transcript[0]) == _normalize(_EXPECTED), (
        f"Expected transcript {_EXPECTED!r}, got {last_final_transcript[0]!r}"
    )


@pytest.mark.asyncio
async def test_e2e_deepgram_v1() -> None:
    api_key = load_api_key()
    await _run_e2e(
        "deepgram_v1",
        {"api_key": api_key, "model": "nova-3"},
        port=18001,
    )


@pytest.mark.asyncio
async def test_e2e_deepgram_v2() -> None:
    api_key = load_api_key()
    await _run_e2e(
        "deepgram_v2",
        {"api_key": api_key, "model": "flux-general-en"},
        port=18002,
        trailing_silence_s=6.0,
    )


# ---------------------------------------------------------------------------
# listen tool — trigger_word and timeout modes
# ---------------------------------------------------------------------------


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
        listen_config={"end_of_utterance_mode": "trigger_word", "trigger_words": ["validate"]},
    )
    try:
        client = McpToolClient(f"http://127.0.0.1:18003/mcp")
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
async def test_e2e_listen_timeout() -> None:
    """listen tool with timeout mode: ends after silence, returns full transcript."""
    api_key = load_api_key()
    proc, config_path = await start_mcp_server(
        _FIXTURE_WAV,
        "deepgram_v1",
        {"api_key": api_key, "model": "nova-3"},
        port=18004,
        engine_config={"auto_start": False},
        listen_config={"end_of_utterance_mode": "timeout", "end_of_speech_timeout_s": 2.0},
    )
    try:
        client = McpToolClient(f"http://127.0.0.1:18004/mcp")
        result = await asyncio.wait_for(client.call_tool("listen"), timeout=30.0)
    finally:
        await stop_mcp_server(proc, config_path)

    assert result["end_reason"] == "end_of_speech_timeout"
    assert _normalize(result["transcript"]) == _normalize("the sky is blue")
