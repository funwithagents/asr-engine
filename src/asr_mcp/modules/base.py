from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Awaitable, Callable


@dataclass
class ASRResult:
    transcript: str
    is_final: bool
    confidence: float | None  # None if not provided by backend


ResultCallback = Callable[[ASRResult], Awaitable[None]]
ConnectedCallback = Callable[[bool], None]


class ASRModule(ABC):
    def __init__(self, config: dict) -> None:
        self.config = config

    @abstractmethod
    async def start(
        self,
        audio_queue: asyncio.Queue[bytes],
        on_result: ResultCallback,
        on_connected: ConnectedCallback | None = None,
    ) -> None:
        """
        Start the ASR module.

        Runs indefinitely until stop() is called. Responsible for reconnecting
        to the backend on connection loss and draining audio_queue while reconnecting.
        """
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Stop the ASR module gracefully."""
        ...
