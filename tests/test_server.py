"""Unit tests for server.py — create_mcp_server and the MCP tools/resources."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from asr_engine.config import (
    AppConfig,
    ASREngineConfig,
    ModuleConfig,
    ServerConfig,
    SoundFeedbackConfig,
)
from asr_engine.engine import ASREngine
from asr_engine.modules.base import SpeechUtterance
from asr_engine.segmenter import SpeechSegment
from asr_engine.server import create_mcp_server, run_server

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _engine_config(module_type: str = "mock", **kwargs) -> ASREngineConfig:
    return ASREngineConfig(
        sound_feedback=SoundFeedbackConfig(enabled=False),
        module=ModuleConfig(type=module_type),
        **kwargs,
    )


def make_engine(**cfg_kwargs) -> ASREngine:
    module = MagicMock()
    module.start = AsyncMock(return_value=None)
    module.stop = AsyncMock(return_value=None)
    mock_class = MagicMock(return_value=module)
    with patch.dict("asr_engine.engine.REGISTRY", {"mock": mock_class}):
        return ASREngine(_engine_config(**cfg_kwargs))


def tool_result_json(result) -> dict:
    """Parse the JSON payload from a call_tool result (list of TextContent)."""
    return json.loads(result[0].text)


def _utterance(text: str, is_final: bool, confidence=None) -> SpeechUtterance:
    return SpeechUtterance(transcript=text, is_final=is_final, confidence=confidence)


def _segment(
    text: str, is_final: bool, end_reason=None, utterances=None
) -> SpeechSegment:
    return SpeechSegment(
        transcript=text,
        is_final=is_final,
        end_reason=end_reason,
        utterances=utterances or [],
    )


# ---------------------------------------------------------------------------
# create_mcp_server — basic structure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_both_resources_registered():
    engine = make_engine()
    mcp = create_mcp_server(engine)
    resources = await mcp.list_resources()
    uris = {str(r.uri) for r in resources}
    assert {"asr://utterance", "asr://segment"} <= uris


@pytest.mark.asyncio
async def test_tools_registered():
    engine = make_engine()
    mcp = create_mcp_server(engine)
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    assert {"start", "stop", "is_running", "listen"} <= names


# ---------------------------------------------------------------------------
# Resources — initial state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_utterance_resource_initial_empty():
    engine = make_engine()
    mcp = create_mcp_server(engine)
    items = await mcp.read_resource("asr://utterance")
    payload = json.loads(next(iter(items)).content)
    assert payload["transcript"] == ""
    assert payload["is_final"] is False
    assert payload["confidence"] is None
    assert payload["timestamp"] is None


@pytest.mark.asyncio
async def test_segment_resource_initial_empty():
    engine = make_engine()
    mcp = create_mcp_server(engine)
    items = await mcp.read_resource("asr://segment")
    payload = json.loads(next(iter(items)).content)
    assert payload["transcript"] == ""
    assert payload["is_final"] is False
    assert payload["end_reason"] is None


# ---------------------------------------------------------------------------
# Engine callbacks update the resources
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_utterance_callback_updates_utterance_resource():
    engine = make_engine()
    mcp = create_mcp_server(engine)

    await engine.on_speech_utterance(_utterance("hello world", True, 0.95))

    items = await mcp.read_resource("asr://utterance")
    payload = json.loads(next(iter(items)).content)
    assert payload["transcript"] == "hello world"
    assert payload["is_final"] is True
    assert payload["confidence"] == pytest.approx(0.95)
    assert payload["timestamp"] is not None


@pytest.mark.asyncio
async def test_segment_callback_updates_segment_resource():
    engine = make_engine()
    mcp = create_mcp_server(engine)

    await engine.on_speech_segment(
        _segment("the sky is blue", True, end_reason="trigger_word")
    )

    items = await mcp.read_resource("asr://segment")
    payload = json.loads(next(iter(items)).content)
    assert payload["transcript"] == "the sky is blue"
    assert payload["is_final"] is True
    assert payload["end_reason"] == "trigger_word"
    assert payload["timestamp"] is not None


@pytest.mark.asyncio
async def test_utterance_resource_overwrites_previous():
    engine = make_engine()
    mcp = create_mcp_server(engine)

    await engine.on_speech_utterance(_utterance("first", False))
    await engine.on_speech_utterance(_utterance("second", True, 0.8))

    items = await mcp.read_resource("asr://utterance")
    payload = json.loads(next(iter(items)).content)
    assert payload["transcript"] == "second"


def test_create_mcp_server_wires_engine_callbacks():
    engine = make_engine()
    before_u = engine.on_speech_utterance
    before_s = engine.on_speech_segment
    create_mcp_server(engine)
    assert engine.on_speech_utterance is not before_u
    assert engine.on_speech_segment is not before_s


def test_create_mcp_server_leaves_engine_segment_mode():
    """The engine owns its segment mode (from config); the server must not touch it."""
    engine = make_engine(segmentation_mode="trigger_word")
    assert engine._segment_mode == "trigger_word"
    create_mcp_server(engine)
    assert engine._segment_mode == "trigger_word"


# ---------------------------------------------------------------------------
# Tools — start / stop / is_running
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_tool_starts_engine():
    engine = make_engine()
    mcp = create_mcp_server(engine)

    fake_queue: asyncio.Queue[bytes] = asyncio.Queue()
    with patch("asr_engine.engine.AudioCapture") as MockCapture:
        MockCapture.return_value.start.return_value = fake_queue
        result = await mcp.call_tool("start", {})

    assert tool_result_json(result) == {"status": "running"}
    assert engine.status()["running"] is True
    await engine.stop()


@pytest.mark.asyncio
async def test_stop_tool_stops_engine():
    engine = make_engine()
    mcp = create_mcp_server(engine)

    fake_queue: asyncio.Queue[bytes] = asyncio.Queue()
    with patch("asr_engine.engine.AudioCapture") as MockCapture:
        MockCapture.return_value.start.return_value = fake_queue
        await mcp.call_tool("start", {})
        result = await mcp.call_tool("stop", {})

    assert tool_result_json(result) == {"status": "stopped"}
    assert engine.status()["running"] is False


@pytest.mark.asyncio
async def test_is_running_tool_returns_engine_status():
    engine = make_engine()
    mcp = create_mcp_server(engine)
    engine._running = True
    engine._connected = True

    result = await mcp.call_tool("is_running", {})
    assert tool_result_json(result) == {"running": True, "connected": True}


# ---------------------------------------------------------------------------
# Push notifications — dead session cleanup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dead_session_removed_on_send_failure():
    """Sessions that raise during send_resource_updated are pruned."""
    engine = make_engine()
    mcp = create_mcp_server(engine)

    dead_session = AsyncMock()
    dead_session.send_resource_updated = AsyncMock(side_effect=Exception("gone"))

    from unittest.mock import PropertyMock

    with patch.object(
        type(mcp._mcp_server),
        "request_context",
        new_callable=PropertyMock,
        return_value=MagicMock(session=dead_session),
    ):
        from mcp.types import SubscribeRequest, SubscribeRequestParams
        from pydantic import AnyUrl

        req = SubscribeRequest(
            method="resources/subscribe",
            params=SubscribeRequestParams(uri=AnyUrl("asr://utterance")),
        )
        handler = mcp._mcp_server.request_handlers.get(type(req))
        if handler:
            await handler(req)

    # Sending an utterance should prune the dead session, no exception raised.
    await engine.on_speech_utterance(_utterance("t", True))
    await engine.on_speech_utterance(_utterance("t2", True))


# ---------------------------------------------------------------------------
# run_server — validation and banner
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_server_raises_on_unknown_asr_type() -> None:
    config = AppConfig(engine=_engine_config(module_type="no_such_module"))
    with pytest.raises(ValueError, match="no_such_module"):
        await run_server(config)


# ---------------------------------------------------------------------------
# Tools — listen
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_listen_engine_already_running():
    """listen raises when engine is already running."""
    from mcp.server.fastmcp.exceptions import ToolError

    engine = make_engine()
    engine._running = True
    mcp = create_mcp_server(engine)
    with pytest.raises(ToolError, match="already running"):
        await mcp.call_tool("listen", {})


@pytest.mark.asyncio
async def test_listen_concurrent_calls_blocked():
    """Second listen call while first is in progress raises."""
    engine = make_engine()
    mcp = create_mcp_server(engine)

    release = asyncio.Event()

    async def _blocking_listen(mode=None, *, on_update=None):
        await release.wait()
        return _segment("", True, end_reason="trigger_word")

    with patch.object(engine, "listen", new=_blocking_listen):
        first_task = asyncio.create_task(mcp.call_tool("listen", {}))
        await asyncio.sleep(0)  # let first_task grab the lock

        from mcp.server.fastmcp.exceptions import ToolError

        with pytest.raises(ToolError, match="already in progress"):
            await mcp.call_tool("listen", {})

        release.set()
        await first_task


@pytest.mark.asyncio
async def test_listen_success_returns_transcript_and_reason():
    """listen returns the closed segment's transcript and end_reason."""
    engine = make_engine()
    mcp = create_mcp_server(engine)

    async def _fake_listen(mode=None, *, on_update=None):
        # The MCP tool passes no explicit mode; engine uses its config default.
        assert mode is None
        return _segment("the sky is blue", True, end_reason="trigger_word")

    with patch.object(engine, "listen", new=_fake_listen):
        result = await mcp.call_tool("listen", {})

    payload = tool_result_json(result)
    assert payload == {"transcript": "the sky is blue", "end_reason": "trigger_word"}


@pytest.mark.asyncio
async def test_listen_releases_lock_on_exception():
    """A failed listen still releases the lock so a later call can proceed."""
    from mcp.server.fastmcp.exceptions import ToolError

    engine = make_engine()
    mcp = create_mcp_server(engine)

    async def _boom(mode=None, *, on_update=None):
        raise RuntimeError("boom")

    with patch.object(engine, "listen", new=_boom):
        with pytest.raises(ToolError, match="boom"):
            await mcp.call_tool("listen", {})

    async def _ok(mode=None, *, on_update=None):
        return _segment("ok", True, end_reason="trigger_word")

    with patch.object(engine, "listen", new=_ok):
        result = await mcp.call_tool("listen", {})
    assert tool_result_json(result)["transcript"] == "ok"


# ---------------------------------------------------------------------------
# Tools — listen — progress notifications
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_listen_progress_reported_per_committed_final():
    """report_progress fires once per committed final, with the accumulated transcript."""
    engine = make_engine()
    mcp = create_mcp_server(engine)

    u1 = _utterance("hello world", True)
    u2 = _utterance("how are you", True)

    async def _fake_listen(mode=None, *, on_update):
        # open segments as finals commit, then the trigger-word close (no growth).
        await on_update(_segment("hello world", False, utterances=[u1]))
        await on_update(_segment("hello world how are you", False, utterances=[u1, u2]))
        await on_update(
            _segment(
                "hello world how are you", True, "trigger_word", utterances=[u1, u2]
            )
        )
        return _segment(
            "hello world how are you", True, "trigger_word", utterances=[u1, u2]
        )

    progress_calls: list[dict] = []
    from mcp.server.fastmcp import Context

    original = Context.report_progress

    async def _capture(self, progress, total=None, message=None):
        progress_calls.append({"progress": progress, "message": message})

    Context.report_progress = _capture
    try:
        with patch.object(engine, "listen", new=_fake_listen):
            await mcp.call_tool("listen", {})
    finally:
        Context.report_progress = original

    assert len(progress_calls) == 2
    assert progress_calls[0] == {"progress": 1, "message": "hello world"}
    assert progress_calls[1] == {"progress": 2, "message": "hello world how are you"}


@pytest.mark.asyncio
async def test_listen_progress_not_reported_when_no_finals_committed():
    """No committed finals (immediate trigger word) → no progress notifications."""
    engine = make_engine()
    mcp = create_mcp_server(engine)

    async def _fake_listen(mode=None, *, on_update):
        await on_update(_segment("", True, "trigger_word", utterances=[]))
        return _segment("", True, "trigger_word", utterances=[])

    progress_calls: list[dict] = []
    from mcp.server.fastmcp import Context

    original = Context.report_progress

    async def _capture(self, progress, total=None, message=None):
        progress_calls.append({"progress": progress, "message": message})

    Context.report_progress = _capture
    try:
        with patch.object(engine, "listen", new=_fake_listen):
            await mcp.call_tool("listen", {})
    finally:
        Context.report_progress = original

    assert progress_calls == []


# ---------------------------------------------------------------------------
# run_server — auto_start / banner
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_server_auto_start_false_does_not_start_engine() -> None:
    import asr_engine.modules as mod

    fake_module = MagicMock()
    fake_module.start = AsyncMock(return_value=None)
    fake_module.stop = AsyncMock(return_value=None)
    fake_class = MagicMock(return_value=fake_module)

    config = AppConfig(
        engine=_engine_config(module_type="fake_auto", auto_start=False),
    )
    original = dict(mod.REGISTRY)
    mod.REGISTRY["fake_auto"] = fake_class  # type: ignore[assignment]  # MagicMock test double
    try:
        with patch("asr_engine.server.uvicorn.Server.serve", new_callable=AsyncMock):
            await run_server(config)
    finally:
        mod.REGISTRY.clear()
        mod.REGISTRY.update(original)

    fake_module.start.assert_not_called()


@pytest.mark.asyncio
async def test_run_server_prints_banner(capsys) -> None:
    import asr_engine.modules as mod

    fake_module = MagicMock()
    fake_module.start = AsyncMock(return_value=None)
    fake_module.stop = AsyncMock(return_value=None)
    fake_class = MagicMock(return_value=fake_module)

    config = AppConfig(
        server=ServerConfig(host="0.0.0.0", port=9090),
        engine=_engine_config(module_type="fake_banner"),
    )
    original = dict(mod.REGISTRY)
    mod.REGISTRY["fake_banner"] = fake_class  # type: ignore[assignment]  # MagicMock test double
    try:
        with patch("asr_engine.server.uvicorn.Server.serve", new_callable=AsyncMock):
            await run_server(config)
    finally:
        mod.REGISTRY.clear()
        mod.REGISTRY.update(original)

    out = capsys.readouterr().out
    assert "0.0.0.0" in out
    assert "9090" in out
    assert "fake_banner" in out
