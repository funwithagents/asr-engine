"""End-to-end tests: FileAudioSource → ASREngine → MCP server → MCP client."""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest
import uvicorn

from asr_mcp.audio import FileAudioSource
from asr_mcp.client import AsrMcpClient
from asr_mcp.config import ASRConfig, AudioConfig
from asr_mcp.engine import ASREngine
from asr_mcp.server import create_mcp_server

_FIXTURE_WAV = Path(__file__).parent / "fixtures" / "sample.wav"
_EXPECTED = "the sky is blue"


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()


def _load_api_key() -> str:
    config_path = Path(__file__).parent.parent / "config.json"
    with open(config_path) as f:
        data = json.load(f)
    return data["asr"]["api_key"]


async def _run_e2e(
    module_type: str,
    module_config: dict,
    port: int,
    trailing_silence_s: float = 0.0,
) -> None:
    audio_source = FileAudioSource(_FIXTURE_WAV, trailing_silence_s=trailing_silence_s)
    audio_config = AudioConfig(device=None)
    asr_config = ASRConfig(type=module_type, extra=module_config)

    async def _noop(result):
        pass

    engine = ASREngine(audio_config, asr_config, _noop, audio_source=audio_source)
    mcp = create_mcp_server(engine)

    starlette_app = mcp.streamable_http_app()
    uv_config = uvicorn.Config(
        starlette_app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
    )
    server = uvicorn.Server(uv_config)
    server_task = asyncio.create_task(server.serve())

    while not server.started:
        await asyncio.sleep(0.05)

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
        await engine.start()
        await client.start()
        await asyncio.wait_for(final_event.wait(), timeout=30.0)
    finally:
        await client.stop()
        await engine.stop()
        server.should_exit = True
        try:
            await asyncio.wait_for(server_task, timeout=5.0)
        except asyncio.TimeoutError:
            server_task.cancel()
            await asyncio.gather(server_task, return_exceptions=True)

    assert len(last_final_transcript) > 0, "No final result received"
    assert _normalize(last_final_transcript[0]) == _normalize(_EXPECTED), (
        f"Expected transcript {_EXPECTED!r}, got {last_final_transcript[0]!r}"
    )


@pytest.mark.asyncio
async def test_e2e_deepgram_v1() -> None:
    api_key = _load_api_key()
    await _run_e2e(
        "deepgram_v1",
        {"api_key": api_key, "model": "nova-3"},
        port=18001,
    )


@pytest.mark.asyncio
async def test_e2e_deepgram_v2() -> None:
    api_key = _load_api_key()
    await _run_e2e(
        "deepgram_v2",
        {"api_key": api_key, "model": "flux-general-en"},
        port=18002,
        trailing_silence_s=6.0,
    )
