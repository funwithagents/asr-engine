from __future__ import annotations

import argparse
import asyncio
import json

import mcp.types as types
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from pydantic import AnyUrl

_RESOURCE_URI = "asr://result"
_DEFAULT_SERVER = "http://127.0.0.1:8080/mcp"


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


async def _fetch_and_print(session: ClientSession) -> None:
    """Read asr://result and print the formatted line. Runs as a standalone task."""
    try:
        result = await session.read_resource(AnyUrl(_RESOURCE_URI))
        for content in result.contents:
            if hasattr(content, "text"):
                payload = json.loads(content.text)
                print(_format_result(payload), flush=True)
    except Exception as exc:
        print(f"[ERROR] fetch: {exc}", flush=True)


async def _run(server_url: str) -> None:
    """Connect, subscribe, and stream results until cancelled."""
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
        asyncio.create_task(_fetch_and_print(session))

    async with streamable_http_client(server_url) as (rs, ws, _):
        async with ClientSession(rs, ws, message_handler=_on_message) as session:
            session_holder[0] = session
            await session.initialize()
            print(f"[INFO] Connected to MCP server at {server_url}", flush=True)
            await session.subscribe_resource(AnyUrl(_RESOURCE_URI))
            print(f"[INFO] Subscribed to {_RESOURCE_URI}", flush=True)
            try:
                await asyncio.sleep(float("inf"))
            finally:
                try:
                    await session.unsubscribe_resource(AnyUrl(_RESOURCE_URI))
                except Exception:
                    pass


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
