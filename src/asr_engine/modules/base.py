from __future__ import annotations

import asyncio
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Awaitable, Callable


@dataclass
class SpeechUtterance:
    transcript: str
    is_final: bool
    confidence: float | None  # None if not provided by backend


UtteranceCallback = Callable[[SpeechUtterance], Awaitable[None]]
ConnectedCallback = Callable[[bool], None]


def resolve_api_key(config: dict, module_label: str) -> str:
    """Resolve an API key from a module config, so keys can stay out of config files.

    Precedence:
    1. ``config["api_key"]`` — a literal key.
    2. ``config["api_key_env"]`` — the name of an environment variable to read.

    Raises ``ValueError`` if neither is provided, or if the named environment
    variable is unset or empty.
    """
    api_key = config.get("api_key")
    if api_key:
        return api_key
    env_name = config.get("api_key_env")
    if env_name:
        value = os.environ.get(env_name)
        if not value:
            raise ValueError(
                f"{module_label} module: environment variable '{env_name}' "
                f"(api_key_env) is not set or is empty"
            )
        return value
    raise ValueError(
        f"{module_label} module requires 'api_key' or 'api_key_env' in config"
    )


class ASRModule(ABC):
    def __init__(self, config: dict) -> None:
        self.config = config

    @abstractmethod
    async def start(
        self,
        audio_queue: asyncio.Queue[bytes],
        on_utterance: UtteranceCallback,
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
