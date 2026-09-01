"""End-to-end: FileAudioSource → ASREngine → MCP server → AsrResourceClient.

Verifies the resource path (asr://utterance) surfaces a final transcript. This is
module-agnostic plumbing, so it runs on a single provider (see ``default_provider``);
per-module backend behavior is covered in ``test_engine_modules.py``.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest
from helpers import default_provider, start_mcp_server, stop_mcp_server

from asr_engine.resource_client import AsrResourceClient

_FIXTURE_WAV = Path(__file__).parent / "fixtures" / "sample.wav"
_EXPECTED = "the sky is blue"


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()


@pytest.mark.asyncio
async def test_resource_emits_final_transcript() -> None:
    module_type, module_config = default_provider()
    proc, config_path = await start_mcp_server(
        _FIXTURE_WAV, module_type, module_config, port=18001, trailing_silence_s=3.0
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
    assert _normalize(last_final_transcript[0]) == _normalize(_EXPECTED), (
        f"Expected transcript {_EXPECTED!r}, got {last_final_transcript[0]!r}"
    )
