---
code:
  - src/asr_engine/modules/base.py
  - src/asr_engine/modules/__init__.py
  - src/asr_engine/engine.py
  - pyproject.toml
tests:
  - tests/modules/test_base.py
  - tests/modules/test_registry.py
  - tests/test_engine.py
---

# ASR Module Interface

**Status:** Implemented

## Purpose

The ASR module interface decouples the engine (and the MCP server over it) from any specific speech recognition backend. The engine loads one module at construction based on the `engine.module.type` config field.

**No backend is the default.** `engine.module.type` is required and has no fallback. Real backends ship as **optional extras** (e.g. `asr-engine[deepgram]`), so the core install depends on no provider SDK; the only always-available module is the [`fake`](fake-module.md) test double.

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

The audio format is **configurable end-to-end** and carried by an `AudioFormat` value object (`asr_engine.audio`):

```python
@dataclass(frozen=True)
class AudioFormat:
    sample_rate: int = 16000
    channels: int = 1
    encoding: str = "linear16"  # "linear16" (s16 PCM) | "mulaw" (G.711)
```

The **default** `AudioFormat` (16 kHz, mono, `linear16`, ~100 ms / 3,200-byte chunks) is what every module receives unless `engine.audio` overrides it. Chunk sizing derives from the format (`frames_per_chunk ≈ sample_rate × 0.1`).

**Modules declare what they support.** Every concrete module declares six class attributes — `SUPPORTED_SAMPLE_RATES` / `SUPPORTED_CHANNELS` / `SUPPORTED_ENCODINGS` (each a `frozenset`, or `None` meaning "any") and `DEFAULT_SAMPLE_RATE` / `DEFAULT_CHANNELS` / `DEFAULT_ENCODING`. Declaration is **required and enforced at import time**: `ASRModule.__init_subclass__` raises `TypeError` if a concrete subclass omits any attribute, or declares a `DEFAULT_*` outside its (non-`None`) `SUPPORTED_*` set. Abstract intermediate bases are exempt.

**The engine reconciles** the configured `AudioFormat` against the selected module's declared support via `reconcile_audio_format(desired, module_cls, *, on_unsupported)` at construction. Per dimension: a supported value is kept; an unsupported one either raises (`on_unsupported="error"`, the default) or falls back to the module's default with a warning (`"fallback"`). The resolved format is handed to both the capture layer and `start(..., audio_format=...)`.

**Who converts.** Live capture opens its stream at the resolved rate/channels (PortAudio performs any device conversion) and transcodes the captured s16 to the target encoding (`linear16` is a no-op; `mulaw` uses a numpy G.711 encoder — `audioop` is not used, as it is removed in Python 3.13+). File sources (any container/codec `libsndfile`/`soundfile` decodes — WAV, MP3, …) are decoded to s16, validated against the resolved rate/channels (**not** resampled) and re-encoded to `mulaw` when required. An **injected** custom `AudioSource` (see [audio-source.md](audio-source.md)) must itself produce chunks already in the reconciled `AudioFormat` — the engine does not resample or re-encode chunks from an injected source.

## Module Registration

Modules are registered in a central registry mapping `type` string → module entry. Built-in entries are **lazy**: the registry names an import path and the extra that provides its dependencies, and nothing is imported until that type is selected — so `import asr_engine` never imports a provider SDK, and a missing extra only matters for the module that needs it.

```python
# src/asr_engine/modules/__init__.py
@dataclass(frozen=True)
class LazyModule:
    import_path: str  # "package.module:ClassName"
    extra: str | None = None  # pip extra providing its dependencies; None = core


REGISTRY: dict[str, LazyModule | type[ASRModule]] = {
    "fake": LazyModule("asr_engine.modules.fake:FakeASRModule"),
    "deepgram_v1": LazyModule(
        "asr_engine.modules.deepgram_v1:DeepgramV1Module", extra="deepgram"
    ),
    "deepgram_v2": LazyModule(
        "asr_engine.modules.deepgram_v2:DeepgramV2Module", extra="deepgram"
    ),
}


def resolve_module_class(module_type: str) -> type[ASRModule]: ...
def load_module(asr_config: dict) -> ASRModule: ...
```

- A value may also be an `ASRModule` subclass itself (eager). This is how a caller or a test registers a module it already imported (e.g. `patch.dict(REGISTRY, {"mock": MockModule})`).
- **`resolve_module_class(module_type)`** is the single resolution point, used by the engine and `load_module`:
  - unknown key → `ValueError("Unknown ASR type '<t>'. Available: <sorted keys>")` (unchanged message);
  - eager class → returned as is;
  - `LazyModule` → imports `import_path` and returns the class. If the import fails with `ModuleNotFoundError` for a **third-party** package (the error's `name` is not under `asr_engine`), it raises `ImportError` chaining the original, with a message naming the module type, the missing package, and the install command: `ASR module 'deepgram_v1' requires the optional dependency 'deepgram' which is not installed. Install it with: pip install 'asr-engine[deepgram]'` (or `uv sync --extra deepgram`). A `ModuleNotFoundError` for an `asr_engine` module itself is a packaging bug and propagates unchanged.
- **Listing does not import.** `sorted(REGISTRY)` lists every registered type, installed or not; config validation (`validate_asr_type`) checks key membership only, so a config naming an uninstalled module still *parses*. The missing extra surfaces at engine construction (`ASREngine(config)` → `resolve_module_class`), which the MCP server does at startup — so `asr-engine-mcp` still fails fast, printing the install hint and exiting 1.
- The engine calls `resolve_module_class(config.module.type)` then instantiates with the module-specific fields (the `engine.module` block minus `type`).

## Optional dependencies (extras)

Each real backend's third-party dependencies live in a `[project.optional-dependencies]` extra, named after the provider, never in core `dependencies`:

| Extra | Modules | Dependencies |
|---|---|---|
| `deepgram` | `deepgram_v1`, `deepgram_v2` | `deepgram-sdk` |
| *(core)* | `fake` | — |

A module file may import its SDK at module top level — the lazy registry guarantees it is only imported when selected. Adding a backend with new dependencies means adding an extra and naming it in the `LazyModule` entry.

## Module Constructor Contract

Each module is instantiated with the module-specific portion of the config:

```python
module = resolve_module_class(asr_type)(config=asr_config_dict)
```

Modules must validate their config in `__init__` and raise `ValueError` with a clear message if required fields are missing.

## API key resolution

`modules/base.py` provides a shared helper for modules that authenticate with an API key, so keys can be kept out of committed config files:

```python
def resolve_api_key(config: dict, module_label: str) -> str: ...
```

Resolution precedence:

1. `config["api_key"]` — a literal key.
2. `config["api_key_env"]` — the **name** of an environment variable to read.

Raises `ValueError` if neither is provided, or if the named environment variable is unset or empty. Modules call it in `__init__` (e.g. `self._api_key = resolve_api_key(config, "deepgram_v1")`).

## Reconnection Contract

Each module is solely responsible for reconnecting to its backend on connection loss. The reconnection strategy should follow exponential backoff:

| Attempt | Delay |
|---|---|
| 1 | 1s |
| 2 | 2s |
| 3 | 4s |
| 4+ | 8s (max) |

While reconnecting, the module must continue draining `audio_queue` to prevent it from growing unbounded.
