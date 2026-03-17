"""
Debug script — inspect a running ASR MCP server without relying on subscriptions.

1. Connects to the server and prints the engine status (is_running tool).
2. Polls asr://result every second and prints any change.

If results appear here but NOT in the demo client, the ASR pipeline is fine
but the subscription / push-notification path is broken.

If nothing appears here either, the problem is upstream: audio capture,
Deepgram connection, or the engine not running.

Usage:
    uv run python scripts/debug_server.py
    uv run python scripts/debug_server.py --server http://127.0.0.1:8000/mcp
"""

from __future__ import annotations

import argparse
import asyncio
import json

import mcp.types as types
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from pydantic import AnyUrl

_DEFAULT_SERVER = "http://127.0.0.1:8000/mcp"
_RESOURCE_URI = "asr://result"


async def _run(server_url: str) -> None:
    async with streamable_http_client(server_url) as (rs, ws, _):
        async with ClientSession(rs, ws) as session:
            await session.initialize()
            print(f"[debug] Connected to {server_url}\n")

            # 1. Check engine status
            print("=== Engine status (is_running tool) ===")
            result = await session.call_tool("is_running", {})
            status = json.loads(result.content[0].text)
            for k, v in status.items():
                print(f"  {k}: {v}")
            print()

            if not status.get("running"):
                print("[debug] Engine is NOT running — server may have crashed at startup.")
                return
            if not status.get("connected"):
                print("[debug] Engine is running but NOT connected to Deepgram.")
                print("[debug] Check your API key and network connectivity.")
                return
            if status.get("paused"):
                print("[debug] Engine is PAUSED — call the resume tool to start it.")
                return

            # 2. Poll asr://result directly (no subscription)
            print("=== Polling asr://result every second (speak now) ===")
            print("[debug] If transcripts appear here, ASR is working.")
            print("[debug] If they don't appear in the client, the issue is in push notifications.")
            print("[debug] Press Ctrl+C to stop.\n")

            last_transcript = None
            last_timestamp = None
            while True:
                try:
                    read_result = await session.read_resource(AnyUrl(_RESOURCE_URI))
                    for content in read_result.contents:
                        if hasattr(content, "text"):
                            payload = json.loads(content.text)
                            ts = payload.get("timestamp")
                            transcript = payload.get("transcript", "")
                            is_final = payload.get("is_final", False)
                            confidence = payload.get("confidence")

                            # Print only when the result changes
                            if ts != last_timestamp and transcript:
                                tag = "[FINAL  ]" if is_final else "[INTERIM]"
                                conf = f" (confidence: {confidence})" if is_final and confidence is not None else ""
                                print(f"  {tag} {transcript}{conf}")
                                last_transcript = transcript
                                last_timestamp = ts
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    print(f"[debug] Read error: {exc}")

                await asyncio.sleep(1.0)


def main() -> None:
    parser = argparse.ArgumentParser(description="ASR MCP server debug tool")
    parser.add_argument(
        "--server",
        default=_DEFAULT_SERVER,
        help=f"MCP server URL (default: {_DEFAULT_SERVER})",
    )
    args = parser.parse_args()

    try:
        asyncio.run(_run(args.server))
    except KeyboardInterrupt:
        print("\n[debug] Stopped.")


if __name__ == "__main__":
    main()
