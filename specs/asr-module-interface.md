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
        - audio_format: the module format (rate/channels/encoding) of
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

A format is carried by an `AudioFormat` value object (`asr_engine.audio`):

```python
@dataclass(frozen=True)
class AudioFormat:
    sample_rate: int = 16000
    channels: int = 1
    encoding: str = "linear16"  # "linear16" (s16 PCM) | "mulaw" (G.711)
```

The engine keeps two of them ([format-conversion.md](format-conversion.md)): the **source format** (`engine.audio_format`), which the audio source produces, and the **module format** (`engine.module_audio_format`), which the module is fed. Chunk sizing derives from the format (`frames_per_chunk ≈ sample_rate × 0.1`, so ~100 ms).

**Modules declare what they support.** Every concrete module declares six class attributes — `SUPPORTED_SAMPLE_RATES` / `SUPPORTED_CHANNELS` / `SUPPORTED_ENCODINGS` (each a `frozenset`, or `None` meaning "any") and `DEFAULT_SAMPLE_RATE` / `DEFAULT_CHANNELS` / `DEFAULT_ENCODING`. Declaration is **required and enforced at import time**: `ASRModule.__init_subclass__` raises `TypeError` if a concrete subclass omits any attribute, or declares a `DEFAULT_*` outside its (non-`None`) `SUPPORTED_*` set. Abstract intermediate bases are exempt.

**An instance can narrow or widen the rates.** The engine reads the rates through two instance properties, `supported_sample_rates` and `default_sample_rate`, which return the class attributes by default. A protocol module whose servers differ overrides them from its config with `parse_sample_rate_overrides` ([format-conversion.md](format-conversion.md#per-instance-rates)).

**The engine resolves the module format** at construction with `resolve_module_audio_format(module, sample_rate=, channels=, encoding=)`: the module's default rate, channel count and encoding, unless a field of `engine.module.audio_format` sets another value, which must be one the module supports (`ValueError` otherwise). The source's format plays no part. The module format is what `start(..., audio_format=...)` receives.

**Who converts.** When the two formats differ, the engine puts a converter between the source and the module: it resamples, downmixes or duplicates channels, and re-encodes ([format-conversion.md](format-conversion.md)). A module never converts and never sees the source format. The bundled sources produce the source format themselves: live capture opens its stream at that rate and channel count (PortAudio performs any device conversion), file sources (any container/codec `libsndfile`/`soundfile` decodes — WAV, MP3, …) are decoded to s16 at their own rate, and both encode to `mulaw` when the source format says so (a numpy G.711 encoder — `audioop` is not used, as it is removed in Python 3.13+). An **injected** `AudioSource` (see [audio-source.md](audio-source.md)) produces `engine.audio_format`.

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
    "deepgram-v1": LazyModule(
        "asr_engine.modules.deepgram.v1:DeepgramV1Module", extra="deepgram"
    ),
    "deepgram-v2": LazyModule(
        "asr_engine.modules.deepgram.v2:DeepgramV2Module", extra="deepgram"
    ),
    "mistral-realtime": LazyModule(
        "asr_engine.modules.mistral.realtime:MistralRealtimeModule", extra="mistral"
    ),
    "openai-realtime-compatible": LazyModule(
        "asr_engine.modules.openai_realtime_compatible.module:"
        "OpenAIRealtimeCompatibleModule",
        extra="openai-compatible",
    ),
    "openai-transcriptions-compatible": LazyModule(
        "asr_engine.modules.openai_transcriptions_compatible.module:"
        "OpenAITranscriptionsCompatibleModule",
        extra="openai-compatible",
    ),
    "mlx-audio": LazyModule(
        "asr_engine.modules.mlx_audio.module:MlxAudioModule"
    ),  # see below
    "kyutai": LazyModule("asr_engine.modules.kyutai.module:KyutaiModule"),  # see below
}


RENAMED_MODULE_TYPES: dict[str, str] = {...}  # old key → new key, see "Module names"


def canonical_module_type(module_type: str) -> str: ...
def resolve_module_class(module_type: str) -> type[ASRModule]: ...
def load_module(asr_config: dict) -> ASRModule: ...
```

- A value may also be an `ASRModule` subclass itself (eager). This is how a caller or a test registers a module it already imported (e.g. `patch.dict(REGISTRY, {"mock": MockModule})`).
- **`resolve_module_class(module_type)`** is the single resolution point, used by the engine and `load_module`:
  - an old key (see "Module names") → resolved as its new key, with a deprecation warning;
  - unknown key → `ValueError("Unknown ASR type '<t>'. Available: <sorted keys>")` (unchanged message);
  - eager class → returned as is;
  - `LazyModule` → imports `import_path` and returns the class. If the import fails with `ModuleNotFoundError` for a **third-party** package (the error's `name` is not under `asr_engine`), it raises `ImportError` chaining the original, with a message naming the module type, the missing package, and the install command: `ASR module 'deepgram-v1' requires the optional dependency 'deepgram' which is not installed. Install it with: pip install 'asr-engine[deepgram]'` (or `uv sync --extra deepgram`). A `ModuleNotFoundError` for an `asr_engine` module itself is a packaging bug and propagates unchanged.
- **Listing does not import.** `sorted(REGISTRY)` lists every registered type, installed or not, under its current name only. Config validation (`validate_asr_type`) checks key membership only, accepting an old key, so a config naming an uninstalled module still *parses*. The missing extra surfaces at engine construction (`ASREngine(config)` → `resolve_module_class`), which the MCP server does at startup — so `asr-engine-mcp` still fails fast, printing the install hint and exiting 1.
- The engine calls `resolve_module_class(config.module.type)` then instantiates with the module-specific fields (the `engine.module` block minus `type`).

## Module names

A registry key is a user-facing name written in a config file, not a Python identifier, so it is spelled with `-` and never `_`:

- a **provider module** (one vendor's API or one model family) is named after the provider and API (`deepgram-v1`, `mistral-realtime`) or the model family (`kyutai`);
- a **runner module** (a generic model runner that serves many model families, the model being a config value) is named after the runner (`mlx-audio`);
- a **protocol module** (any server implementing a de-facto standard API) is named `<origin>-<api>-compatible` (`openai-realtime-compatible`, `openai-transcriptions-compatible`). The suffix leaves the plain name (`openai-realtime`) free for a vendor-only module.

The rule covers registry keys: `engine.module.type` here, and `engine.vad.type` in the VAD registry ([vad.md](vad.md)), whose only key, `silero`, needed no change. Enumerated mode values such as the segmentation modes (`trigger_word`, [engine.md](engine.md)) and values passed through to a provider (`server_vad`, `near_field`) are not registry keys and keep their spelling. Python package and directory names (`modules/openai_realtime_compatible/`) are identifiers and keep `_`.

**Old names keep working.** Keys were first spelled with `_`. `RENAMED_MODULE_TYPES` in `modules/__init__.py` maps each old key to its new one, and `canonical_module_type(t)` applies it, logging a warning (`ASR module type 'deepgram_v1' is deprecated, use 'deepgram-v1' instead`) on the `asr_engine.modules` logger. It is applied where a module type enters the library: `ASREngineConfig.from_dict` stores the new key in `config.module.type`, so the engine, its log lines and the demos only see new keys, and `resolve_module_class` and `validate_asr_type` also accept an old key passed to them directly. The table only grows: a renamed key stays in it.

| Old key | New key |
|---|---|
| `deepgram_v1` | `deepgram-v1` |
| `deepgram_v2` | `deepgram-v2` |
| `mistral_realtime` | `mistral-realtime` |
| `mlx_audio` | `mlx-audio` |
| `openai_realtime_compatible` | `openai-realtime-compatible` |
| `openai_transcriptions_compatible` | `openai-transcriptions-compatible` |

A later rename of a VAD key would get the same table and warning in the VAD registry.

## Optional dependencies (extras)

Each real backend's third-party dependencies live in a `[project.optional-dependencies]` extra, never in core `dependencies`. The extra is named after what the user installs it for:

- a **provider module**'s extra is named after the provider (`deepgram`, `mistral`), or the model family and its backend (`kyutai-mlx`);
- a **runner module**'s extra is named after the runner (`mlx-audio`);
- **every protocol module of one origin shares one extra**, `<origin>-compatible`: `openai-compatible` holds the dependencies of all the OpenAI-protocol modules (`websockets` for `openai-realtime-compatible`, `httpx` for `openai-transcriptions-compatible`). One short, stable name covers the whole protocol family, so adding another OpenAI-protocol module never adds an extra. The dependencies are small (`websockets` ≈ 1.1 MB installed, `httpx` and its dependencies ≈ 3.5 MB), so a user of one protocol module installing the other's is no real cost. If they ever need splitting, explicit per-module extras are added and `openai-compatible` becomes the bundle that lists them. The per-module extras that came before it (`openai-realtime-compatible`, `openai-transcriptions-compatible`, from 2026-11-13) were removed without aliases: they had shipped only days before.

| Extra | Modules | Dependencies |
|---|---|---|
| `deepgram` | `deepgram-v1`, `deepgram-v2` | `deepgram-sdk` |
| `kyutai-mlx` | `kyutai` (MLX backend) | `moshi-mlx`, marker-guarded to Apple Silicon; **exclusive with `mlx-audio`**, not in `all` / `dev` ([project.md](project.md) "Exclusive extras") |
| `kyutai-torch` | `kyutai` (PyTorch backend) | `moshi`; **exclusive with `mlx-audio`**, not in `all` / `dev` |
| `mistral` | `mistral-realtime` | `websockets` |
| `openai-compatible` | `openai-realtime-compatible`, `openai-transcriptions-compatible` | `websockets`, `httpx` |
| `mlx-audio` | `mlx-audio` | `mlx-audio[stt]`, marker-guarded to Apple Silicon; exclusive with the Kyutai extras |
| *(core)* | `fake` | — |

The optional VAD stage follows the same rule with its own registry: the `silero` extra (`onnxruntime`, `soxr`) serves `engine.vad.type: "silero"` ([vad.md](vad.md)).

A module file may import its SDK at module top level — the lazy registry guarantees it is only imported when selected. Adding a backend with new dependencies means adding an extra and naming it in the `LazyModule` entry.

**A module that raises its own hint.** `mlx-audio` is registered with `extra=None` too: its extra is Apple Silicon only, so its `module.py` imports nothing third-party and raises an `ImportError` that says so, which also keeps the module testable on every platform ([modules/mlx-audio.md](modules/mlx-audio.md)).

**One module over several extras.** A `LazyModule` names a single `extra`, which cannot describe `kyutai`: one registry key over two interchangeable backends, each with its own extra. It is therefore registered with `extra=None`, its module file imports no third-party package, and it resolves its backend in `__init__`, raising its own `ImportError` worded like the registry's (see [modules/kyutai.md](modules/kyutai.md) "Backend selection"). The error still surfaces at engine construction, so the fail-fast behaviour is unchanged.

### Adding a module (checklist)

1. Create a package `src/asr_engine/modules/<backend>/` — **one folder per backend**, never a loose file next to `base.py`. Put the `ASRModule` subclass in a file inside it (declare its supported/default audio formats — see "Audio Format Contract"): `module.py` for a single-module backend (`fake/`, `kyutai/`), or one file per module when a backend ships several (`deepgram/v1.py`, `deepgram/v2.py`). Its `__init__.py` may re-export the public names, but only when that imports nothing the registry is supposed to keep lazy — `deepgram/__init__.py` imports neither of its modules, so selecting `deepgram-v1` never imports `v2`.
2. Register it lazily in `modules/__init__.py`: `REGISTRY["<name>"] = LazyModule("asr_engine.modules.<backend>.<file>:<ClassName>", extra="<extra>")`, the `<name>` spelled with hyphens (see "Module names"). Put its third-party dependencies in an extra named per the rule above (`<provider>`, or the shared `<origin>-compatible` extra for a protocol module, adding its packages there) under `[project.optional-dependencies]` in `pyproject.toml` (never in core `dependencies`), and **add it to the `all` extra** — `all` means every extra, no exceptions. Then decide *separately* whether the `dev` group needs it: add it there too when contributors must have the module type-checked and tested (the usual case), or leave it out when it is heavy enough that not every contributor should pay for it (see [project.md](project.md) "`all` means every extra").
3. Document its config fields (the `engine.module` block accepts any fields beyond `type`); if it authenticates, resolve the key through `resolve_api_key` (`api_key` / `api_key_env`).
4. Add the backend's spec as `specs/modules/<backend>.md` (or update the existing one, e.g. [modules/deepgram.md](modules/deepgram.md)), with its frontmatter `code:`/`tests:` lists, and a row in [_index.md](_index.md).
5. Decide its `VAD_GATING` (see "VAD gating"): implement `finalize()` and the marker branch in the drain loop and declare `"supported"` — or `"required"` for a backend with no endpointing of its own. A batch model subclasses `BatchASRModule` ([batch-asr-module.md](batch-asr-module.md)), which is `"required"` and only asks for `load()` and `transcribe()`. Leaving the default `"unsupported"` makes `engine.vad` a configuration error with this module.
6. Add a row to the `MODULES` table in `tests-e2e/helpers.py` (module type, model, `api_key_env`, a `silence_s` matching how long the backend needs to finalize an utterance, and the `ModuleAudio` — format + fixtures — to drive it with; a local-model module also gets a `download.py` in its package (`DEFAULT_REPO`, `is_cached`, a `python -m` entry point) and a `LOCAL_MODELS` entry) so `test_engine_modules.py` gives it e2e conformance coverage, ungated and (when it supports gating) behind the VAD gate (see [e2e-testing.md](e2e-testing.md)). Verify with `zsh -ic 'uv run pytest tests-e2e -k <name>'`.
7. Document it for users: a page `docs/modules/<backend>.md` (the install line, a minimal config, a `| Field |` table with the spec's fields, troubleshooting, links to the spec and the example config), a row in the module tables of `docs/modules/index.md` and the README, and a complete server config `examples/config/<name>.json`. `tests/test_docs.py` checks the tables against the registry and the page's fields against the spec's ([project.md](project.md) "Documentation checks").

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

Raises `ValueError` if neither is provided, or if the named environment variable is unset or empty. Modules call it in `__init__` (e.g. `self._api_key = resolve_api_key(config, "deepgram-v1")`).

## Reconnection Contract

Each module is solely responsible for reconnecting to its backend on connection loss. The reconnection strategy should follow exponential backoff:

| Attempt | Delay |
|---|---|
| 1 | 1s |
| 2 | 2s |
| 3 | 4s |
| 4+ | 8s (max) |

While reconnecting, the module must continue draining `audio_queue` to prevent it from growing unbounded. `END_OF_SPEECH` markers drained this way are dropped.
