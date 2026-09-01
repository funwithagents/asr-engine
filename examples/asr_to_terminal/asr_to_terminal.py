"""AsrToTerminal: progressive speech-to-terminal injection driven by asr://segment.

Segmentation is owned by the server's ASREngine and exposed as the
``asr://segment`` resource (see specs/engine.md). This client just types each
segment's transcript as it changes and sends Enter when the segment closes.
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from examples.asr_to_terminal.terminal_typer import KeystrokeSink, TerminalTyper
from examples.mcp_client.resource_client import AsrResourceClient

log = logging.getLogger(__name__)

_DEFAULT_SERVER = "http://127.0.0.1:8000/mcp"
_SEGMENT_URI = "asr://segment"


class AsrToTerminal:
    """Bridge between the server's ``asr://segment`` resource and the terminal.

    Progressively types the current segment transcript (overwriting the changed
    suffix on each update) and, when a segment closes (``is_final``), sends Enter
    and starts fresh. No segmentation logic lives here — the server decides when
    to close segments (its dictation / ``dictation_default_segmentation_mode``
    config; see specs/configuration.md).
    """

    def __init__(
        self,
        server_url: str = _DEFAULT_SERVER,
        display_server: str | None = None,
        typer: KeystrokeSink | None = None,
    ) -> None:
        # A caller may inject a KeystrokeSink (e.g. a recording sink in tests);
        # otherwise build the real OS-injection TerminalTyper.
        self._typer: KeystrokeSink = (
            typer if typer is not None else TerminalTyper(display_server)
        )
        self._client = AsrResourceClient(
            server_url, self._on_segment, resource_uri=_SEGMENT_URI
        )
        self._pending: str = ""
        self._lock = asyncio.Lock()

    def _diff_pending(self, new: str) -> tuple[int, str]:
        """Return (backspaces_needed, suffix_to_type) to transition from _pending to new."""
        common = 0
        for a, b in zip(self._pending, new):
            if a != b:
                break
            common += 1
        return len(self._pending) - common, new[common:]

    async def _on_segment(self, payload: dict) -> None:
        transcript = payload.get("transcript", "")
        is_final = payload.get("is_final", False)

        async with self._lock:
            # Type the diff from what's currently shown to the new transcript.
            backs, suffix = self._diff_pending(transcript)
            await self._typer.backspace(backs)
            await self._typer.type_text(suffix)
            self._pending = transcript

            if is_final:
                await self._typer.send_enter()
                self._pending = ""
                log.info("enter: %s (%s)", transcript, payload.get("end_reason"))
            else:
                log.info("segment: %s", transcript)

    async def start(self) -> None:
        await self._client.start()

    async def stop(self) -> None:
        await self._client.stop()


async def _run(server_url: str, display_server: str | None) -> None:
    asr_to_terminal = AsrToTerminal(
        server_url=server_url,
        display_server=display_server,
    )
    await asr_to_terminal.start()
    # The subscriber logs the connection lifecycle (connected / subscribed /
    # reconnected / lost); nothing to print here.
    try:
        await asyncio.sleep(float("inf"))
    finally:
        await asr_to_terminal.stop()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="ASR to Terminal: type speech into the active terminal"
    )
    parser.add_argument(
        "--server",
        default=_DEFAULT_SERVER,
        help=f"MCP server URL (default: {_DEFAULT_SERVER})",
    )
    parser.add_argument(
        "--display-server",
        choices=["x11", "wayland"],
        default=None,
        help="Display server to use (default: auto-detect via $XDG_SESSION_TYPE)",
    )
    args = parser.parse_args()

    # This example is an application entry point, so it configures logging itself
    # (deliberately not importing the library's private _logging.setup_logging).
    # basicConfig writes to stderr, so diagnostics never pollute the typed window.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        asyncio.run(_run(server_url=args.server, display_server=args.display_server))
    except KeyboardInterrupt:
        log.info("Disconnected")


if __name__ == "__main__":
    main()
