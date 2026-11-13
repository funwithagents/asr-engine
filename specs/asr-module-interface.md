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

**No backend is the default.** `engine.module.type` is required and has no fallback. Real backends ship as **optional extras** (e.g. `asr-engine[deepgram]`), so the core install depends on no provider SDK; the only always-available module is the [`fake`](modules/fake.md) test double.

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

    # How the module relates to VAD gating (see "VAD gating" below).
    VAD_GATING: ClassVar[VadGating] = "unsupported"

    def __init__(self, config: dict) -> None:
        self.config = config

    @abstractmethod
    async def start(
        self,
        audio_queue: AudioQueue,  # asyncio.Queue[bytes | EndOfSpeech]
        on_utterance: UtteranceCallback,
        on_connected: ConnectedCallback | None = None,
        *,
        audio_format: AudioFormat = DEFAULT_AUDIO_FORMAT,
    ) -> None:
        """
        Start the ASR module.

        - audio_queue: async queue of raw audio chunks in `audio_format`. A
          module whose VAD_GATING is not "unsupported" may also read
          END_OF_SPEECH markers from it (only when a VAD is configured).
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

    async def finalize(self) -> None:
        """
        The audio received so far ends an utterance: emit its final and be
        ready for the next one. Default: no-op. See "VAD gating".
        """
```

## VAD gating

When the engine is configured with a VAD ([vad.md](vad.md)), a speech gate sits between the audio source and the module: the module receives only the speech, and an in-band `END_OF_SPEECH` marker (`asr_engine.audio`, also exported from `asr_engine`) right after each utterance's last chunk. The full design, and what each bundled module does, is in [vad.md](vad.md) "End of speech"; the contract for a module author is:

- **`VAD_GATING`** declares the module's relation to the gate. Unlike the audio-format attributes it has a default, so a module written before VAD existed keeps working unchanged.

  | Value | Meaning | Engine check at construction |
  |---|---|---|
  | `"unsupported"` (default) | The module never receives a marker; its queue holds only `bytes`. | `engine.vad` set → `ValueError` |
  | `"supported"` | Handles the marker; works with or without a VAD. | — |
  | `"required"` | Produces no final without the marker (a batch backend). | no `engine.vad` → `ValueError` |

- **The module handles the marker in its own drain loop**, where its connection or model is in scope: on `END_OF_SPEECH` it calls `self.finalize()` instead of sending audio. The engine never calls `finalize()`.
- **`finalize()`** makes the backend emit the final for the audio received so far, and leaves the module ready for the next utterance on the same connection or session. It may return before the final has been emitted (the final can arrive through the normal receive path) and must not block the drain loop for long.
- A marker read while the module is discarding audio (reconnect backoff, model loading) is discarded with it.
- There is no start-of-utterance signal: an utterance begins with the first chunk after the previous marker. During a gap no chunk arrives, and the module waits on the queue as it would on an idle source (keep-alives and idle handling apply as usual).

All five bundled modules declare `"supported"`.

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
    "fake": LazyModule("asr_engine.modules.fake.module:FakeASRModule"),
    "deepgram_v1": LazyModule(
        "asr_engine.modules.deepgram.v1:DeepgramV1Module", extra="deepgram"
    ),
    "deepgram_v2": LazyModule(
        "asr_engine.modules.deepgram.v2:DeepgramV2Module", extra="deepgram"
    ),
    "mistral_realtime": LazyModule(
        "asr_engine.modules.mistral.realtime:MistralRealtimeModule", extra="mistral"
    ),
    "openai_realtime_compatible": LazyModule(
        "asr_engine.modules.openai_realtime_compatible.module:"
        "OpenAIRealtimeCompatibleModule",
        extra="openai-realtime-compatible",
    ),
    "mlx_audio": LazyModule(
        "asr_engine.modules.mlx_audio.module:MlxAudioModule"
    ),  # see below
    "kyutai": LazyModule("asr_engine.modules.kyutai.module:KyutaiModule"),  # see below
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

Each real backend's third-party dependencies live in a `[project.optional-dependencies]` extra, never in core `dependencies`. The extra is named after what the user installs it for:

- a **provider module** (one vendor's API or one model family) is named after the provider and API (`deepgram_v1`, `mistral_realtime`) or the model family (`kyutai`), and its extra after the provider (`deepgram`, `mistral`, `kyutai-mlx`);
- a **runner module** (a generic model runner that serves many model families, the model being a config value) is named after the runner (`mlx_audio`), and its extra after the module key (`mlx-audio`);
- a **protocol module** (any server implementing a de-facto standard API) is named `<origin>_<api>_compatible` (`openai_realtime_compatible`), and its extra after the module key with `_` → `-` (`openai-realtime-compatible`), even when another extra lists the same packages. The suffix leaves the plain name (`openai_realtime`) free for a vendor-only module.

| Extra | Modules | Dependencies |
|---|---|---|
| `deepgram` | `deepgram_v1`, `deepgram_v2` | `deepgram-sdk` |
| `kyutai-mlx` | `kyutai` (MLX backend) | `moshi-mlx`, marker-guarded to Apple Silicon |
| `kyutai-torch` | `kyutai` (PyTorch backend) | `moshi` |
| `mistral` | `mistral_realtime` | `websockets` |
| `openai-realtime-compatible` | `openai_realtime_compatible` | `websockets` |
| `mlx-audio` | `mlx_audio` | `mlx-audio[stt]`, marker-guarded to Apple Silicon; **exclusive with `kyutai-mlx` / `kyutai-torch`** ([project.md](project.md) "Exclusive extras") |
| *(core)* | `fake` | — |

The optional VAD stage follows the same rule with its own registry: the `silero` extra (`onnxruntime`, `soxr`) serves `engine.vad.type: "silero"` ([vad.md](vad.md)).

A module file may import its SDK at module top level — the lazy registry guarantees it is only imported when selected. Adding a backend with new dependencies means adding an extra and naming it in the `LazyModule` entry.

**A module that raises its own hint.** `mlx_audio` is registered with `extra=None` too: its extra is Apple Silicon only and exclusive with the Kyutai extras, so its `module.py` imports nothing third-party and raises an `ImportError` that says so ([modules/mlx-audio.md](modules/mlx-audio.md)).

**One module over several extras.** A `LazyModule` names a single `extra`, which cannot describe `kyutai`: one registry key over two interchangeable backends, each with its own extra. It is therefore registered with `extra=None`, its module file imports no third-party package, and it resolves its backend in `__init__`, raising its own `ImportError` worded like the registry's (see [modules/kyutai.md](modules/kyutai.md) "Backend selection"). The error still surfaces at engine construction, so the fail-fast behaviour is unchanged.

### Adding a module (checklist)

1. Create a package `src/asr_engine/modules/<backend>/` — **one folder per backend**, never a loose file next to `base.py`. Put the `ASRModule` subclass in a file inside it (declare its supported/default audio formats — see "Audio Format Contract"): `module.py` for a single-module backend (`fake/`, `kyutai/`), or one file per module when a backend ships several (`deepgram/v1.py`, `deepgram/v2.py`). Its `__init__.py` may re-export the public names, but only when that imports nothing the registry is supposed to keep lazy — `deepgram/__init__.py` imports neither of its modules, so selecting `deepgram_v1` never imports `v2`.
2. Register it lazily in `modules/__init__.py`: `REGISTRY["<name>"] = LazyModule("asr_engine.modules.<backend>.<file>:<ClassName>", extra="<extra>")`. Put its third-party dependencies in an extra named per the rule above (`<provider>`, or the module key for a protocol module) under `[project.optional-dependencies]` in `pyproject.toml` (never in core `dependencies`), and **add it to the `all` extra** — `all` means every extra, no exceptions. Then decide *separately* whether the `dev` group needs it: add it there too when contributors must have the module type-checked and tested (the usual case), or leave it out when it is heavy enough that not every contributor should pay for it (see [project.md](project.md) "`all` means every extra").
3. Document its config fields (the `engine.module` block accepts any fields beyond `type`); if it authenticates, resolve the key through `resolve_api_key` (`api_key` / `api_key_env`).
4. Add the backend's spec as `specs/modules/<backend>.md` (or update the existing one, e.g. [modules/deepgram.md](modules/deepgram.md)), with its frontmatter `code:`/`tests:` lists, and a row in [_index.md](_index.md).
5. Decide its `VAD_GATING` (see "VAD gating"): implement `finalize()` and the marker branch in the drain loop and declare `"supported"` — or `"required"` for a backend with no endpointing of its own. A batch model subclasses `BatchASRModule` ([batch-asr-module.md](batch-asr-module.md)), which is `"required"` and only asks for `load()` and `transcribe()`. Leaving the default `"unsupported"` makes `engine.vad` a configuration error with this module.
6. Add a row to the `MODULES` table in `tests-e2e/helpers.py` (module type, model, `api_key_env`, a `silence_s` matching how long the backend needs to finalize an utterance, and the `ModuleAudio` — format + fixtures — to drive it with; a local-model module also gets a `LOCAL_MODEL_OPT_IN_ENV` entry) so `test_engine_modules.py` gives it e2e conformance coverage, ungated and (when it supports gating) behind the VAD gate (see [e2e-testing.md](e2e-testing.md)). Verify with `zsh -ic 'uv run pytest tests-e2e -k <name>'`.

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

While reconnecting, the module must continue draining `audio_queue` to prevent it from growing unbounded. `END_OF_SPEECH` markers drained this way are dropped.
