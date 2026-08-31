from __future__ import annotations

from typing import Awaitable, Callable

from asr_mcp.resource_subscriber import ResourceSubscriber

_DEFAULT_RESOURCE_URI = "asr://utterance"


class AsrResourceClient:
    """MCP client for the ASR server.

    Subscribes to an ASR resource (``asr://utterance`` by default, or
    ``asr://segment``) and calls *on_event* with the parsed JSON payload on each
    update. Reconnects automatically on connection loss.

    Use :meth:`start` / :meth:`stop` to manage the background subscription.
    """

    def __init__(
        self,
        server_url: str,
        on_event: Callable[[dict], Awaitable[None]],
        resource_uri: str = _DEFAULT_RESOURCE_URI,
    ) -> None:
        self._subscriber = ResourceSubscriber(server_url, resource_uri, on_event)

    async def start(self) -> None:
        await self._subscriber.start()

    async def stop(self) -> None:
        await self._subscriber.stop()
