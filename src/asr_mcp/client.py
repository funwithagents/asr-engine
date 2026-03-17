from __future__ import annotations

import argparse
import asyncio
import json
from typing import Awaitable, Callable

import mcp.types as types
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from pydantic import AnyUrl

_RESOURCE_URI = "asr://result"
_DEFAULT_SERVER = "http://127.0.0.1:8080/mcp"


class ResourceSubscriber:
    """Subscribes to an MCP resource and calls *on_event* for each update.

    *on_event* receives the parsed JSON payload (dict) of the resource on every
    ``ResourceUpdatedNotification``. It is called via ``asyncio.create_task`` so
    it must not block but may ``await`` freely.

    :meth:`run` connects, subscribes, and runs until cancelled. Unsubscribes
    cleanly on exit.
    """

    def __init__(
        self,
        server_url: str,
        resource_uri: str,
        on_event: Callable[[dict], Awaitable[None]],
    ) -> None:
        self._server_url = server_url
        self._resource_uri = resource_uri
        self._on_event = on_event

    async def run(self) -> None:
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


def _format_result(payload: dict) -> str:
    """Format an ASR result payload into a log line."""
    transcript = payload.get("transcript", "")
    is_final = payload.get("is_final", False)
    confidence = payload.get("confidence")

    if is_final:
        if confidence is not None:
            return f"[FINAL  ] {transcript} (confidence: {confidence})"
        return f"[FINAL  ] {transcript}"
    return f"[INTERIM] {transcript}"


async def _run(server_url: str) -> None:
    """Connect, subscribe, and stream results until cancelled."""
    print(f"[INFO] Connecting to MCP server at {server_url}", flush=True)

    async def _on_event(payload: dict) -> None:
        print(_format_result(payload), flush=True)

    await ResourceSubscriber(server_url, _RESOURCE_URI, _on_event).run()


async def _run_with_reconnect(server_url: str) -> None:
    """Run with automatic reconnection on connection loss."""
    while True:
        try:
            await _run(server_url)
        except asyncio.CancelledError:
            raise
        except Exception:
            print("[WARN] Connection lost, retrying...", flush=True)
            await asyncio.sleep(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="ASR MCP Demo Client")
    parser.add_argument(
        "--server",
        default=_DEFAULT_SERVER,
        help=f"MCP server URL (default: {_DEFAULT_SERVER})",
    )
    args = parser.parse_args()

    try:
        asyncio.run(_run_with_reconnect(args.server))
    except KeyboardInterrupt:
        print("[INFO] Disconnected", flush=True)
