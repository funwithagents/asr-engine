# asr-engine

`asr-engine` is an asynchronous, real-time speech-recognition engine for Python. It captures live or file-based audio, streams it to a configurable ASR module, and emits both atomic utterances and engine-segmented speech.

```text
AudioSource ──▶ ASREngine ──▶ ASRModule ──▶ ASR provider
                  │
                  ├──▶ SpeechUtterance callbacks
                  └──▶ SpeechSegment callbacks
```

The engine owns audio capture, backend selection, audio-format negotiation, provider-independent results, segmentation, lifecycle, and optional sound feedback. It can run continuously, listen for a single segment, or temporarily aggregate speech in a dictation session.

`AsrTools` and the bundled MCP server are optional adapters over the same engine. You do not need MCP or an agent framework to use `asr-engine` in a Python application.

## Installation

### Requirements

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/)
- An ASR module installed from its extra, plus that provider's credentials — no provider is installed or selected by default (see [ASR modules](#asr-modules))
- An available system input device for live capture

Install the project from source:

```bash
git clone <repo-url>
cd asr-engine
uv sync --no-default-groups --extra deepgram   # the extra for the ASR module you will use
export DEEPGRAM_API_KEY="..."
```

`--no-default-groups` is what keeps that install minimal: `uv sync` syncs the `dev` and `demo` groups by default, and `dev` pulls in the extras contributors need, so a bare `uv sync` here builds the full contributor environment — the Deepgram SDK, the MCP server stack, the Kyutai MLX backend (on Apple Silicon) and Gradio (see [Development](#development-and-project-documentation)).

The base installation contains the engine, `AsrTools` and the `fake` test module, but no speech provider and no server. Install the provider you want as an extra, and add the `mcp` extra to run the MCP server — its stack (the MCP SDK and `uvicorn`) is never pulled in by a program that only imports `asr_engine`:

```bash
uv sync --no-default-groups --extra deepgram               # Deepgram modules
uv sync --no-default-groups --extra deepgram --extra mcp   # + the MCP server (asr-engine-mcp)
uv sync --no-default-groups --extra kyutai-mlx             # local Kyutai STT, Apple Silicon (MLX)
uv sync --no-default-groups --extra kyutai-torch           # local Kyutai STT, elsewhere (PyTorch)
uv sync --no-default-groups --extra all                    # every provider and the server

# For an installed package:
pip install 'asr-engine[deepgram]'
pip install 'asr-engine[mcp,deepgram]'
pip install 'asr-engine[kyutai-mlx]'     # or 'asr-engine[kyutai-torch]'
pip install 'asr-engine[all]'
```

The examples in this README use Deepgram, the first available provider module.

## Quick start

Create an `ASREngineConfig`, attach the callbacks your application needs, and control the engine lifecycle asynchronously:

```python
import asyncio

from asr_engine import ASREngine, ASREngineConfig, SpeechSegment


async def on_segment(segment: SpeechSegment) -> None:
    if segment.is_final:
        print(segment.transcript)


async def main() -> None:
    config = ASREngineConfig.from_dict(
        {
            "module": {
                "type": "deepgram_v1",
                "api_key_env": "DEEPGRAM_API_KEY",
                "model": "nova-3",
                "language": "multi",
            }
        }
    )

    engine = ASREngine(config, on_speech_segment=on_segment)

    await engine.start()
    try:
        await asyncio.Event().wait()
    finally:
        await engine.stop()


asyncio.run(main())
```

`start()` runs continuously and emits one segment per final utterance until `stop()` is called. Constructing an engine never starts asynchronous work by itself; direct callers always own its lifecycle.

## Configuring the engine

### Creating the configuration

`ASREngine` takes one `ASREngineConfig`. Most applications should build it from the ASR portion of their existing configuration:

```python
import json

from asr_engine import ASREngine, ASREngineConfig

with open("application.json") as file:
    application_config = json.load(file)

config = ASREngineConfig.from_dict(application_config["asr"])
engine = ASREngine(config)
```

This works naturally when the same application file also contains TTS or other settings. The dictionary passed to `from_dict()` must have the engine-block shape shown below.

Use the constructor matching the configuration source:

```python
# Already-parsed dictionary
config = ASREngineConfig.from_dict(engine_data)

# JSON string containing an engine block
config = ASREngineConfig.from_json(engine_json)

# JSON file whose root is an engine block
config = ASREngineConfig.from_json_file("asr.json")
```

Configuration defined directly in Python can use the nested dataclasses:

```python
from asr_engine import ASREngineConfig, ModuleConfig

config = ASREngineConfig(
    module=ModuleConfig(
        type="deepgram_v1",
        extra={"api_key_env": "DEEPGRAM_API_KEY"},
    )
)
```

Direct dataclass construction expects trusted values. The `from_*` constructors normalize and validate externally supplied configuration.

All three `from_*` methods above expect an engine block, without an MCP `server` wrapper. Complete `{server, engine}` files are handled by `MCPServerConfig` in the optional MCP section.

### Engine configuration shape

A complete engine block looks like this:

```json
{
  "auto_start": true,
  "auto_start_dictation": false,
  "listen_default_segmentation_mode": "trigger_word",
  "dictation_default_segmentation_mode": "trigger_word",
  "segmentation": {
    "trigger_words": ["send", "submit"],
    "initial_silence_timeout_s": 10.0,
    "end_of_speech_timeout_s": 5.0
  },
  "sound_feedback": {
    "enabled": true,
    "output_device": null
  },
  "audio": {
    "device": null,
    "audio_file": null,
    "trailing_silence_s": 0.0,
    "sample_rate": 16000,
    "channels": 1,
    "encoding": "linear16",
    "on_unsupported_format": "error"
  },
  "module": {
    "type": "deepgram_v1",
    "api_key_env": "DEEPGRAM_API_KEY",
    "model": "nova-3",
    "language": "multi"
  }
}
```

| Block or field | Purpose |
|---|---|
| `module` | Selects an ASR backend and supplies its provider-specific settings; required |
| `audio` | Selects live or file input and requests the end-to-end audio format |
| `segmentation` | Defines the trigger words and silence timeouts used by listen and dictation |
| `sound_feedback` | Controls the start and stop cues played by `listen()` |
| `listen_default_segmentation_mode` | Default mode for future `listen()` calls; defaults to `trigger_word` |
| `dictation_default_segmentation_mode` | Default mode for future dictation sessions; defaults to `trigger_word` |
| `auto_start` | Hosting policy used by the bundled MCP server; defaults to `true` |
| `auto_start_dictation` | Hosting policy that makes the MCP server enter persistent dictation after auto-start; requires `auto_start=true` |

`auto_start` and `auto_start_dictation` are instructions for an application that hosts the engine. The bundled MCP server applies them during startup, but `ASREngine` does not act on them when constructed directly.

Logging is also an application concern. Importing and constructing the engine does not configure handlers or log levels.

For every field, default, and validation rule, see the [configuration specification](specs/configuration.md).

## ASR modules

An ASR module owns provider-specific authentication, connection management, audio streaming, reconnection, and conversion of provider responses into `SpeechUtterance` values. Audio capture, segmentation, engine operations, tools, and MCP resources remain provider-independent.

```text
ASREngine ──▶ resolve_module_class(config.module.type) ──▶ ASRModule ──▶ provider
                                                        │
                                                        └──▶ SpeechUtterance
```

### Available modules

Provider modules are optional: each one ships in an extra, and none is a default. `engine.module.type` must always name the module you chose. Selecting a module whose extra is not installed fails at engine construction with an `ImportError` naming the extra to install.

| Module type | Extra | Provider API | Best for | Default model |
|---|---|---|---|---|
| `deepgram_v1` | `deepgram` | Deepgram Listen v1 | General and multilingual transcription | `nova-3` |
| `deepgram_v2` | `deepgram` | Deepgram Listen v2 | English conversational transcription with integrated turn detection | `flux-general-en` |
| `kyutai` | `kyutai-mlx` or `kyutai-torch` | None — runs on-device | Local English + French transcription: no network, no API key, no per-minute cost | `kyutai/stt-1b-en_fr-candle` |

#### `fake` — test double, not an ASR backend

The core install also contains `fake`, a scripted module for **tests only**. It ignores the audio and replays configured utterances: word-by-word interims evenly spaced over each utterance's `[start_s, end_s]` window (measured in audio time), then the full text as a final at `end_s`. It needs no extra and no credentials, so it lets you test code built on `asr-engine` deterministically:

```json
{
  "type": "fake",
  "utterances": [{"text": "the sky is blue", "start_s": 0.5, "end_s": 1.5}]
}
```

Do not use it for real speech recognition. See the [fake module specification](specs/modules/fake.md).

#### Deepgram v1

```json
{
  "type": "deepgram_v1",
  "api_key_env": "DEEPGRAM_API_KEY",
  "model": "nova-3",
  "language": "multi",
  "punctuate": true,
  "interim_results": true
}
```

| Field | Default | Description |
|---|---|---|
| `model` | `nova-3` | Deepgram v1-compatible model |
| `language` | `multi` | BCP-47 language code or multilingual auto-detection |
| `punctuate` | `true` | Enable automatic punctuation |
| `interim_results` | `true` | Emit interim results while speech is in progress |

#### Deepgram v2

```json
{
  "type": "deepgram_v2",
  "api_key_env": "DEEPGRAM_API_KEY",
  "model": "flux-general-en",
  "eot_threshold": 0.7,
  "eot_timeout_ms": 2000
}
```

| Field | Default | Description |
|---|---|---|
| `model` | `flux-general-en` | Deepgram Flux-family model |
| `eot_threshold` | `0.7` | End-of-turn confidence threshold |
| `eot_timeout_ms` | `2000` | Silence before a forced turn end, in milliseconds |

#### Kyutai (local, on-device)

[Kyutai's streaming STT](https://github.com/kyutai-labs/delayed-streams-modeling) runs inside the process: no network, no API key. One module type covers two interchangeable backends — MLX on Apple Silicon (`kyutai-mlx` extra) and PyTorch elsewhere (`kyutai-torch` extra); `"backend": "auto"` picks MLX when it is available, so the same config works on both. The PyTorch backend has not yet been verified on real hardware.

```json
{
  "engine": {
    "audio": { "sample_rate": 24000 },
    "module": {
      "type": "kyutai",
      "backend": "auto",
      "hf_repo": "kyutai/stt-1b-en_fr-candle"
    }
  }
}
```

**`engine.audio.sample_rate` must be `24000`** — the model's only rate; with the 16000 default, engine construction fails with the format-reconciliation error. A complete server config is in [`config.kyutai.example.json`](config.kyutai.example.json).

| Field | Default | Description |
|---|---|---|
| `backend` | `auto` | `auto`, `mlx`, or `torch` |
| `hf_repo` | `kyutai/stt-1b-en_fr-candle` | Hugging Face repo to load. Only the `-candle` 1B repo carries the semantic VAD heads |
| `vad` | `true` | Close utterances on the model's end-of-turn (semantic VAD) head |
| `vad_threshold` | `0.5` | End-of-turn probability above which an utterance closes |
| `finalize_after_silence_s` | `2.0` | Fallback: close an utterance after this long with no new text |
| `max_steps` | `4096` | Model step budget (~5.5 min) before the session is recycled at an utterance boundary |
| `device` | `auto` | PyTorch backend only: `auto`, `cuda`, `mps`, or `cpu` |
| `lag_warn_s` / `lag_drop_s` | `2.0` / `10.0` | If the model falls behind real time: warn, then drop the oldest audio |

The first `start()` downloads the weights (~2 GB for the 1B model) and loads them; `connected` turns true once the model is warm. The model then stays in memory across `stop()`/`start()` and `listen()` calls. To check that a machine keeps up with real time, run `uv run python scripts/benchmark_kyutai.py` (Apple M5 Pro: real-time factor ≈ 0.45). See the [Kyutai module specification](specs/modules/kyutai.md).

### Credentials

Both Deepgram modules accept either a literal API key or the name of an environment variable containing it:

```json
{
  "type": "deepgram_v1",
  "api_key_env": "DEEPGRAM_API_KEY"
}
```

`api_key_env` keeps secrets out of configuration files. If both `api_key` and `api_key_env` are present, the literal `api_key` takes precedence. Credentials are resolved when the engine constructs the selected module, not while `ASREngineConfig.from_dict()` parses the configuration.

### Adding a module

To integrate another cloud or local backend, implement the `ASRModule` interface, declare its supported and default audio formats, emit `SpeechUtterance` values, and register it under a new module key as a lazy entry naming the extra that provides its dependencies. `ASREngine`, `AsrTools`, and the MCP server can then use it without provider-specific changes. See the [ASR module interface specification](specs/asr-module-interface.md) for the complete contract.

## Engine output

The engine exposes two independent async callbacks. They can be passed to the constructor or assigned later as `engine.on_speech_utterance` and `engine.on_speech_segment`.

### Utterances

A `SpeechUtterance` is one atomic event emitted by the ASR module:

```python
@dataclass
class SpeechUtterance:
    transcript: str
    is_final: bool
    confidence: float | None
```

The utterance callback receives every non-empty provider result, including interim and final results.

### Segments

A `SpeechSegment` is the engine's aggregation of utterances according to the active segmentation mode:

```python
@dataclass
class SpeechSegment:
    transcript: str
    is_final: bool
    end_reason: str | None
    utterances: list[SpeechUtterance]
```

The segment callback receives every change while a segment grows and one final value when it closes. `utterances` contains the final utterances committed to the segment; interim utterances contribute to the current transcript but are not stored in that list.

You can consume either stream or both:

```python
from asr_engine import ASREngine, SpeechSegment, SpeechUtterance


async def on_utterance(utterance: SpeechUtterance) -> None:
    print("utterance", utterance.transcript, utterance.is_final)


async def on_segment(segment: SpeechSegment) -> None:
    print("segment", segment.transcript, segment.is_final, segment.end_reason)


engine = ASREngine(
    config,
    on_speech_utterance=on_utterance,
    on_speech_segment=on_segment,
)
```

## Running the engine

### Continuous recognition

Call `start()` to run the pipeline continuously in `utterance` segmentation mode. Results are delivered through the callbacks until `stop()` is called:

```python
await engine.start()
try:
    await run_your_application()
finally:
    await engine.stop()
```

### Listen for one segment

`listen()` starts a stopped engine in the requested segmentation mode, waits for one segment to close, stops the engine, and returns that segment:

```python
segment = await engine.listen(mode="timeout")
print(segment.transcript, segment.end_reason)
```

Pass `mode=None` or omit it to use `listen_default_segmentation_mode`. An optional `on_update` callback can consume the segment's intermediate values:

```python
segment = await engine.listen(mode="trigger_word", on_update=on_segment)
```

`listen()` requires the engine to be stopped. If sound feedback is enabled, the engine plays its configured start and stop cues around the session.

### Dictation on a running engine

A dictation session temporarily changes how a running engine aggregates utterances. Starting dictation is non-blocking and does not replace the engine's callbacks:

```python
await engine.start()
await engine.start_dictation(
    segmentation_mode="trigger_word",
    end_on_final_segment=False,
)

# Aggregated updates continue through on_speech_segment.

await engine.stop_dictation()
await engine.stop()
```

Pass `segmentation_mode=None` or omit it to use `dictation_default_segmentation_mode`. With `end_on_final_segment=True`, which is the default, the first closed segment ends the dictation automatically and the engine returns to `utterance` mode. Otherwise, call `stop_dictation()` explicitly. The engine keeps running in both cases.

| Operation | Required initial state | Blocking | Stops the engine afterward |
|---|---|---:|---:|
| `start()` | Stopped | No | No |
| `listen()` | Stopped | Yes | Yes |
| `start_dictation()` | Running | No | No |

### Other operations

| API | Purpose |
|---|---|
| `status()` | Return the engine's `running` and backend `connected` state |
| `dictating` | Report whether a dictation session is active |
| `segmentation_mode` | Inspect the currently active mode |
| `audio_format` | Inspect the audio format reconciled with the active module |
| `await set_segmentation_params(...)` | Update trigger words or timeouts and rebuild the segmenter |
| `set_listen_default_segmentation_mode(mode)` | Change the default for future listens |
| `set_dictation_default_segmentation_mode(mode)` | Change the default for future dictations |

## Segmentation

Segmentation belongs to the engine, so direct consumers, tools, and MCP resources all observe the same speech boundaries.

| Mode | Segment closes when | Final `end_reason` |
|---|---|---|
| `utterance` | Each final utterance arrives | `utterance` |
| `trigger_word` | A final utterance contains a configured trigger word | `trigger_word` |
| `timeout` | Initial silence or end-of-speech silence exceeds its configured timeout | `initial_silence_timeout` or `end_of_speech_timeout` |

In `trigger_word` mode, matching is a case-insensitive substring check. The trigger-word utterance closes the segment but is not included in its transcript.

In `timeout` mode, an initial-silence timer starts with the segment and an end-of-speech timer starts after the first event. Every interim or final event resets the end-of-speech timer.

The normal continuous engine always starts in `utterance` mode. Aggregation is activated explicitly by `listen()`, by a dictation session, or by a hosting application applying `auto_start_dictation`. Listen and dictation select a mode but share the trigger words and timeout values from `config.segmentation`.

## Audio input and format

By default, the engine captures from `config.audio.device`. A `null` device selects the system default input.

Set `config.audio.audio_file` to stream a decoded audio file at real-time pace instead of using an input device:

```json
{
  "audio": {
    "audio_file": "speech.mp3",
    "trailing_silence_s": 2.0,
    "sample_rate": 44100,
    "channels": 1,
    "encoding": "linear16"
  },
  "module": {
    "type": "deepgram_v1",
    "api_key_env": "DEEPGRAM_API_KEY"
  }
}
```

Files are decoded and validated against the resolved sample rate and channel count, but they are not resampled. `trailing_silence_s` can give a remote backend enough silence to finalize the last utterance.

You can also inject an `AudioSource` when constructing the engine. An injected source takes precedence over `audio.audio_file` and the live input device, which makes custom capture systems and deterministic tests possible.

### Audio-format negotiation

`audio.sample_rate`, `audio.channels`, and `audio.encoding` describe the format the engine should deliver to the selected module. Every module declares its supported values and a default for each dimension.

| `on_unsupported_format` | Behavior when a requested value is unsupported |
|---|---|
| `error` | Engine construction fails with the module's supported values; this is the default |
| `fallback` | The unsupported dimension uses the module's declared default and a warning is logged |

The resolved format is exposed as `engine.audio_format` and is given to both the audio source and the ASR module. The bundled Deepgram modules support sample rates of 8000, 16000, 24000, 44100, and 48000 Hz; mono audio; and `linear16` or `mulaw` encoding. The `kyutai` module supports exactly 24000 Hz mono `linear16`.

## Optional tools and MCP server

The engine is the primary API. The tools layer and MCP server adapt an engine for agent frameworks and remote clients without changing its recognition or segmentation behavior.

### Use `AsrTools` in process

`AsrTools` provides agent-friendly arguments, return dictionaries, lifecycle checks, and progress translation over an existing engine:

```python
from asr_engine import AsrTools

asr_tools = AsrTools(engine)
```

Frameworks that accept Python callables as tools can register the needed bound methods directly.

| Tool method | Purpose |
|---|---|
| `start` | Start continuous capture and recognition |
| `stop` | Stop capture and recognition |
| `is_running` | Return running and connection state |
| `listen` | Capture and return one closed segment |
| `start_dictation` | Begin non-blocking aggregation on a running engine |
| `stop_dictation` | End dictation without stopping the engine |
| `is_dictation_running` | Return dictation state and segmentation mode |
| `set_dictation_default_segmentation_mode` | Set the default for future dictations |
| `set_listen_default_segmentation_mode` | Set the default for future listens |

### Start the MCP server

The MCP server adds a `server` block around the same engine configuration:

```json
{
  "server": {
    "host": "127.0.0.1",
    "port": 8000
  },
  "engine": {
    "auto_start": true,
    "module": {
      "type": "deepgram_v1",
      "api_key_env": "DEEPGRAM_API_KEY",
      "model": "nova-3"
    }
  }
}
```

This complete `{server, engine}` document maps to `MCPServerConfig`, which delegates its `engine` block to `ASREngineConfig.from_dict()`. Direct engine users do not need `MCPServerConfig`.

To load the complete shape from Python:

```python
from asr_engine.config import MCPServerConfig

server_config = MCPServerConfig.from_json_file("config.json")
engine_config = server_config.engine
```

Start the bundled server with the example configuration (requires the `mcp` extra; without it, `asr-engine-mcp` exits with the install hint):

```bash
uv sync --no-default-groups --extra deepgram --extra mcp
cp config.example.json config.json
uv run asr-engine-mcp --config config.json
```

The default endpoint is `http://127.0.0.1:8000/mcp`. Logging is configured by the server command rather than the engine configuration:

```bash
uv run asr-engine-mcp --config config.json --log-level DEBUG
```

### MCP resources

The server exposes the `AsrTools` operations as MCP tools and publishes two subscribable `application/json` resources:

| Resource | Contents | Updated |
|---|---|---|
| `asr://utterance` | Latest atomic interim or final backend result | On every utterance event |
| `asr://segment` | Latest growing or closed engine segment | On every segment change |

These are rolling latest-value snapshots, not transcript histories or event logs. A notification tells a client that a URI changed, after which the client reads its current value. Fast consecutive updates can be coalesced.

Point a StreamableHTTP-compatible client at the endpoint:

```json
{
  "mcpServers": {
    "asr-engine": {
      "type": "http",
      "url": "http://127.0.0.1:8000/mcp"
    }
  }
}
```

## Examples

The runnable consumers under [`examples/`](examples/) are not part of the wheel and are not installed as console scripts:

| Example | Integration | Demonstrates |
|---|---|---|
| [`gradio_demo`](examples/gradio_demo/) | Direct engine import | An in-process browser UI for devices, modules, lifecycle, listen, dictation, utterances, and segments |
| [`mcp_client`](examples/mcp_client/) | MCP resources | Subscribing to rolling transcription resources |
| [`asr_to_terminal`](examples/asr_to_terminal/) | MCP resources | Typing server-owned segments into the focused terminal |

See the [examples guide](examples/README.md) for setup and links to each example's documentation.

## Development and project documentation

The repository keeps its design specifications alongside the code. The [specification index](specs/_index.md) describes the intended design and implementation status, while the [implementation plan index](plans/_index.md) records how each feature was built.

Install the development environment and run all default checks:

```bash
uv sync
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest tests/
```

A bare `uv sync` syncs the default groups: `dev`, which depends on `asr-engine[all]` and so installs every provider extra and the `mcp` extra, and `demo`, which adds Gradio for the example UI. The whole suite then runs with nothing skipped for a missing extra. Fast deterministic tests live in `tests/`; opt-in real-time pipeline tests live in `tests-e2e/` (live provider conformance, plus keyless scenarios on the `fake` module) and must be invoked explicitly.

## License

`asr-engine` is available under the [MIT License](LICENSE).
