from __future__ import annotations

from typing import Awaitable, Callable

from asr_mcp.resource_subscriber import ResourceSubscriber

_RESOURCE_URI = "asr://result"


class AsrResourceClient:
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
