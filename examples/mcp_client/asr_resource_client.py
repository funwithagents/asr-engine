"""Demo CLI: subscribe to asr://utterance and log each update."""

from __future__ import annotations

import argparse
import asyncio
import logging

from examples.mcp_client.resource_client import AsrResourceClient

log = logging.getLogger(__name__)

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
        log.info("%s", _format_result(payload))

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

    # This example is an application entry point, so it configures logging itself
    # (deliberately not importing the library's private _logging.setup_logging).
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        asyncio.run(_run_client(args.server))
    except KeyboardInterrupt:
        log.info("Disconnected")


if __name__ == "__main__":
    main()
