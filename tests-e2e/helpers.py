"""Shared helpers for e2e tests."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import uvicorn

from asr_mcp.audio import FileAudioSource
from asr_mcp.config import ASRConfig, AudioConfig
from asr_mcp.engine import ASREngine
from asr_mcp.server import create_mcp_server


def load_api_key() -> str:
    config_path = Path(__file__).parent.parent / "config.json"
    with open(config_path) as f:
        data = json.load(f)
    return data["asr"]["api_key"]


async def start_mcp_server(
    audio_source: FileAudioSource,
    module_type: str,
    module_config: dict,
    port: int,
    trailing_silence_s: float = 0.0,
) -> tuple:
    """Start ASREngine + MCP server, return (engine, server, server_task)."""
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

    return engine, server, server_task


async def stop_mcp_server(server, server_task) -> None:
    """Shut down an MCP server started with start_mcp_server."""
    server.should_exit = True
    try:
        await asyncio.wait_for(server_task, timeout=5.0)
    except asyncio.TimeoutError:
        server_task.cancel()
        await asyncio.gather(server_task, return_exceptions=True)
