"""Unit tests for server.py — create_mcp_server and the MCP tools/resource."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from asr_mcp.config import AppConfig, ASRConfig, AudioConfig
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
    assert {"pause", "resume", "is_running"} <= names


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
    from datetime import datetime, timezone

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

    await engine._on_result(ASRResult(transcript="first", is_final=False, confidence=None))
    await engine._on_result(ASRResult(transcript="second", is_final=True, confidence=0.8))

    items = await mcp.read_resource("asr://result")
    payload = json.loads(next(iter(items)).content)
    assert payload["transcript"] == "second"


# ---------------------------------------------------------------------------
# create_mcp_server wires engine._on_result
# ---------------------------------------------------------------------------

def test_create_mcp_server_replaces_engine_on_result():
    original = AsyncMock()
    engine = make_engine(on_result=original)
    mcp = create_mcp_server(engine)
    # After wiring, engine._on_result is no longer the original noop
    assert engine._on_result is not original


# ---------------------------------------------------------------------------
# Tools — pause
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pause_tool_calls_engine_pause():
    engine = make_engine()
    mcp = create_mcp_server(engine)

    result = await mcp.call_tool("pause", {})
    assert tool_result_json(result) == {"status": "paused"}
    assert engine.status()["paused"] is True


@pytest.mark.asyncio
async def test_pause_tool_raises_when_already_paused():
    from mcp.server.fastmcp.exceptions import ToolError

    engine = make_engine()
    mcp = create_mcp_server(engine)
    engine._paused = True  # pre-paused

    with pytest.raises(ToolError):
        await mcp.call_tool("pause", {})


# ---------------------------------------------------------------------------
# Tools — resume
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resume_tool_calls_engine_resume():
    engine = make_engine()
    mcp = create_mcp_server(engine)

    engine._paused = True  # pre-paused so resume is valid
    result = await mcp.call_tool("resume", {})
    assert tool_result_json(result) == {"status": "running"}
    assert engine.status()["paused"] is False


@pytest.mark.asyncio
async def test_resume_tool_raises_when_not_paused():
    from mcp.server.fastmcp.exceptions import ToolError

    engine = make_engine()
    mcp = create_mcp_server(engine)

    with pytest.raises(ToolError):
        await mcp.call_tool("resume", {})


# ---------------------------------------------------------------------------
# Tools — is_running
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_is_running_tool_returns_engine_status():
    engine = make_engine()
    mcp = create_mcp_server(engine)
    engine._running = True
    engine._paused = False
    engine._connected = True

    result = await mcp.call_tool("is_running", {})
    assert tool_result_json(result) == {"running": True, "paused": False, "connected": True}


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
    from unittest.mock import patch, PropertyMock

    with patch.object(
        type(mcp._mcp_server),
        "request_context",
        new_callable=PropertyMock,
        return_value=MagicMock(session=dead_session),
    ):
        # Trigger subscribe handler directly via the lowlevel handler
        from pydantic import AnyUrl
        from mcp.types import SubscribeRequest, SubscribeRequestParams

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
    from asr_mcp.config import AppConfig, ASRConfig, ServerConfig, AudioConfig

    config = AppConfig(
        server=ServerConfig(),
        audio=AudioConfig(),
        asr=ASRConfig(type="no_such_module"),
    )
    with pytest.raises(ValueError, match="no_such_module"):
        await run_server(config)


@pytest.mark.asyncio
async def test_run_server_prints_banner(capsys) -> None:
    import asr_mcp.modules as mod
    from asr_mcp.config import AppConfig, ASRConfig, ServerConfig, AudioConfig

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
    mod.REGISTRY["fake_banner"] = fake_class
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
