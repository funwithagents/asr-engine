"""AsrToTerminal: progressive speech-to-terminal injection with end-of-utterance detection."""
from __future__ import annotations

import argparse
import asyncio
import sys

from asr_mcp.end_of_utterance_detector import EndOfUtteranceDetector
from asr_mcp.modules.base import ASRResult
from asr_mcp.resource_client import AsrResourceClient
from asr_mcp.terminal_typer import TerminalTyper

_DEFAULT_SERVER = "http://127.0.0.1:8000/mcp"

_DEFAULT_TRIGGER_WORDS: list[str] = [
    "submit", "enter", "validate", "send", "confirm", "go",
    "envoyer", "valider", "confirmer", "soumettre", "entree", "entrée",
]


class AsrToTerminal:
    """Bridge between AsrResourceClient and the active terminal window.

    Progressively types interim transcripts (overwriting the previous interim
    on each update) and handles final results by committing the text. A background
    session loop uses EndOfUtteranceDetector to decide when to send Enter and start
    a fresh utterance cycle.
    """

    def __init__(
        self,
        server_url: str = _DEFAULT_SERVER,
        display_server: str | None = None,
        mode: str = "trigger_word",
        trigger_words: list[str] | None = None,
        end_of_speech_timeout_s: float = 5.0,
        initial_silence_timeout_s: float = 10.0,
    ) -> None:
        self._mode = mode
        self._trigger_words = trigger_words if trigger_words is not None else _DEFAULT_TRIGGER_WORDS
        self._end_of_speech_timeout_s = end_of_speech_timeout_s
        self._initial_silence_timeout_s = initial_silence_timeout_s
        self._typer = TerminalTyper(display_server)
        self._client = AsrResourceClient(server_url, self._on_event)
        self._pending: str = ""
        self._lock = asyncio.Lock()
        self._current_session: EndOfUtteranceDetector | None = None
        self._session_loop_task: asyncio.Task | None = None

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

        if self._current_session is not None:
            result = ASRResult(transcript=transcript, is_final=is_final, confidence=None)
            await self._current_session.on_result(result)

    async def _handle_event(self, transcript: str, is_final: bool) -> None:
        if not is_final:
            # Interim: only retype the changed suffix
            backs, suffix = self._diff_pending(transcript)
            await self._typer.backspace(backs)
            await self._typer.type_text(suffix)
            self._pending = transcript
        else:
            # Final: commit the transcript
            backs, suffix = self._diff_pending(transcript)
            await self._typer.backspace(backs)
            await self._typer.type_text(suffix)
            self._pending = ""

    async def _session_loop(self) -> None:
        """Continuously run utterance sessions; send Enter at each end-of-utterance."""
        try:
            while True:
                self._current_session = EndOfUtteranceDetector(
                    mode=self._mode,
                    trigger_words=self._trigger_words,
                    initial_silence_timeout_s=self._initial_silence_timeout_s,
                    end_of_speech_timeout_s=self._end_of_speech_timeout_s,
                )
                await self._current_session.wait()
                async with self._lock:
                    await self._typer.backspace(len(self._pending))
                    await self._typer.send_enter()
                    self._pending = ""
                self._current_session = None
        except asyncio.CancelledError:
            self._current_session = None

    async def start(self) -> None:
        self._session_loop_task = asyncio.get_event_loop().create_task(self._session_loop())
        await self._client.start()

    async def stop(self) -> None:
        await self._client.stop()
        if self._session_loop_task is not None:
            self._session_loop_task.cancel()
            try:
                await self._session_loop_task
            except asyncio.CancelledError:
                pass
            self._session_loop_task = None


async def _run(
    server_url: str,
    display_server: str | None,
    mode: str,
    trigger_words: list[str] | None,
    end_of_speech_timeout_s: float,
    initial_silence_timeout_s: float,
) -> None:
    asr_to_terminal = AsrToTerminal(
        server_url=server_url,
        display_server=display_server,
        mode=mode,
        trigger_words=trigger_words,
        end_of_speech_timeout_s=end_of_speech_timeout_s,
        initial_silence_timeout_s=initial_silence_timeout_s,
    )
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
        "--display-server",
        choices=["x11", "wayland"],
        default=None,
        help="Display server to use (default: auto-detect via $XDG_SESSION_TYPE)",
    )
    parser.add_argument(
        "--mode",
        choices=["trigger_word", "timeout"],
        default="trigger_word",
        help="End-of-utterance mode (default: trigger_word)",
    )
    parser.add_argument(
        "--trigger-words",
        nargs="+",
        default=None,
        metavar="WORD",
        help="Words that trigger Enter instead of typing (replaces built-in defaults)",
    )
    parser.add_argument(
        "--end-of-speech-timeout",
        type=float,
        default=5.0,
        metavar="SECONDS",
        help="Seconds of silence after speech before submitting (timeout mode, default: 5.0)",
    )
    parser.add_argument(
        "--initial-silence-timeout",
        type=float,
        default=10.0,
        metavar="SECONDS",
        help="Seconds of initial silence before giving up (timeout mode, default: 10.0)",
    )
    args = parser.parse_args()

    try:
        asyncio.run(_run(
            server_url=args.server,
            display_server=args.display_server,
            mode=args.mode,
            trigger_words=args.trigger_words,
            end_of_speech_timeout_s=args.end_of_speech_timeout,
            initial_silence_timeout_s=args.initial_silence_timeout,
        ))
    except KeyboardInterrupt:
        print("[INFO] Disconnected", file=sys.stderr)
