from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

import uvicorn
from mcp.server.fastmcp import Context, FastMCP
from pydantic import AnyUrl

from asr_mcp.config import AppConfig, AudioConfig, ListenConfig
from asr_mcp.end_of_utterance_detector import EndOfUtteranceDetector
from asr_mcp.engine import ASREngine
from asr_mcp.modules.base import ASRResult
from asr_mcp.sound_feedback import NoOpSoundFeedback, SoundFeedback

_RESOURCE_URI = "asr://result"


def create_mcp_server(
    engine: ASREngine,
    listen_config: ListenConfig | None = None,
    audio_config: AudioConfig | None = None,
) -> FastMCP:
    """Create the FastMCP server wired to *engine*.

    Side-effect: replaces ``engine._on_result`` with the MCP result callback
    so that ASR results flow into the resource state.
    """
    if listen_config is None:
        from asr_mcp.config import ListenConfig as _LC  # noqa: PLC0415

        listen_config = _LC()
    if audio_config is None:
        from asr_mcp.config import AudioConfig as _AC  # noqa: PLC0415

        audio_config = _AC()

    if listen_config.sound_feedback:
        sound_feedback = SoundFeedback(output_device=audio_config.output_device)
    else:
        sound_feedback = NoOpSoundFeedback()

    _current_result: dict[str, Any] = {
        "transcript": "",
        "is_final": False,
        "confidence": None,
        "timestamp": None,
    }
    _subscribed_sessions: list[Any] = []

    mcp = FastMCP("asr-mcp")
    _listen_lock = asyncio.Lock()

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

    @mcp.tool()
    async def listen(ctx: Context) -> dict:
        """Start ASR, accumulate speech until end-of-utterance, stop ASR, return transcript."""
        if _listen_lock.locked():
            raise ValueError("A listen session is already in progress.")
        if engine.status()["running"]:
            raise ValueError("ASR is already running. Stop it before calling listen.")

        async with _listen_lock:

            async def _on_final_committed(transcript: str) -> None:
                await ctx.report_progress(
                    progress=len(session._committed),
                    total=None,
                    message=transcript,
                )

            session = EndOfUtteranceDetector(
                mode=listen_config.end_of_utterance_mode,
                trigger_words=listen_config.trigger_words,
                initial_silence_timeout_s=listen_config.initial_silence_timeout_s,
                end_of_speech_timeout_s=listen_config.end_of_speech_timeout_s,
                on_final_committed=_on_final_committed,
            )
            original_callback = engine._on_result
            engine._on_result = session.on_result
            await sound_feedback.play_start()
            await engine.start()
            try:
                result = await session.wait()
            finally:
                await engine.stop()
                await sound_feedback.play_stop()
                engine._on_result = original_callback

            return {"transcript": result.transcript, "end_reason": result.end_reason}

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

    mcp = create_mcp_server(engine, config.listen, config.audio)

    if config.engine.auto_start:
        await engine.start()

    # Suppress chatty low-level logs that belong at DEBUG, not INFO.
    # Must be passed as log_config so uvicorn doesn't overwrite them on startup.
    logging.getLogger("mcp").setLevel(logging.WARNING)
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
            "uvicorn": {"handlers": ["default"], "level": "INFO", "propagate": False},
            "uvicorn.error": {"level": "INFO"},
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
        f"ASR MCP Server starting — "
        f"host={config.server.host} port={config.server.port} "
        f"asr={config.asr.type}"
    )
    try:
        await server.serve()
    finally:
        await engine.stop()
