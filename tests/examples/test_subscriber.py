"""Unit tests for subscriber.py — ResourceSubscriber."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import mcp.types as types
import pytest
from pydantic import AnyUrl

from examples.mcp_client.resource_subscriber import ResourceSubscriber

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_resource_notification(uri: str = "asr://result") -> types.ServerNotification:
    return types.ServerNotification(
        root=types.ResourceUpdatedNotification(
            params=types.ResourceUpdatedNotificationParams(uri=AnyUrl(uri))
        )
    )


def _make_mock_session(payload: dict | None = None) -> AsyncMock:
    session = AsyncMock()
    if payload is not None:
        content = types.TextResourceContents(
            uri=AnyUrl("asr://result"),
            mimeType="application/json",
            text=json.dumps(payload),
        )
        result = MagicMock()
        result.contents = [content]
        session.read_resource = AsyncMock(return_value=result)
    else:
        session.read_resource = AsyncMock()
    return session


class _SessionCM:
    """Context manager that yields a fixed mock session and captures message_handler."""

    def __init__(self, session: AsyncMock, handlers: list):
        self._session = session
        self._handlers = handlers

    def __call__(self, rs, ws, message_handler=None, **kwargs):
        self._handlers.append(message_handler)
        return self

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *args):
        pass


# ---------------------------------------------------------------------------
# start / stop
# ---------------------------------------------------------------------------


def _counting_failed_connections():
    """A transport that refuses every connection, counting the attempts, and a
    sleep that does not wait, so a retry would happen at once."""
    attempts: list[int] = []
    real_sleep = asyncio.sleep

    def refuse(*args, **kwargs):
        attempts.append(1)
        raise ConnectionError("down")

    async def fast_sleep(_seconds: float) -> None:
        await real_sleep(0)

    patches = (
        patch(
            "examples.mcp_client.resource_subscriber.streamable_http_client",
            side_effect=refuse,
        ),
        patch("examples.mcp_client.resource_subscriber.asyncio.sleep", new=fast_sleep),
    )
    return attempts, real_sleep, patches


@pytest.mark.asyncio
async def test_stop_ends_the_connection_attempts():
    attempts, real_sleep, (transport, sleep) = _counting_failed_connections()
    subscriber = ResourceSubscriber("http://x", "r://u", AsyncMock())
    with transport, sleep:
        await subscriber.start()
        while len(attempts) < 3:  # it keeps retrying while started
            await real_sleep(0)
        await subscriber.stop()
        after_stop = len(attempts)
        for _ in range(20):
            await real_sleep(0)
    assert len(attempts) == after_stop
    await subscriber.stop()  # a second stop is harmless


# ---------------------------------------------------------------------------
# on_event dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_event_called_on_resource_updated(capsys):
    """When a ResourceUpdatedNotification arrives, on_event is called with the payload."""
    payload = {"transcript": "hello world", "is_final": True, "confidence": 0.95}
    mock_session = _make_mock_session(payload)
    handlers: list = []

    collected: list[dict] = []

    async def _on_event(p: dict) -> None:
        collected.append(p)

    subscriber = ResourceSubscriber("http://x", "asr://result", _on_event)
    session_cm = _SessionCM(mock_session, handlers)

    with (
        patch(
            "examples.mcp_client.resource_subscriber.streamable_http_client"
        ) as mock_transport,
        patch("examples.mcp_client.resource_subscriber.ClientSession", session_cm),
    ):
        mock_transport.return_value.__aenter__ = AsyncMock(
            return_value=(MagicMock(), MagicMock(), lambda: None)
        )
        mock_transport.return_value.__aexit__ = AsyncMock(return_value=None)

        task = asyncio.create_task(subscriber._connect())
        await asyncio.sleep(0)

        if handlers:
            await handlers[0](_make_resource_notification())
            await asyncio.sleep(0)  # let create_task run _fetch

        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    assert len(collected) == 1
    assert collected[0]["transcript"] == "hello world"


@pytest.mark.asyncio
async def test_non_notification_ignored():
    """Non-notification messages do not trigger on_event."""
    mock_session = _make_mock_session()
    handlers: list = []

    called: list = []

    async def _on_event(p: dict) -> None:
        called.append(p)

    subscriber = ResourceSubscriber("http://x", "asr://result", _on_event)
    session_cm = _SessionCM(mock_session, handlers)

    with (
        patch(
            "examples.mcp_client.resource_subscriber.streamable_http_client"
        ) as mock_transport,
        patch("examples.mcp_client.resource_subscriber.ClientSession", session_cm),
    ):
        mock_transport.return_value.__aenter__ = AsyncMock(
            return_value=(MagicMock(), MagicMock(), lambda: None)
        )
        mock_transport.return_value.__aexit__ = AsyncMock(return_value=None)

        task = asyncio.create_task(subscriber._connect())
        await asyncio.sleep(0)

        if handlers:
            await handlers[0](Exception("noise"))

        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    mock_session.read_resource.assert_not_called()
    assert called == []


# ---------------------------------------------------------------------------
# Reconnection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconnects_on_error():
    """_loop retries after a connection error when reconnect=True."""
    call_count = 0
    real_sleep = asyncio.sleep  # capture before patching

    async def _on_event(p: dict) -> None:
        pass

    subscriber = ResourceSubscriber("http://x", "r://u", _on_event, reconnect=True)

    async def _flaky_connect(reconnecting: bool = False):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise ConnectionError("boom")
        await real_sleep(float("inf"))

    async def _fast_sleep(_seconds: float) -> None:
        # A real yield (sleep(0)) so the loop advances without a full second
        # between retries — an async replacement, so no coroutine is left
        # unawaited the way a sync lambda returning real_sleep(0) would.
        await real_sleep(0)

    with (
        patch.object(subscriber, "_connect", side_effect=_flaky_connect),
        patch(
            "examples.mcp_client.resource_subscriber.asyncio.sleep",
            new=_fast_sleep,
        ),
    ):
        task = asyncio.create_task(subscriber._loop())
        for _ in range(20):
            await real_sleep(0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert call_count >= 2


@pytest.mark.asyncio
async def test_no_reconnect_makes_a_single_attempt():
    attempts, real_sleep, (transport, sleep) = _counting_failed_connections()
    subscriber = ResourceSubscriber("http://x", "r://u", AsyncMock(), reconnect=False)
    with transport, sleep:
        await subscriber.start()
        for _ in range(20):  # a retry would happen at once
            await real_sleep(0)
        await subscriber.stop()  # does not raise the connection error
    assert len(attempts) == 1
