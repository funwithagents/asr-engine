"""AsrToTerminal: progressive speech-to-terminal injection with submit word detection."""
from __future__ import annotations

import argparse
import asyncio
import sys

from asr_mcp.resource_client import AsrResourceClient
from asr_mcp.speech_utils import contains_trigger_word
from asr_mcp.terminal_typer import TerminalTyper

_DEFAULT_SERVER = "http://127.0.0.1:8000/mcp"

_DEFAULT_SUBMIT_WORDS: list[str] = [
    "submit", "enter", "validate", "send", "confirm", "go",
    "envoyer", "valider", "confirmer", "soumettre", "entree", "entrée",
]


class AsrToTerminal:
    """Bridge between AsrResourceClient and the active terminal window.

    Progressively types interim transcripts (overwriting the previous interim
    on each update) and handles final results: either committing the text or
    triggering Enter when a submit word is detected.
    """

    def __init__(
        self,
        server_url: str = _DEFAULT_SERVER,
        submit_words: list[str] | None = None,
        display_server: str | None = None,
    ) -> None:
        self._submit_words = submit_words if submit_words is not None else _DEFAULT_SUBMIT_WORDS
        self._typer = TerminalTyper(display_server)
        self._client = AsrResourceClient(server_url, self._on_event)
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

    async def _on_event(self, payload: dict) -> None:
        transcript = payload.get("transcript", "")
        is_final = payload.get("is_final", False)

        async with self._lock:
            await self._handle_event(transcript, is_final)

    async def _handle_event(self, transcript: str, is_final: bool) -> None:
        if not is_final:
            # Interim: only retype the changed suffix
            backs, suffix = self._diff_pending(transcript)
            await self._typer.backspace(backs)
            await self._typer.type_text(suffix)
            self._pending = transcript
        elif contains_trigger_word(transcript, self._submit_words):
            # Final with submit word: erase interim and send Enter
            await self._typer.backspace(len(self._pending))
            await self._typer.send_enter()
            self._pending = ""
        else:
            # Final without submit word: commit the transcript
            backs, suffix = self._diff_pending(transcript)
            await self._typer.backspace(backs)
            await self._typer.type_text(suffix)
            self._pending = ""

    async def start(self) -> None:
        await self._client.start()

    async def stop(self) -> None:
        await self._client.stop()


async def _run(
    server_url: str,
    submit_words: list[str] | None,
    display_server: str | None,
) -> None:
    asr_to_terminal = AsrToTerminal(server_url, submit_words, display_server)
    await asr_to_terminal.start()
    print(f"[INFO] Connected to {server_url}", file=sys.stderr)
    try:
        await asyncio.sleep(float("inf"))
    finally:
        await asr_to_terminal.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="ASR to Terminal: type speech into the active terminal")
    parser.add_argument(
        "--server",
        default=_DEFAULT_SERVER,
        help=f"MCP server URL (default: {_DEFAULT_SERVER})",
    )
    parser.add_argument(
        "--submit-words",
        nargs="+",
        default=None,
        metavar="WORD",
        help="Words that trigger Enter instead of typing (replaces built-in defaults)",
    )
    parser.add_argument(
        "--display-server",
        choices=["x11", "wayland"],
        default=None,
        help="Display server to use (default: auto-detect via $XDG_SESSION_TYPE)",
    )
    args = parser.parse_args()

    try:
        asyncio.run(_run(args.server, args.submit_words, args.display_server))
    except KeyboardInterrupt:
        print("[INFO] Disconnected", file=sys.stderr)
