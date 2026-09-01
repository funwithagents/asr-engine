"""Demo CLI: subscribe to asr://utterance and log each update."""

from __future__ import annotations

import argparse
import asyncio

from examples.mcp_client.resource_client import AsrResourceClient

_DEFAULT_SERVER = "http://127.0.0.1:8000/mcp"


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
    """Start an AsrResourceClient and run until cancelled."""

    async def _on_event(payload: dict) -> None:
        print(_format_result(payload), flush=True)

    client = AsrResourceClient(server_url, _on_event)
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


if __name__ == "__main__":
    main()
