"""End-to-end tests: FileAudioSource → ASREngine → MCP server → MCP client."""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import mcp.types as types
import pytest
import uvicorn
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from pydantic import AnyUrl

from asr_mcp.audio import FileAudioSource
from asr_mcp.config import ASRConfig, AudioConfig
from asr_mcp.engine import ASREngine
from asr_mcp.server import create_mcp_server

_FIXTURE_WAV = Path(__file__).parent / "fixtures" / "sample.wav"
_RESOURCE_URI = "asr://result"
_EXPECTED = "the sky is blue"


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()


def _load_api_key() -> str:
    config_path = Path(__file__).parent.parent.parent / "config.json"
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

    # Wait for uvicorn to be ready
    while not server.started:
        await asyncio.sleep(0.05)

    final_event = asyncio.Event()
    last_final_transcript: list[str] = []
    final_payloads: list[dict] = []
    session_holder: list[ClientSession | None] = [None]

    async def _on_message(
        msg: types.RequestResponder | types.ServerNotification | Exception,
    ) -> None:
        if not isinstance(msg, types.ServerNotification):
            return
        if not isinstance(msg.root, types.ResourceUpdatedNotification):
            return
        session = session_holder[0]
        if session is None:
            return
        asyncio.create_task(_fetch_result(session))

    async def _fetch_result(session: ClientSession) -> None:
        try:
            result = await session.read_resource(AnyUrl(_RESOURCE_URI))
            for content in result.contents:
                if hasattr(content, "text"):
                    payload = json.loads(content.text)
                    if payload.get("transcript"):
                        final_payloads.append(payload)
                        if payload.get("is_final"):
                            last_final_transcript.clear()
                            last_final_transcript.append(payload["transcript"])
                            final_event.set()
        except Exception:
            pass

    server_url = f"http://127.0.0.1:{port}/mcp"

    async def _client() -> None:
        async with streamable_http_client(server_url) as (rs, ws, _):
            async with ClientSession(rs, ws, message_handler=_on_message) as session:
                session_holder[0] = session
                await session.initialize()
                await session.subscribe_resource(AnyUrl(_RESOURCE_URI))
                await asyncio.wait_for(final_event.wait(), timeout=28.0)
                try:
                    await session.unsubscribe_resource(AnyUrl(_RESOURCE_URI))
                except Exception:
                    pass

    try:
        await engine.start()
        await asyncio.wait_for(_client(), timeout=30.0)
    finally:
        await engine.stop()
        server.should_exit = True
        await server_task

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
