"""Unit tests for server.py — create_mcp_server and the MCP tools/resource."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from asr_mcp.config import AppConfig, ASRConfig, AudioConfig, EngineConfig, ListenConfig
from asr_mcp.end_of_utterance_detector import UtteranceResult as ListenResult
from asr_mcp.engine import ASREngine
from asr_mcp.modules.base import ASRResult
from asr_mcp.server import create_mcp_server, run_server

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_engine(on_result=None) -> ASREngine:
    audio_config = AudioConfig(device=None)
    module = MagicMock()
    module.start = AsyncMock(return_value=None)
    module.stop = AsyncMock(return_value=None)
    mock_class = MagicMock(return_value=module)
    if on_result is None:
        on_result = AsyncMock()
    asr_config = ASRConfig(type="mock")
    with patch.dict("asr_mcp.engine.REGISTRY", {"mock": mock_class}):
        return ASREngine(audio_config, asr_config, on_result)


def tool_result_json(result) -> dict:
    """Parse the JSON payload from a call_tool result (list of TextContent)."""
    return json.loads(result[0].text)


# ---------------------------------------------------------------------------
# create_mcp_server — basic structure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_asr_result_resource_registered():
    engine = make_engine()
    mcp = create_mcp_server(engine)
    resources = await mcp.list_resources()
    uris = [str(r.uri) for r in resources]
    assert "asr://result" in uris


@pytest.mark.asyncio
async def test_tools_registered():
    engine = make_engine()
    mcp = create_mcp_server(engine)
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    assert {"start", "stop", "is_running", "listen"} <= names


# ---------------------------------------------------------------------------
# Resource — initial state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resource_initial_empty_transcript():
    engine = make_engine()
    mcp = create_mcp_server(engine)
    items = await mcp.read_resource("asr://result")
    payload = json.loads(next(iter(items)).content)
    assert payload["transcript"] == ""
    assert payload["is_final"] is False
    assert payload["confidence"] is None
    assert payload["timestamp"] is None


# ---------------------------------------------------------------------------
# _on_asr_result callback — updates resource state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_asr_result_updates_resource():
    engine = make_engine()
    mcp = create_mcp_server(engine)

    result = ASRResult(transcript="hello world", is_final=True, confidence=0.95)
    await engine._on_result(result)

    items = await mcp.read_resource("asr://result")
    payload = json.loads(next(iter(items)).content)

    assert payload["transcript"] == "hello world"
    assert payload["is_final"] is True
    assert payload["confidence"] == pytest.approx(0.95)
    assert payload["timestamp"] is not None


@pytest.mark.asyncio
async def test_on_asr_result_interim_result():
    engine = make_engine()
    mcp = create_mcp_server(engine)

    result = ASRResult(transcript="partial", is_final=False, confidence=None)
    await engine._on_result(result)

    items = await mcp.read_resource("asr://result")
    payload = json.loads(next(iter(items)).content)

    assert payload["transcript"] == "partial"
    assert payload["is_final"] is False
    assert payload["confidence"] is None


@pytest.mark.asyncio
async def test_on_asr_result_timestamp_is_iso8601():
    from datetime import datetime

    engine = make_engine()
    mcp = create_mcp_server(engine)

    await engine._on_result(ASRResult(transcript="x", is_final=True, confidence=None))

    items = await mcp.read_resource("asr://result")
    payload = json.loads(next(iter(items)).content)

    # Should be parseable as a datetime
    ts = datetime.fromisoformat(payload["timestamp"])
    assert ts.tzinfo is not None


@pytest.mark.asyncio
async def test_on_asr_result_overwrites_previous():
    engine = make_engine()
    mcp = create_mcp_server(engine)

    await engine._on_result(
        ASRResult(transcript="first", is_final=False, confidence=None)
    )
    await engine._on_result(
        ASRResult(transcript="second", is_final=True, confidence=0.8)
    )

    items = await mcp.read_resource("asr://result")
    payload = json.loads(next(iter(items)).content)
    assert payload["transcript"] == "second"


# ---------------------------------------------------------------------------
# create_mcp_server wires engine._on_result
# ---------------------------------------------------------------------------


def test_create_mcp_server_replaces_engine_on_result():
    original = AsyncMock()
    engine = make_engine(on_result=original)
    create_mcp_server(engine)  # wiring side effect: rebinds engine._on_result
    # After wiring, engine._on_result is no longer the original noop
    assert engine._on_result is not original


# ---------------------------------------------------------------------------
# Tools — start / stop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_tool_starts_engine():
    engine = make_engine()
    mcp = create_mcp_server(engine)

    fake_queue: asyncio.Queue[bytes] = asyncio.Queue()
    with patch("asr_mcp.engine.AudioCapture") as MockCapture:
        MockCapture.return_value.start.return_value = fake_queue
        result = await mcp.call_tool("start", {})

    assert tool_result_json(result) == {"status": "running"}
    assert engine.status()["running"] is True


@pytest.mark.asyncio
async def test_stop_tool_stops_engine():
    engine = make_engine()
    mcp = create_mcp_server(engine)

    fake_queue: asyncio.Queue[bytes] = asyncio.Queue()
    with patch("asr_mcp.engine.AudioCapture") as MockCapture:
        MockCapture.return_value.start.return_value = fake_queue
        await mcp.call_tool("start", {})
        result = await mcp.call_tool("stop", {})

    assert tool_result_json(result) == {"status": "stopped"}
    assert engine.status()["running"] is False


# ---------------------------------------------------------------------------
# Tools — is_running
# ---------------------------------------------------------------------------


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

    # Inject a fake session that always fails
    dead_session = AsyncMock()
    dead_session.send_resource_updated = AsyncMock(side_effect=Exception("gone"))

    # Access the internal subscribed sessions list via the closure
    # by registering a subscribe, which adds to the list.
    # We'll inject directly via the subscribe handler side-effect.
    # Easiest: find the subscribed_sessions list by calling _on_asr_result once
    # with a mock session injected.
    #
    # We reach the list by temporarily monkey-patching request_context.
    from unittest.mock import PropertyMock, patch

    with patch.object(
        type(mcp._mcp_server),
        "request_context",
        new_callable=PropertyMock,
        return_value=MagicMock(session=dead_session),
    ):
        # Trigger subscribe handler directly via the lowlevel handler
        from mcp.types import SubscribeRequest, SubscribeRequestParams
        from pydantic import AnyUrl

        req = SubscribeRequest(
            method="resources/subscribe",
            params=SubscribeRequestParams(uri=AnyUrl("asr://result")),
        )
        handler = mcp._mcp_server.request_handlers.get(type(req))
        if handler:
            await handler(req)

    # Now send a result — the dead session should be removed
    await engine._on_result(ASRResult(transcript="t", is_final=True, confidence=None))

    # Send again — dead session is gone, no exception raised
    await engine._on_result(ASRResult(transcript="t2", is_final=True, confidence=None))


# ---------------------------------------------------------------------------
# run_server — validation and banner
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_server_raises_on_unknown_asr_type() -> None:
    from asr_mcp.config import AppConfig, ASRConfig, AudioConfig, ServerConfig

    config = AppConfig(
        server=ServerConfig(),
        audio=AudioConfig(),
        asr=ASRConfig(type="no_such_module"),
    )
    with pytest.raises(ValueError, match="no_such_module"):
        await run_server(config)


# ---------------------------------------------------------------------------
# Tools — listen
# ---------------------------------------------------------------------------


def _listen_config(**kwargs) -> ListenConfig:
    defaults = dict(
        end_of_utterance_mode="trigger_word",
        trigger_words=["validate"],
        initial_silence_timeout_s=10.0,
        end_of_speech_timeout_s=5.0,
        sound_feedback=False,
    )
    defaults.update(kwargs)
    return ListenConfig(**defaults)  # type: ignore[arg-type]  # heterogeneous test kwargs


@pytest.mark.asyncio
async def test_listen_engine_already_running():
    """listen raises when engine is already running."""
    from mcp.server.fastmcp.exceptions import ToolError

    engine = make_engine()
    engine._running = True
    mcp = create_mcp_server(engine, _listen_config())
    with pytest.raises(ToolError, match="already running"):
        await mcp.call_tool("listen", {})


@pytest.mark.asyncio
async def test_listen_concurrent_calls_blocked():
    """Second listen call while first is in progress raises."""
    engine = make_engine()
    listen_cfg = _listen_config()
    mcp = create_mcp_server(engine, listen_cfg)

    # First listen: session.wait() blocks until we signal
    wait_event = asyncio.Event()

    async def _blocking_wait():
        await wait_event.wait()
        return ListenResult(transcript="", end_reason="trigger_word")

    fake_session = AsyncMock()
    fake_session.on_result = AsyncMock()
    fake_session.wait = _blocking_wait

    with (
        patch("asr_mcp.server.EndOfUtteranceDetector", return_value=fake_session),
        patch("asr_mcp.engine.AudioCapture") as MockCapture,
    ):
        MockCapture.return_value.start.return_value = asyncio.Queue()
        first_task = asyncio.create_task(mcp.call_tool("listen", {}))
        await asyncio.sleep(0)  # let first_task reach the lock

        from mcp.server.fastmcp.exceptions import ToolError

        with pytest.raises(ToolError, match="already in progress"):
            await mcp.call_tool("listen", {})

        # Unblock the first task
        wait_event.set()
        await first_task


@pytest.mark.asyncio
async def test_listen_trigger_word_success():
    """Successful listen in trigger_word mode: starts engine, returns transcript, stops engine."""
    engine = make_engine()
    listen_cfg = _listen_config(end_of_utterance_mode="trigger_word")
    mcp = create_mcp_server(engine, listen_cfg)

    fake_result = ListenResult(transcript="the sky is blue", end_reason="trigger_word")
    fake_session = AsyncMock()
    fake_session.on_result = AsyncMock()
    fake_session.wait = AsyncMock(return_value=fake_result)

    with (
        patch("asr_mcp.server.EndOfUtteranceDetector", return_value=fake_session),
        patch("asr_mcp.engine.AudioCapture") as MockCapture,
    ):
        MockCapture.return_value.start.return_value = asyncio.Queue()
        result = await mcp.call_tool("listen", {})

    payload = tool_result_json(result)
    assert payload == {"transcript": "the sky is blue", "end_reason": "trigger_word"}
    assert engine.status()["running"] is False


@pytest.mark.asyncio
async def test_listen_timeout_mode_success():
    """Successful listen in timeout mode returns end_of_speech_timeout end_reason."""
    engine = make_engine()
    listen_cfg = _listen_config(
        end_of_utterance_mode="timeout", end_of_speech_timeout_s=0.01
    )
    mcp = create_mcp_server(engine, listen_cfg)

    fake_result = ListenResult(transcript="hello", end_reason="end_of_speech_timeout")
    fake_session = AsyncMock()
    fake_session.on_result = AsyncMock()
    fake_session.wait = AsyncMock(return_value=fake_result)

    with (
        patch("asr_mcp.server.EndOfUtteranceDetector", return_value=fake_session),
        patch("asr_mcp.engine.AudioCapture") as MockCapture,
    ):
        MockCapture.return_value.start.return_value = asyncio.Queue()
        result = await mcp.call_tool("listen", {})

    payload = tool_result_json(result)
    assert payload["end_reason"] == "end_of_speech_timeout"


@pytest.mark.asyncio
async def test_listen_engine_stopped_on_exception():
    """Engine is always stopped even if session.wait() raises."""
    engine = make_engine()
    listen_cfg = _listen_config()
    mcp = create_mcp_server(engine, listen_cfg)

    fake_session = AsyncMock()
    fake_session.on_result = AsyncMock()
    fake_session.wait = AsyncMock(side_effect=RuntimeError("boom"))

    from mcp.server.fastmcp.exceptions import ToolError

    with (
        patch("asr_mcp.server.EndOfUtteranceDetector", return_value=fake_session),
        patch("asr_mcp.engine.AudioCapture") as MockCapture,
    ):
        MockCapture.return_value.start.return_value = asyncio.Queue()
        with pytest.raises(ToolError, match="boom"):
            await mcp.call_tool("listen", {})

    assert engine.status()["running"] is False


# ---------------------------------------------------------------------------
# Tools — listen — progress notifications (ctx.report_progress)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_listen_progress_called_per_committed_final():
    """ctx.report_progress is called once per committed final with the accumulated transcript."""
    engine = make_engine()
    listen_cfg = _listen_config(
        end_of_utterance_mode="trigger_word", trigger_words=["submit"]
    )
    mcp = create_mcp_server(engine, listen_cfg)

    progress_calls: list[dict] = []

    # We need a real ListenSession to drive committed accumulation.
    # Patch ctx.report_progress via the Context injected by FastMCP.
    # FastMCP calls tools directly; when called via mcp.call_tool the ctx is
    # constructed internally — we patch Context.report_progress on the class.
    from mcp.server.fastmcp import Context

    original_report = Context.report_progress

    async def _capture_progress(self, progress, total=None, message=None):
        progress_calls.append(
            {"progress": progress, "total": total, "message": message}
        )

    Context.report_progress = _capture_progress
    try:
        with patch("asr_mcp.engine.AudioCapture") as MockCapture:
            MockCapture.return_value.start.return_value = asyncio.Queue()

            # Feed results directly into the session while listen is waiting.
            # We do this by running listen in the background and feeding results
            # through the engine callback once the session is wired.
            listen_task = asyncio.create_task(mcp.call_tool("listen", {}))
            await asyncio.sleep(0)  # let listen wire engine._on_result to session

            await engine._on_result(
                ASRResult(transcript="hello world", is_final=True, confidence=None)
            )
            await engine._on_result(
                ASRResult(transcript="how are you", is_final=True, confidence=None)
            )
            await engine._on_result(
                ASRResult(transcript="submit", is_final=True, confidence=None)
            )

            await asyncio.wait_for(listen_task, timeout=5.0)
    finally:
        Context.report_progress = original_report

    assert len(progress_calls) == 2
    assert progress_calls[0]["message"] == "hello world"
    assert progress_calls[0]["progress"] == 1
    assert progress_calls[1]["message"] == "hello world how are you"
    assert progress_calls[1]["progress"] == 2


@pytest.mark.asyncio
async def test_listen_progress_not_called_for_trigger_word_final():
    """ctx.report_progress is NOT called when the trigger word final ends the session."""
    engine = make_engine()
    listen_cfg = _listen_config(
        end_of_utterance_mode="trigger_word", trigger_words=["validate"]
    )
    mcp = create_mcp_server(engine, listen_cfg)

    progress_calls: list[dict] = []

    from mcp.server.fastmcp import Context

    original_report = Context.report_progress

    async def _capture_progress(self, progress, total=None, message=None):
        progress_calls.append({"progress": progress, "message": message})

    Context.report_progress = _capture_progress
    try:
        with patch("asr_mcp.engine.AudioCapture") as MockCapture:
            MockCapture.return_value.start.return_value = asyncio.Queue()

            listen_task = asyncio.create_task(mcp.call_tool("listen", {}))
            await asyncio.sleep(0)

            await engine._on_result(
                ASRResult(transcript="validate", is_final=True, confidence=None)
            )

            await asyncio.wait_for(listen_task, timeout=5.0)
    finally:
        Context.report_progress = original_report

    assert progress_calls == []


# ---------------------------------------------------------------------------
# run_server — auto_start
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_server_auto_start_false_does_not_start_engine(capsys) -> None:
    """When auto_start=False the engine is not started on server startup."""
    import asr_mcp.modules as mod

    fake_module = MagicMock()
    fake_module.start = AsyncMock(return_value=None)
    fake_module.stop = AsyncMock(return_value=None)
    fake_class = MagicMock(return_value=fake_module)

    config = AppConfig(
        server=AppConfig.__dataclass_fields__["server"].default_factory(),
        audio=AudioConfig(),
        asr=ASRConfig(type="fake_auto"),
        engine=EngineConfig(auto_start=False),
    )
    original = dict(mod.REGISTRY)
    mod.REGISTRY["fake_auto"] = fake_class  # type: ignore[assignment]  # MagicMock test double
    try:
        with patch("asr_mcp.server.uvicorn.Server.serve", new_callable=AsyncMock):
            await run_server(config)
    finally:
        mod.REGISTRY.clear()
        mod.REGISTRY.update(original)

    # fake_module.start should NOT have been called
    fake_module.start.assert_not_called()


# ---------------------------------------------------------------------------
# run_server — validation and banner
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_server_prints_banner(capsys) -> None:
    import asr_mcp.modules as mod
    from asr_mcp.config import AppConfig, ASRConfig, AudioConfig, ServerConfig

    fake_module = MagicMock()
    fake_module.start = AsyncMock(return_value=None)
    fake_module.stop = AsyncMock(return_value=None)
    fake_class = MagicMock(return_value=fake_module)

    config = AppConfig(
        server=ServerConfig(host="0.0.0.0", port=9090),
        audio=AudioConfig(),
        asr=ASRConfig(type="fake_banner"),
    )
    original = dict(mod.REGISTRY)
    mod.REGISTRY["fake_banner"] = fake_class  # type: ignore[assignment]  # MagicMock test double
    try:
        with patch("asr_mcp.server.uvicorn.Server.serve", new_callable=AsyncMock):
            await run_server(config)
    finally:
        mod.REGISTRY.clear()
        mod.REGISTRY.update(original)

    out = capsys.readouterr().out
    assert "0.0.0.0" in out
    assert "9090" in out
    assert "fake_banner" in out
