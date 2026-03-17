# ASR Module Interface

## Purpose

The ASR module interface decouples the MCP server from any specific speech recognition backend. The server loads one module at startup based on the `asr.type` config field.

## Abstract Base Class

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, Awaitable

@dataclass
class ASRResult:
    transcript: str
    is_final: bool
    confidence: float | None  # None if not provided by backend

# Callback type: called each time the module emits a result
ResultCallback = Callable[[ASRResult], Awaitable[None]]

class ASRModule(ABC):

    @abstractmethod
    async def start(self, audio_queue: asyncio.Queue[bytes], on_result: ResultCallback) -> None:
        """
        Start the ASR module.

        - audio_queue: async queue of raw PCM audio chunks (16-bit, mono, 16kHz)
        - on_result: async callback invoked for each interim or final transcript

        This method should run indefinitely until stop() is called.
        It is responsible for reconnecting to the backend on connection loss.
        """
        ...

    @abstractmethod
    async def stop(self) -> None:
        """
        Stop the ASR module gracefully.
        Should close the backend connection and stop consuming audio_queue.
        """
        ...
```

## Audio Format Contract

All ASR modules receive audio in the following format:
- **Encoding:** Raw PCM (signed 16-bit little-endian)
- **Sample rate:** 16,000 Hz
- **Channels:** 1 (mono)
- **Chunk size:** ~100ms of audio (1,600 samples = 3,200 bytes per chunk)

Audio capture is responsible for resampling to this format before placing chunks on the queue.

## Module Registration

Modules are registered in a central registry mapping `type` string → module class:

```python
# src/asr_mcp/modules/__init__.py
REGISTRY: dict[str, type[ASRModule]] = {
    "deepgram": DeepgramModule,
}
```

The server loads the correct class from this registry using the `asr.type` config value, then instantiates it with the full `asr` config dict (minus the `type` field).

## Module Constructor Contract

Each module is instantiated with the module-specific portion of the config:

```python
module = REGISTRY[asr_type](config=asr_config_dict)
```

Modules must validate their config in `__init__` and raise `ValueError` with a clear message if required fields are missing.

## Reconnection Contract

Each module is solely responsible for reconnecting to its backend on connection loss. The reconnection strategy should follow exponential backoff:

| Attempt | Delay |
|---|---|
| 1 | 1s |
| 2 | 2s |
| 3 | 4s |
| 4+ | 8s (max) |

While reconnecting, the module must continue draining `audio_queue` to prevent it from growing unbounded.
