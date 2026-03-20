from __future__ import annotations

import json

from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.session import ProgressFnT


class McpToolClient:
    """Single-call MCP client: connect, call a tool, return the parsed result, disconnect.

    A new connection is opened per :meth:`call_tool` invocation.
    """

    def __init__(self, server_url: str) -> None:
        self._server_url = server_url

    async def call_tool(
        self,
        name: str,
        arguments: dict | None = None,
        progress_callback: ProgressFnT | None = None,
    ) -> dict:
        """Call *name* with *arguments* and return the parsed JSON result dict."""
        async with streamable_http_client(self._server_url) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(
                    name, arguments or {}, progress_callback=progress_callback
                )
                if result.isError:
                    texts = [c.text for c in result.content if hasattr(c, "text")]
                    raise RuntimeError(texts[0] if texts else "Tool returned an error")
                return json.loads(result.content[0].text)
