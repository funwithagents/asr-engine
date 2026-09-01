---
code:
  - src/asr_engine/modules/base.py
  - src/asr_engine/modules/__init__.py
  - src/asr_engine/engine.py
tests:
  - tests/modules/test_base.py
  - tests/test_engine.py
---

# ASR Module Interface

**Status:** Implemented

## Purpose

The ASR module interface decouples the MCP server from any specific speech recognition backend. The engine loads one module at construction based on the `engine.module.type` config field.

## Abstract Base Class

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, Awaitable


@dataclass
class SpeechUtterance:
    transcript: str
    is_final: bool  # False = interim/partial, True = final
    confidence: float | None  # None if not provided by backend


# Callback type: called each time the module emits an utterance
UtteranceCallback = Callable[[SpeechUtterance], Awaitable[None]]
# Callback type: called when backend connection state changes (True=connected)
ConnectedCallback = Callable[[bool], None]


class ASRModule(ABC):
    # Declared audio-format capabilities (see "Audio Format Contract" below).
    SUPPORTED_SAMPLE_RATES: ClassVar[frozenset[int] | None]  # None = any
    SUPPORTED_CHANNELS: ClassVar[frozenset[int] | None]
    SUPPORTED_ENCODINGS: ClassVar[frozenset[str] | None]
    DEFAULT_SAMPLE_RATE: ClassVar[int]
    DEFAULT_CHANNELS: ClassVar[int]
    DEFAULT_ENCODING: ClassVar[str]

    def __init__(self, config: dict) -> None:
        self.config = config

    @abstractmethod
    async def start(
        self,
        audio_queue: asyncio.Queue[bytes],
        on_utterance: UtteranceCallback,
        on_connected: ConnectedCallback | None = None,
        *,
        audio_format: AudioFormat = DEFAULT_AUDIO_FORMAT,
    ) -> None:
        """
        Start the ASR module.

        - audio_queue: async queue of raw audio chunks in `audio_format`
        - on_utterance: async callback invoked for each interim or final utterance
        - on_connected: optional callback invoked with the backend connection
          state (True on connect, False on disconnect). Drives the `connected`
          field of the `is_running` tool.
        - audio_format: the reconciled `AudioFormat` (rate/channels/encoding) of
          the chunks on `audio_queue`; the module reports it to its backend.

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

The audio format is **configurable end-to-end** and carried by an `AudioFormat`
value object (`asr_engine.audio`):

```python
@dataclass(frozen=True)
class AudioFormat:
    sample_rate: int = 16000
    channels: int = 1
    encoding: str = "linear16"  # "linear16" (s16 PCM) | "mulaw" (G.711)
```

The **default** `AudioFormat` (16 kHz, mono, `linear16`, ~100 ms / 3,200-byte
chunks) is what every module receives unless `engine.audio` overrides it. Chunk
sizing derives from the format (`frames_per_chunk ≈ sample_rate × 0.1`).

**Modules declare what they support.** Every concrete module declares six
class attributes — `SUPPORTED_SAMPLE_RATES` / `SUPPORTED_CHANNELS` /
`SUPPORTED_ENCODINGS` (each a `frozenset`, or `None` meaning "any") and
`DEFAULT_SAMPLE_RATE` / `DEFAULT_CHANNELS` / `DEFAULT_ENCODING`. Declaration is
**required and enforced at import time**: `ASRModule.__init_subclass__` raises
`TypeError` if a concrete subclass omits any attribute, or declares a `DEFAULT_*`
outside its (non-`None`) `SUPPORTED_*` set. Abstract intermediate bases are
exempt.

**The engine reconciles** the configured `AudioFormat` against the selected
module's declared support via `reconcile_audio_format(desired, module_cls, *,
on_unsupported)` at construction. Per dimension: a supported value is kept; an
unsupported one either raises (`on_unsupported="error"`, the default) or falls
back to the module's default with a warning (`"fallback"`). The resolved format
is handed to both the capture layer and `start(..., audio_format=...)`.

**Who converts.** Live capture opens its stream at the resolved rate/channels
(PortAudio performs any device conversion) and transcodes the captured s16 to the
target encoding (`linear16` is a no-op; `mulaw` uses a numpy G.711 encoder —
`audioop` is not used, as it is removed in Python 3.13+). File sources (any
container/codec `libsndfile`/`soundfile` decodes — WAV, MP3, …) are decoded to
s16, validated against the resolved rate/channels (**not** resampled) and
re-encoded to `mulaw` when required.

## Module Registration

Modules are registered in a central registry mapping `type` string → module class:

```python
# src/asr_engine/modules/__init__.py
REGISTRY: dict[str, type[ASRModule]] = {
    "deepgram_v1": DeepgramV1Module,
    "deepgram_v2": DeepgramV2Module,
}
```

The engine loads the correct class from this registry using the
`engine.module.type` config value, then instantiates it with the remaining
module-specific fields (the `engine.module` block minus `type`).

## Module Constructor Contract

Each module is instantiated with the module-specific portion of the config:

```python
module = REGISTRY[asr_type](config=asr_config_dict)
```

Modules must validate their config in `__init__` and raise `ValueError` with a clear message if required fields are missing.

## API key resolution

`modules/base.py` provides a shared helper for modules that authenticate with an
API key, so keys can be kept out of committed config files:

```python
def resolve_api_key(config: dict, module_label: str) -> str: ...
```

Resolution precedence:

1. `config["api_key"]` — a literal key.
2. `config["api_key_env"]` — the **name** of an environment variable to read.

Raises `ValueError` if neither is provided, or if the named environment variable
is unset or empty. Modules call it in `__init__`
(e.g. `self._api_key = resolve_api_key(config, "deepgram_v1")`).

## Reconnection Contract

Each module is solely responsible for reconnecting to its backend on connection loss. The reconnection strategy should follow exponential backoff:

| Attempt | Delay |
|---|---|
| 1 | 1s |
| 2 | 2s |
| 3 | 4s |
| 4+ | 8s (max) |

While reconnecting, the module must continue draining `audio_queue` to prevent it from growing unbounded.
