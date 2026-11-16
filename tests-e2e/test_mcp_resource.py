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
    script_blue,
    script_blue_validate,
    start_mcp_server,
    stop_mcp_server,
)

from examples.mcp_client.resource_client import AsrResourceClient


@pytest.mark.asyncio
async def test_resource_emits_final_transcript() -> None:
    # The server auto-starts before the client subscribes: script into the tail.
    module_type, module_config = default_module(script_blue(delay_s=2.0))
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
    assert last_final_transcript == ["the sky is blue"]


@pytest.mark.asyncio
async def test_a_file_at_another_rate_than_the_module_needs_no_audio_config() -> None:
    """The server plays a 44.1 kHz MP3 into a module fed 16 kHz, with no rate in
    ``engine.audio``: the file's rate is read from the file, and the engine
    converts (specs/format-conversion.md).

    The fake module's clock is the audio it receives, counted at the 16 kHz it is
    told it gets. The final is scripted 3.5 s in, near the end of the ~4.3 s of
    audio fed, so it does not come out if the module is handed less audio than
    that. Whether converted speech is still recognized is a live backend's to show
    (``test_engine_modules_converted``).
    """
    module_type, module_config = default_module(script_blue(delay_s=2.3))
    proc, config_path = await start_mcp_server(
        FIXTURE_BLUE,
        module_type,
        {**module_config, "sample_rate": 16000},
        port=18007,
        trailing_silence_s=3.0,
        audio_format=None,
    )

    final_event = asyncio.Event()
    finals: list[str] = []

    async def _on_event(payload: dict) -> None:
        if payload.get("transcript") and payload.get("is_final"):
            finals.append(payload["transcript"])
            final_event.set()

    client = AsrResourceClient("http://127.0.0.1:18007/mcp", _on_event)
    try:
        await client.start()
        await asyncio.wait_for(final_event.wait(), timeout=30.0)
    finally:
        await client.stop()
        await stop_mcp_server(proc, config_path)

    assert finals == ["the sky is blue"]


@pytest.mark.asyncio
async def test_auto_start_dictation_aggregates_segment() -> None:
    """auto_start_dictation makes the asr://segment stream aggregate from startup.

    The server begins in a persistent trigger_word dictation (before the file
    plays), so the segment closes with end_reason 'trigger_word', excluding the
    trigger utterance.
    """
    module_type, module_config = default_module(script_blue_validate(delay_s=1.5))
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
    assert closed[-1]["transcript"] == "the sky is blue"
