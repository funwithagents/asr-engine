from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

import uvicorn
from mcp.server.fastmcp import Context, FastMCP
from pydantic import AnyUrl

from asr_mcp.config import AppConfig, AudioConfig, EngineConfig, ListenConfig
from asr_mcp.engine import ASREngine
from asr_mcp.modules.base import SpeechUtterance
from asr_mcp.segmenter import SpeechSegment
from asr_mcp.sound_feedback import NoOpSoundFeedback, SoundFeedback

_UTTERANCE_URI = "asr://utterance"
_SEGMENT_URI = "asr://segment"


def create_mcp_server(
    engine: ASREngine,
    listen_config: ListenConfig | None = None,
    audio_config: AudioConfig | None = None,
    engine_config: EngineConfig | None = None,
) -> FastMCP:
    """Create the FastMCP server wired to *engine*.

    Side-effect: sets ``engine.on_speech_utterance`` / ``engine.on_speech_segment``
    to publish the ``asr://utterance`` / ``asr://segment`` resources, and applies
    the engine config's segment mode.
    """
    if listen_config is None:
        from asr_mcp.config import ListenConfig as _LC  # noqa: PLC0415

        listen_config = _LC()
    if audio_config is None:
        from asr_mcp.config import AudioConfig as _AC  # noqa: PLC0415

        audio_config = _AC()
    if engine_config is None:
        from asr_mcp.config import EngineConfig as _EC  # noqa: PLC0415

        engine_config = _EC()

    if listen_config.sound_feedback:
        sound_feedback = SoundFeedback(output_device=audio_config.output_device)
    else:
        sound_feedback = NoOpSoundFeedback()

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

    mcp = FastMCP("asr-mcp")
    _listen_lock = asyncio.Lock()

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
        """Start ASR, accumulate speech until the segment closes, stop ASR, return it."""
        if _listen_lock.locked():
            raise ValueError("A listen session is already in progress.")
        if engine.status()["running"]:
            raise ValueError("ASR is already running. Stop it before calling listen.")

        async with _listen_lock:
            reported = 0

            async def _on_update(segment: SpeechSegment) -> None:
                # Report progress only when a new final is committed.
                nonlocal reported
                committed = len(segment.utterances)
                if committed > reported:
                    reported = committed
                    await ctx.report_progress(
                        progress=committed,
                        total=None,
                        message=segment.transcript,
                    )

            await sound_feedback.play_start()
            try:
                segment = await engine.listen(
                    mode=listen_config.segment_mode,
                    trigger_words=listen_config.trigger_words,
                    initial_silence_timeout_s=listen_config.initial_silence_timeout_s,
                    end_of_speech_timeout_s=listen_config.end_of_speech_timeout_s,
                    on_update=_on_update,
                )
            finally:
                await sound_feedback.play_stop()

            return {"transcript": segment.transcript, "end_reason": segment.end_reason}

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
    engine.set_segment_mode(
        engine_config.segment_mode,
        trigger_words=engine_config.trigger_words,
        initial_silence_timeout_s=engine_config.initial_silence_timeout_s,
        end_of_speech_timeout_s=engine_config.end_of_speech_timeout_s,
    )

    return mcp


async def run_server(config: AppConfig) -> None:
    """Create the ASR engine, the MCP server, and run uvicorn."""
    if config.audio.audio_file:
        from asr_mcp.audio import FileAudioSource  # noqa: PLC0415

        audio_source = FileAudioSource(
            config.audio.audio_file,
            trailing_silence_s=config.audio.trailing_silence_s,
        )
        engine = ASREngine(config.audio, config.asr, audio_source=audio_source)
    else:
        engine = ASREngine(config.audio, config.asr)

    mcp = create_mcp_server(engine, config.listen, config.audio, config.engine)

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
