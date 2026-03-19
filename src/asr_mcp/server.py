from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

import uvicorn
from mcp.server.fastmcp import FastMCP
from pydantic import AnyUrl

from asr_mcp.config import AppConfig
from asr_mcp.engine import ASREngine
from asr_mcp.modules.base import ASRResult

logger = logging.getLogger(__name__)

_RESOURCE_URI = "asr://result"


def create_mcp_server(engine: ASREngine) -> FastMCP:
    """Create the FastMCP server wired to *engine*.

    Side-effect: replaces ``engine._on_result`` with the MCP result callback
    so that ASR results flow into the resource state.
    """
    _current_result: dict[str, Any] = {
        "transcript": "",
        "is_final": False,
        "confidence": None,
        "timestamp": None,
    }
    _subscribed_sessions: list[Any] = []

    mcp = FastMCP("asr-mcp")

    # --- Subscribe / unsubscribe (lowlevel handlers) ---

    @mcp._mcp_server.subscribe_resource()
    async def _on_subscribe(uri: AnyUrl) -> None:
        try:
            session = mcp._mcp_server.request_context.session
            if session not in _subscribed_sessions:
                _subscribed_sessions.append(session)
        except LookupError:
            pass

    @mcp._mcp_server.unsubscribe_resource()
    async def _on_unsubscribe(uri: AnyUrl) -> None:
        try:
            session = mcp._mcp_server.request_context.session
            if session in _subscribed_sessions:
                _subscribed_sessions.remove(session)
        except LookupError:
            pass

    # --- Resource ---

    @mcp.resource(_RESOURCE_URI, mime_type="application/json")
    def _get_asr_result() -> str:
        return json.dumps(_current_result)

    # --- Tools ---

    @mcp.tool()
    async def start() -> dict:
        """Start audio capture and ASR streaming."""
        await engine.start()
        return {"status": "running"}

    @mcp.tool()
    async def stop() -> dict:
        """Stop audio capture and ASR streaming."""
        await engine.stop()
        return {"status": "stopped"}

    @mcp.tool()
    def is_running() -> dict:
        """Return the current ASR engine status."""
        return engine.status()

    # --- ASR result callback ---

    async def _on_asr_result(result: ASRResult) -> None:
        _current_result.update(
            transcript=result.transcript,
            is_final=result.is_final,
            confidence=result.confidence,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        uri = AnyUrl(_RESOURCE_URI)
        dead: list[Any] = []
        for session in list(_subscribed_sessions):
            try:
                await session.send_resource_updated(uri)
            except Exception:
                dead.append(session)
        for s in dead:
            _subscribed_sessions.remove(s)

    # Wire the callback into the engine
    engine._on_result = _on_asr_result

    return mcp


async def run_server(config: AppConfig) -> None:
    """Create the ASR engine, the MCP server, and run uvicorn."""
    async def _noop(result):
        pass

    if config.audio.audio_file:
        from asr_mcp.audio import FileAudioSource  # noqa: PLC0415
        audio_source = FileAudioSource(
            config.audio.audio_file,
            trailing_silence_s=config.audio.trailing_silence_s,
        )
        engine = ASREngine(config.audio, config.asr, _noop, audio_source=audio_source)
    else:
        engine = ASREngine(config.audio, config.asr, _noop)

    mcp = create_mcp_server(engine)

    await engine.start()

    starlette_app = mcp.streamable_http_app()
    uv_config = uvicorn.Config(
        starlette_app,
        host=config.server.host,
        port=config.server.port,
        log_level="info",
    )
    server = uvicorn.Server(uv_config)
    print(
        f"ASR MCP Server starting — "
        f"host={config.server.host} port={config.server.port} "
        f"asr={config.asr.type}"
    )
    try:
        await server.serve()
    finally:
        await engine.stop()
