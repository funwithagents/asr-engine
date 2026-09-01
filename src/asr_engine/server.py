from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

import uvicorn
from mcp.server.fastmcp import Context, FastMCP
from pydantic import AnyUrl

from asr_engine.config import AppConfig
from asr_engine.engine import ASREngine
from asr_engine.modules.base import SpeechUtterance
from asr_engine.segmenter import SpeechSegment
from asr_engine.tools import AsrTools

_UTTERANCE_URI = "asr://utterance"
_SEGMENT_URI = "asr://segment"


def create_mcp_server(engine: ASREngine) -> FastMCP:
    """Create the FastMCP server wired to *engine*.

    Side-effect: sets ``engine.on_speech_utterance`` / ``engine.on_speech_segment``
    to publish the ``asr://utterance`` / ``asr://segment`` resources. The engine
    already applied its segmentation mode and sound feedback from its config, so
    the server is a thin MCP adapter over the transport-agnostic ``AsrTools``.
    """
    tools = AsrTools(engine)

    _current_utterance: dict[str, Any] = {
        "transcript": "",
        "is_final": False,
        "confidence": None,
        "timestamp": None,
    }
    _current_segment: dict[str, Any] = {
        "transcript": "",
        "is_final": False,
        "end_reason": None,
        "timestamp": None,
    }
    # Subscriber sessions, keyed by resource URI string.
    _subscribers: dict[str, list[Any]] = {_UTTERANCE_URI: [], _SEGMENT_URI: []}

    mcp = FastMCP("asr-engine")

    # --- Subscribe / unsubscribe (lowlevel handlers) ---

    @mcp._mcp_server.subscribe_resource()
    async def _on_subscribe(uri: AnyUrl) -> None:
        sessions = _subscribers.get(str(uri))
        if sessions is None:
            return
        try:
            session = mcp._mcp_server.request_context.session
            if session not in sessions:
                sessions.append(session)
        except LookupError:
            pass

    @mcp._mcp_server.unsubscribe_resource()
    async def _on_unsubscribe(uri: AnyUrl) -> None:
        sessions = _subscribers.get(str(uri))
        if sessions is None:
            return
        try:
            session = mcp._mcp_server.request_context.session
            if session in sessions:
                sessions.remove(session)
        except LookupError:
            pass

    # --- Resources ---

    @mcp.resource(_UTTERANCE_URI, mime_type="application/json")
    def _get_asr_utterance() -> str:
        return json.dumps(_current_utterance)

    @mcp.resource(_SEGMENT_URI, mime_type="application/json")
    def _get_asr_segment() -> str:
        return json.dumps(_current_segment)

    async def _notify(uri_str: str) -> None:
        uri = AnyUrl(uri_str)
        sessions = _subscribers[uri_str]
        dead: list[Any] = []
        for session in list(sessions):
            try:
                await session.send_resource_updated(uri)
            except Exception:
                dead.append(session)
        for s in dead:
            sessions.remove(s)

    # --- Tools (thin MCP adapters over AsrTools) ---

    @mcp.tool()
    async def start() -> dict:
        """Start audio capture and ASR streaming."""
        return await tools.start()

    @mcp.tool()
    async def stop() -> dict:
        """Stop audio capture and ASR streaming."""
        return await tools.stop()

    @mcp.tool()
    def is_running() -> dict:
        """Return the current ASR engine status."""
        return tools.is_running()

    @mcp.tool()
    async def listen(ctx: Context) -> dict:
        """Start ASR, accumulate speech until the segment closes, stop ASR, return it."""

        async def _on_progress(
            progress: float, total: float | None, message: str | None
        ) -> None:
            await ctx.report_progress(progress=progress, total=total, message=message)

        return await tools.listen(on_progress=_on_progress)

    @mcp.tool()
    async def start_dictation(
        end_on_final_segment: bool = True, segmentation_mode: str | None = None
    ) -> dict:
        """Switch the always-on segment stream into an aggregating mode (non-blocking)."""
        return await tools.start_dictation(
            end_on_final_segment=end_on_final_segment,
            segmentation_mode=segmentation_mode,
        )

    @mcp.tool()
    async def stop_dictation() -> dict:
        """End the active dictation and revert to utterance; the engine keeps running."""
        return await tools.stop_dictation()

    @mcp.tool()
    def is_dictation_running() -> dict:
        """Whether a dictation is active, plus the current segmentation mode."""
        return tools.is_dictation_running()

    @mcp.tool()
    def set_dictation_default_segmentation_mode(mode: str) -> dict:
        """Set the default mode start_dictation uses when called with no mode."""
        return tools.set_dictation_default_segmentation_mode(mode)

    @mcp.tool()
    def set_listen_default_segmentation_mode(mode: str) -> dict:
        """Set the default mode listen uses when called with no mode."""
        return tools.set_listen_default_segmentation_mode(mode)

    # --- Engine callbacks → resources ---

    async def _on_speech_utterance(utterance: SpeechUtterance) -> None:
        _current_utterance.update(
            transcript=utterance.transcript,
            is_final=utterance.is_final,
            confidence=utterance.confidence,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        await _notify(_UTTERANCE_URI)

    async def _on_speech_segment(segment: SpeechSegment) -> None:
        _current_segment.update(
            transcript=segment.transcript,
            is_final=segment.is_final,
            end_reason=segment.end_reason,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        await _notify(_SEGMENT_URI)

    engine.on_speech_utterance = _on_speech_utterance
    engine.on_speech_segment = _on_speech_segment

    return mcp


async def run_server(config: AppConfig, log_level: str = "INFO") -> None:
    """Create the ASR engine, the MCP server, and run uvicorn.

    ``log_level`` sets the level for uvicorn's own loggers; the entry point has
    already configured the root/``asr_engine`` handlers via ``setup_logging``.
    """
    engine_config = config.engine
    # The engine selects its own audio source from config (live device or, when
    # engine.audio.audio_file is set, a format-matched FileAudioSource).
    engine = ASREngine(engine_config)

    mcp = create_mcp_server(engine)

    if engine_config.auto_start:
        await engine.start()
        # Optionally begin in a persistent, never-self-ending dictation so the
        # always-on asr://segment stream aggregates from startup.
        if engine_config.auto_start_dictation:
            await engine.start_dictation(end_on_final_segment=False)

    # Suppress chatty low-level logs that belong at DEBUG, not INFO.
    # Must be passed as log_config so uvicorn doesn't overwrite them on startup.
    logging.getLogger("mcp").setLevel(logging.WARNING)
    uvicorn_level = log_level.upper()
    log_config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "()": "uvicorn.logging.DefaultFormatter",
                "fmt": "%(levelprefix)s %(message)s",
            }
        },
        "handlers": {
            "default": {
                "class": "logging.StreamHandler",
                "formatter": "default",
                "stream": "ext://sys.stderr",
            }
        },
        "loggers": {
            "uvicorn": {
                "handlers": ["default"],
                "level": uvicorn_level,
                "propagate": False,
            },
            "uvicorn.error": {"level": uvicorn_level},
            "uvicorn.access": {"level": "WARNING", "propagate": False},
        },
    }

    starlette_app = mcp.streamable_http_app()
    uv_config = uvicorn.Config(
        starlette_app,
        host=config.server.host,
        port=config.server.port,
        log_config=log_config,
    )
    server = uvicorn.Server(uv_config)
    print(
        f"ASR Engine MCP Server starting — "
        f"host={config.server.host} port={config.server.port} "
        f"module={engine_config.module.type}"
    )
    try:
        await server.serve()
    finally:
        await engine.stop()
