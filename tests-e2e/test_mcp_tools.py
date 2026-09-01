"""End-to-end: McpToolClient — the listen tool (trigger_word and timeout modes).

The tool adapter and progress-notification plumbing are module-agnostic, so these
run on a single provider (see ``default_provider``); per-module backend behavior is
covered in ``test_engine_modules.py``.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest
from helpers import default_provider, start_mcp_server, stop_mcp_server
from mcp_tool_client import McpToolClient

_FIXTURE_WAV = Path(__file__).parent / "fixtures" / "sample.wav"
_FIXTURE_SUBMIT_WAV = Path(__file__).parent / "fixtures" / "sample_submit.wav"


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()


@pytest.mark.asyncio
async def test_listen_trigger_word() -> None:
    """listen tool with trigger_word mode: ends on 'validate', transcript excludes trigger utterance."""
    module_type, module_config = default_provider()
    proc, config_path = await start_mcp_server(
        _FIXTURE_SUBMIT_WAV,
        module_type,
        module_config,
        port=18003,
        engine_overrides={
            "auto_start": False,
            "listen_default_segmentation_mode": "trigger_word",
            "segmentation": {"trigger_words": ["validate"]},
            "sound_feedback": {"enabled": True},
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
async def test_listen_streaming() -> None:
    """listen tool streams progress notifications for each committed final."""
    module_type, module_config = default_provider()
    proc, config_path = await start_mcp_server(
        _FIXTURE_WAV,
        module_type,
        module_config,
        port=18005,
        engine_overrides={
            "auto_start": False,
            "listen_default_segmentation_mode": "timeout",
            "segmentation": {"end_of_speech_timeout_s": 2.0},
            "sound_feedback": {"enabled": True},
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
async def test_listen_timeout() -> None:
    """listen tool with timeout mode: ends after silence, returns full transcript."""
    module_type, module_config = default_provider()
    proc, config_path = await start_mcp_server(
        _FIXTURE_WAV,
        module_type,
        module_config,
        port=18004,
        engine_overrides={
            "auto_start": False,
            "listen_default_segmentation_mode": "timeout",
            "segmentation": {"end_of_speech_timeout_s": 2.0},
            "sound_feedback": {"enabled": True},
        },
    )
    try:
        client = McpToolClient("http://127.0.0.1:18004/mcp")
        result = await asyncio.wait_for(client.call_tool("listen"), timeout=30.0)
    finally:
        await stop_mcp_server(proc, config_path)

    assert result["end_reason"] == "end_of_speech_timeout"
    assert _normalize(result["transcript"]) == _normalize("the sky is blue")
