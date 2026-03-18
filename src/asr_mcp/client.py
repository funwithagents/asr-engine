from __future__ import annotations

import argparse
import asyncio
from typing import Awaitable, Callable

from asr_mcp.resource_subscriber import ResourceSubscriber

_RESOURCE_URI = "asr://result"
_DEFAULT_SERVER = "http://127.0.0.1:8000/mcp"


class AsrMcpClient:
    """MCP client for the ASR server.

    Subscribes to ``asr://result`` and calls *on_event* with the parsed JSON
    payload on each update. Reconnects automatically on connection loss.

    Use :meth:`start` / :meth:`stop` to manage the background subscription.
    """

    def __init__(
        self,
        server_url: str,
        on_event: Callable[[dict], Awaitable[None]],
    ) -> None:
        self._subscriber = ResourceSubscriber(server_url, _RESOURCE_URI, on_event)

    async def start(self) -> None:
        await self._subscriber.start()

    async def stop(self) -> None:
        await self._subscriber.stop()


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


async def _run_client(server_url: str) -> None:
    """Start an AsrMcpClient and run until cancelled."""
    async def _on_event(payload: dict) -> None:
        print(_format_result(payload), flush=True)

    client = AsrMcpClient(server_url, _on_event)
    await client.start()
    try:
        await asyncio.sleep(float("inf"))
    finally:
        await client.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="ASR MCP Demo Client")
    parser.add_argument(
        "--server",
        default=_DEFAULT_SERVER,
        help=f"MCP server URL (default: {_DEFAULT_SERVER})",
    )
    args = parser.parse_args()

    try:
        asyncio.run(_run_client(args.server))
    except KeyboardInterrupt:
        print("[INFO] Disconnected", flush=True)
