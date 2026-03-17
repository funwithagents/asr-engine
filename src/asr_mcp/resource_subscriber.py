from __future__ import annotations

import asyncio
import json
import logging
from typing import Awaitable, Callable

import mcp.types as types
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from pydantic import AnyUrl

log = logging.getLogger(__name__)


class ResourceSubscriber:
    """Subscribes to an MCP resource and calls *on_event* for each update.

    *on_event* receives the parsed JSON payload (dict) on every
    ``ResourceUpdatedNotification``. It is dispatched via ``asyncio.create_task``
    so it must not block but may ``await`` freely.

    Call :meth:`start` to begin subscribing in the background and :meth:`stop`
    to cancel cleanly. When *reconnect* is ``True`` (default), connection errors
    are retried automatically with a 1-second delay.
    """

    def __init__(
        self,
        server_url: str,
        resource_uri: str,
        on_event: Callable[[dict], Awaitable[None]],
        reconnect: bool = True,
    ) -> None:
        self._server_url = server_url
        self._resource_uri = resource_uri
        self._on_event = on_event
        self._reconnect = reconnect
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the subscriber as a background task."""
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        """Cancel the background task and wait for it to finish."""
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                await self._connect()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if not self._reconnect:
                    raise
                log.warning("Connection lost, retrying in 1 s: %s", exc)
                await asyncio.sleep(1)

    async def _connect(self) -> None:
        """Single connection attempt: connect, subscribe, run until cancelled."""
        session_holder: list[ClientSession | None] = [None]

        async def _on_message(
            msg: types.RequestResponder | types.ServerNotification | Exception,
        ) -> None:
            # Must return immediately — awaiting session methods here deadlocks
            # _receive_loop (the response would never be processed).
            if not isinstance(msg, types.ServerNotification):
                return
            if not isinstance(msg.root, types.ResourceUpdatedNotification):
                return
            session = session_holder[0]
            if session is None:
                return
            asyncio.create_task(_fetch(session))

        async def _fetch(session: ClientSession) -> None:
            try:
                result = await session.read_resource(AnyUrl(self._resource_uri))
                for content in result.contents:
                    if hasattr(content, "text"):
                        payload = json.loads(content.text)
                        await self._on_event(payload)
            except Exception:
                pass

        async with streamable_http_client(self._server_url) as (rs, ws, _):
            async with ClientSession(rs, ws, message_handler=_on_message) as session:
                session_holder[0] = session
                await session.initialize()
                await session.subscribe_resource(AnyUrl(self._resource_uri))
                try:
                    await asyncio.sleep(float("inf"))
                finally:
                    try:
                        await session.unsubscribe_resource(AnyUrl(self._resource_uri))
                    except Exception:
                        pass
