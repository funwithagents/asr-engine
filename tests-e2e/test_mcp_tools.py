"""End-to-end: McpToolClient — the listen tool (trigger_word and timeout modes).

The tool adapter and progress-notification plumbing are module-agnostic, so these
run on a single provider (see ``default_provider``); per-module backend behavior is
covered in ``test_engine_modules.py``.
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
from mcp_tool_client import McpToolClient


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()


def _error_messages(exc: BaseException) -> list[str]:
    """Flatten an exception (or anyio ExceptionGroup) into its message strings.

    McpToolClient raises a tool's error inside a task group, so it escapes wrapped
    in a (possibly nested) ExceptionGroup — flatten to assert on the message.
    """
    if isinstance(exc, BaseExceptionGroup):
        out: list[str] = []
        for sub in exc.exceptions:
            out.extend(_error_messages(sub))
        return out
    return [str(exc)]


@pytest.mark.asyncio
async def test_listen_trigger_word() -> None:
    """listen tool with trigger_word mode: ends on 'validate', transcript excludes trigger utterance."""
    module_type, module_config = default_provider()
    proc, config_path = await start_mcp_server(
        FIXTURE_BLUE_VALIDATE,
        module_type,
        module_config,
        port=18003,
        # Tail silence so Deepgram finalizes the "validate" utterance and the
        # trigger_word segment can close (the tight mp3 has little of its own).
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

    assert len(received_messages) >= 1, "Expected at least one progress notification"
    assert _normalize(received_messages[-1]) == _normalize(result["transcript"])


@pytest.mark.asyncio
async def test_listen_timeout() -> None:
    """listen tool with timeout mode: ends after silence, returns full transcript."""
    module_type, module_config = default_provider()
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

    assert result["end_reason"] == "end_of_speech_timeout"
    assert _normalize(result["transcript"]) == _normalize("the sky is blue")


@pytest.mark.asyncio
async def test_dictation_tool_control() -> None:
    """is_dictation_running / start_dictation / stop_dictation control plane."""
    module_type, module_config = default_provider()
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


@pytest.mark.asyncio
async def test_dictation_guard_errors() -> None:
    """start_dictation errors: already-in-progress, and engine-not-running."""
    module_type, module_config = default_provider()

    # (1) Second start_dictation while one is active raises.
    proc, config_path = await start_mcp_server(
        FIXTURE_BLUE,
        module_type,
        module_config,
        port=18007,
        engine_overrides={"auto_start": True, "auto_start_dictation": False},
    )
    try:
        client = McpToolClient("http://127.0.0.1:18007/mcp")
        # trigger_word with no matching word → the session stays open.
        await client.call_tool(
            "start_dictation",
            {"end_on_final_segment": False, "segmentation_mode": "trigger_word"},
        )
        with pytest.raises(BaseException) as exc_info:  # noqa: PT011,B017 — grouped below
            await client.call_tool("start_dictation", {"end_on_final_segment": False})
        assert any("already in progress" in m for m in _error_messages(exc_info.value))
    finally:
        await stop_mcp_server(proc, config_path)

    # (2) start_dictation while the engine is not running raises.
    proc, config_path = await start_mcp_server(
        FIXTURE_BLUE,
        module_type,
        module_config,
        port=18008,
        engine_overrides={"auto_start": False},
    )
    try:
        client = McpToolClient("http://127.0.0.1:18008/mcp")
        with pytest.raises(BaseException) as exc_info:  # noqa: PT011,B017 — grouped below
            await client.call_tool(
                "start_dictation", {"segmentation_mode": "trigger_word"}
            )
        assert any("ASR is not running" in m for m in _error_messages(exc_info.value))
    finally:
        await stop_mcp_server(proc, config_path)
