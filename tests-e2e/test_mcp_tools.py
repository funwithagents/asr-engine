"""End-to-end: McpToolClient — the listen tool (trigger_word and timeout modes).

The tool adapter and progress-notification plumbing are module-agnostic, so these
run on the default module (see ``default_module``); per-module backend behavior is
covered in ``test_engine_modules.py``.
"""

from __future__ import annotations

import asyncio

import pytest
from helpers import (
    FIXTURE_BLUE,
    FIXTURE_BLUE_VALIDATE,
    default_module,
    script_blue,
    script_blue_validate,
    start_mcp_server,
    stop_mcp_server,
)
from mcp_tool_client import McpToolClient


@pytest.mark.asyncio
async def test_listen_trigger_word() -> None:
    """listen tool with trigger_word mode: ends on 'validate', transcript excludes trigger utterance."""
    module_type, module_config = default_module(script_blue_validate())
    proc, config_path = await start_mcp_server(
        FIXTURE_BLUE_VALIDATE,
        module_type,
        module_config,
        port=18003,
        trailing_silence_s=2.0,
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

    # The trigger utterance closes the segment but is excluded from its transcript.
    assert result == {"transcript": "the sky is blue", "end_reason": "trigger_word"}


@pytest.mark.asyncio
async def test_listen_streaming() -> None:
    """listen tool streams progress notifications for each committed final."""
    module_type, module_config = default_module(script_blue())
    proc, config_path = await start_mcp_server(
        FIXTURE_BLUE,
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

    # One committed final → one progress notification carrying the transcript.
    assert received_messages == ["the sky is blue"]
    assert result["transcript"] == "the sky is blue"


@pytest.mark.asyncio
async def test_listen_timeout() -> None:
    """listen tool with timeout mode: ends after silence, returns full transcript."""
    module_type, module_config = default_module(script_blue())
    proc, config_path = await start_mcp_server(
        FIXTURE_BLUE,
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

    assert result == {
        "transcript": "the sky is blue",
        "end_reason": "end_of_speech_timeout",
    }


@pytest.mark.asyncio
async def test_dictation_tool_control() -> None:
    """is_dictation_running / start_dictation / stop_dictation control plane."""
    module_type, module_config = default_module(script_blue())
    proc, config_path = await start_mcp_server(
        FIXTURE_BLUE,
        module_type,
        module_config,
        port=18006,
        engine_overrides={
            "auto_start": True,
            "auto_start_dictation": False,
            # Long timeouts so a timeout-mode dictation stays open during the test.
            "segmentation": {
                "end_of_speech_timeout_s": 60.0,
                "initial_silence_timeout_s": 60.0,
            },
        },
    )
    try:
        client = McpToolClient("http://127.0.0.1:18006/mcp")

        assert await client.call_tool("is_dictation_running") == {
            "dictating": False,
            "segmentation_mode": "utterance",
        }

        started = await client.call_tool(
            "start_dictation",
            {"end_on_final_segment": False, "segmentation_mode": "timeout"},
        )
        assert started == {
            "status": "dictating",
            "mode": "timeout",
            "end_on_final_segment": False,
        }
        assert await client.call_tool("is_dictation_running") == {
            "dictating": True,
            "segmentation_mode": "timeout",
        }

        assert await client.call_tool("stop_dictation") == {
            "status": "dictation_stopped"
        }
        assert await client.call_tool("is_dictation_running") == {
            "dictating": False,
            "segmentation_mode": "utterance",
        }
    finally:
        await stop_mcp_server(proc, config_path)
