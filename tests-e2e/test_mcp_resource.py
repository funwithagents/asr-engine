"""End-to-end: FileAudioSource → ASREngine → MCP server → AsrResourceClient.

Verifies the resource path (asr://utterance) surfaces a final transcript. This is
module-agnostic plumbing, so it runs on the default module (see ``default_module``);
per-module backend behavior is covered in ``test_engine_modules.py``.
"""

from __future__ import annotations

import asyncio

import pytest
from helpers import (
    FIXTURE_BLUE,
    FIXTURE_BLUE_VALIDATE,
    default_module,
    normalize_transcript,
    start_mcp_server,
    stop_mcp_server,
)

from examples.mcp_client.resource_client import AsrResourceClient


@pytest.mark.asyncio
async def test_resource_emits_final_transcript() -> None:
    module_type, module_config = default_module()
    proc, config_path = await start_mcp_server(
        FIXTURE_BLUE, module_type, module_config, port=18001, trailing_silence_s=3.0
    )

    final_event = asyncio.Event()
    last_final_transcript: list[str] = []

    async def _on_event(payload: dict) -> None:
        if payload.get("transcript") and payload.get("is_final"):
            last_final_transcript.clear()
            last_final_transcript.append(payload["transcript"])
            final_event.set()

    client = AsrResourceClient("http://127.0.0.1:18001/mcp", _on_event)

    try:
        await client.start()
        await asyncio.wait_for(final_event.wait(), timeout=30.0)
    finally:
        await client.stop()
        await stop_mcp_server(proc, config_path)

    assert len(last_final_transcript) > 0, "No final result received"
    assert "the sky is blue" in normalize_transcript(last_final_transcript[0])


@pytest.mark.asyncio
async def test_auto_start_dictation_aggregates_segment() -> None:
    """auto_start_dictation makes the asr://segment stream aggregate from startup.

    The server begins in a persistent trigger_word dictation (before the file
    plays), so the segment closes with end_reason 'trigger_word', excluding the
    trigger utterance.
    """
    module_type, module_config = default_module()
    proc, config_path = await start_mcp_server(
        FIXTURE_BLUE_VALIDATE,
        module_type,
        module_config,
        port=18002,
        trailing_silence_s=2.0,
        engine_overrides={
            "auto_start_dictation": True,
            "dictation_default_segmentation_mode": "trigger_word",
            "segmentation": {"trigger_words": ["validate"]},
        },
    )

    final_event = asyncio.Event()
    closed: list[dict] = []

    async def _on_event(payload: dict) -> None:
        if payload.get("is_final") and payload.get("end_reason") == "trigger_word":
            closed.append(payload)
            final_event.set()

    client = AsrResourceClient(
        "http://127.0.0.1:18002/mcp", _on_event, resource_uri="asr://segment"
    )

    try:
        await client.start()
        await asyncio.wait_for(final_event.wait(), timeout=30.0)
    finally:
        await client.stop()
        await stop_mcp_server(proc, config_path)

    assert closed, "expected a trigger_word segment on asr://segment"
    assert "validate" not in normalize_transcript(closed[-1]["transcript"])
