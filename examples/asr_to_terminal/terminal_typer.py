"""TerminalTyper: inject keystrokes into the active terminal window."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Protocol

log = logging.getLogger(__name__)


class KeystrokeSink(Protocol):
    """The keystroke operations ``AsrToTerminal`` drives.

    ``TerminalTyper`` is the production implementation (real OS injection); tests
    can supply an in-memory sink that records the same operations.
    """

    async def type_text(self, text: str) -> None: ...
    async def backspace(self, n: int) -> None: ...
    async def send_enter(self) -> None: ...


class TerminalTyper:
    """Thin wrapper around xdotool (X11) or ydotool (Wayland) for keystroke injection."""

    def __init__(self, display_server: str | None = None) -> None:
        if display_server is not None:
            resolved = display_server
        else:
            resolved = os.environ.get("XDG_SESSION_TYPE")
            if resolved is None:
                raise RuntimeError(
                    "Cannot detect display server: "
                    "pass display_server= or set $XDG_SESSION_TYPE"
                )

        resolved = resolved.lower()
        if resolved not in ("x11", "wayland"):
            raise RuntimeError(
                f"Unsupported display server {resolved!r}; expected 'x11' or 'wayland'"
            )
        self._display_server = resolved

    async def type_text(self, text: str) -> None:
        if not text:
            return
        if self._display_server == "x11":
            await self._run("xdotool", "type", "--clearmodifiers", "--", text)
        else:
            await self._run("ydotool", "type", "--key-delay", "0", "--", text)

    async def backspace(self, n: int) -> None:
        if n == 0:
            return
        if self._display_server == "x11":
            await self._run(
                "xdotool", "key", "--clearmodifiers", "--repeat", str(n), "BackSpace"
            )
        else:
            await self._run("ydotool", "key", "--repeat", str(n), "BackSpace")

    async def send_enter(self) -> None:
        if self._display_server == "x11":
            await self._run("xdotool", "key", "--clearmodifiers", "Return")
        else:
            await self._run("ydotool", "key", "Return")

    @staticmethod
    async def _run(*args: str) -> None:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            msg = stderr.decode().strip() if stderr else "(no stderr)"
            log.warning("%s exited %d: %s", args[0], proc.returncode, msg)
