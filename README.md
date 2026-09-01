# asr-engine

`asr-engine` is an asynchronous, real-time speech-recognition engine for Python. It captures live or file-based audio, streams it to a pluggable ASR backend, and emits both raw utterances and engine-segmented speech.

Use it at any of three layers:

- **`ASREngine`** — embed the speech pipeline directly in a Python application.
- **`AsrTools`** — give an in-process agent ready-made ASR tools without writing wrappers around the engine API.
- **MCP server** — expose those tools and two live transcription resources over StreamableHTTP.

```text
Audio source ──▶ ASREngine ──▶ utterances + segments ──▶ Python callbacks
                     ▲
                  AsrTools ◀── in-process agent tools
                     ▲
                  MCP tools

ASREngine callbacks ──▶ MCP rolling resources
```

The engine owns audio capture, backend selection, audio-format negotiation, segmentation, lifecycle, and sound feedback. Consumers receive consistent `SpeechUtterance` and `SpeechSegment` values rather than reimplementing end-of-speech logic.

## Installation and quick start

### Requirements

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/)
- A [Deepgram](https://deepgram.com/) API key for the bundled backends

Clone the repository and install the dependencies:

```bash
git clone <repo-url>
cd asr-engine
uv sync
export DEEPGRAM_API_KEY="..."
```

### Use the engine from Python

Construct an `ASREngine`, attach the output callbacks your application needs, and control its lifecycle asynchronously:

```python
import asyncio

from asr_engine import ASREngine, ASREngineConfig, ModuleConfig


async def main() -> None:
    async def on_utterance(utterance) -> None:
        print("utterance", utterance.transcript, utterance.is_final)

    async def on_segment(segment) -> None:
        print("segment", segment.transcript, segment.is_final, segment.end_reason)

    engine = ASREngine(
        ASREngineConfig(
            module=ModuleConfig(
                type="deepgram_v1",
                extra={"api_key_env": "DEEPGRAM_API_KEY"},
            )
        ),
        on_speech_utterance=on_utterance,
        on_speech_segment=on_segment,
    )
    await engine.start()
    try:
        await asyncio.Event().wait()
    finally:
        await engine.stop()


asyncio.run(main())
```

### Start the MCP server

Copy the example configuration, then run the only installed console script:

```bash
cp config.example.json config.json
uv run asr-engine-mcp --config config.json
```

The default MCP endpoint is `http://127.0.0.1:8000/mcp`.

## `ASREngine` API

`ASREngine` is the primary API. It is constructed from one `ASREngineConfig` and can be used without MCP or an agent framework.

### Output streams

The engine exposes two independent async callbacks, accepted by the constructor and assignable later:

- `SpeechUtterance` is one atomic backend result. It contains `transcript`, `is_final`, and optional `confidence`.
- `SpeechSegment` is an engine-owned aggregation. It contains `transcript`, `is_final`, `end_reason`, and its committed final `utterances`.

Each result emitted by the active ASR backend becomes a `SpeechUtterance`. The engine forwards it unchanged to `on_speech_utterance` and also passes it to its `Segmenter`. The segmenter maintains the current `SpeechSegment` according to the active segmentation mode and sends every change—both growing and closed segments—to `on_speech_segment`.

The streams are independent: consuming segments does not disable access to the underlying utterances. See [Utterances and segments](#utterances-and-segments) and [Segmentation modes](#segmentation-modes) for the aggregation rules.

### Operations

| API | Purpose |
|---|---|
| `await start()` / `await stop()` | Start or stop the always-on pipeline |
| `status()` | Return `running` and backend `connected` state |
| `await listen(mode=None, on_update=None)` | Capture and return one closed segment |
| `await start_dictation(...)` | Temporarily aggregate the running segment stream |
| `await stop_dictation()` | End dictation and return to one-segment-per-utterance mode |
| `dictating` / `segmentation_mode` | Inspect the active session and mode |
| `await set_segmentation_params(...)` | Update trigger words or timeout values |
| `set_*_default_segmentation_mode(mode)` | Change the default for future listen or dictation sessions |
| `audio_format` | Inspect the audio format reconciled with the backend |

### Ways to run the engine

| Usage | Starting state | What happens | How results are delivered |
|---|---|---|---|
| Continuous capture | Engine stopped | `start()` starts the engine in `utterance` mode and it runs until `stop()` is called | Utterance and segment callbacks |
| Dictation | Engine already running | `start_dictation()` temporarily changes how utterances are aggregated; `stop_dictation()` returns to `utterance` mode without stopping the engine | Both callbacks; aggregated output appears in the segment callback |
| Single capture | Engine stopped | `listen()` starts the engine, waits for one segment to close, stops the engine, and returns that segment | Return value, plus optional update callback |

`start_dictation()` is non-blocking: it activates dictation and returns immediately while the engine continues producing segments. `listen()` is blocking: it returns only after its segment has closed.

The `auto_start` and `auto_start_dictation` configuration fields are startup policies for applications that own an engine. The bundled MCP server applies them when it starts; constructing an `ASREngine` directly does not automatically start it.

### Audio input

By default, the engine captures from the configured system input device. Set `engine.audio.audio_file` to stream an audio file instead, or pass an `AudioSource` to the constructor to provide a custom source.

## Tools and MCP

### Use `AsrTools` directly with an agent

`AsrTools` is the transport-independent tool layer over one `ASREngine`. It provides agent-friendly inputs, result dictionaries, concurrency checks, and progress translation without depending on FastMCP, HTTP, or an MCP `Context`.

An agent running in the same Python process creates the tool layer from its engine:

```python
from asr_engine import AsrTools

asr_tools = AsrTools(engine)
```

If your agent framework accepts Python functions as tools, register whichever bound methods from the table below the agent needs. Their names, arguments, return values, and descriptions already define the ASR tool interface, so you do not need to recreate wrapper functions around `ASREngine`.

The MCP server constructs the same `AsrTools(engine)` and exposes thin MCP adapters around it. Direct agents and remote MCP clients therefore receive the same lifecycle rules and return shapes.

### Available tools

| Tool | Description |
|---|---|
| `start` | Start audio capture and ASR streaming |
| `stop` | Stop audio capture and ASR streaming |
| `is_running` | Return `{"running": bool, "connected": bool}` |
| `listen` | Blocking, single-shot capture returning `transcript` and `end_reason` |
| `start_dictation` | Non-blocking aggregation on an already-running engine |
| `stop_dictation` | Stop dictation while leaving the engine running |
| `is_dictation_running` | Return dictation state and current segmentation mode |
| `set_dictation_default_segmentation_mode` | Set the default mode for future dictations |
| `set_listen_default_segmentation_mode` | Set the default mode for future listens |

`AsrTools.listen()` accepts an optional mode and generic progress callback. The MCP `listen` tool uses the configured default mode and maps progress to MCP `notifications/progress` when the client supplies a progress token.

### MCP resources

The server publishes two subscribable `application/json` resources:

| Resource | Contents | Updated |
|---|---|---|
| `asr://utterance` | Latest atomic interim or final backend result | On every utterance event |
| `asr://segment` | Latest growing or closed engine segment | On every segment change |

Both resources contain `transcript`, `is_final`, and a server timestamp. Utterances additionally contain `confidence`; segments contain `end_reason`.

These resources are **rolling latest-value snapshots, not event logs**. An MCP notification says that a URI changed, after which the client reads its current value. Fast consecutive updates can be coalesced, so consumers must not assume that they will observe every intermediate state.

### Connect an MCP client

Point any StreamableHTTP-compatible MCP client at the server endpoint:

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

## Configuration

The MCP server reads a JSON file with two top-level blocks:

- `server` configures the MCP host and port.
- `engine` maps to the `ASREngineConfig` used to construct the engine.

```json
{
  "server": {
    "host": "127.0.0.1",
    "port": 8000
  },
  "engine": {
    "auto_start": true,
    "auto_start_dictation": false,
    "listen_default_segmentation_mode": "trigger_word",
    "dictation_default_segmentation_mode": "trigger_word",
    "segmentation": {
      "trigger_words": ["submit", "validate", "send"],
      "initial_silence_timeout_s": 10.0,
      "end_of_speech_timeout_s": 5.0
    },
    "sound_feedback": {
      "enabled": true,
      "output_device": null
    },
    "audio": {
      "device": null,
      "sample_rate": 16000,
      "channels": 1,
      "encoding": "linear16",
      "on_unsupported_format": "error"
    },
    "module": {
      "type": "deepgram_v2",
      "api_key_env": "DEEPGRAM_API_KEY",
      "model": "flux-general-en"
    }
  }
}
```

| Block or field | Purpose |
|---|---|
| `server` | MCP bind host and port; ignored by direct engine users |
| `engine.audio` | Input device or file plus requested sample rate, channels, and encoding |
| `engine.module` | Backend selection and backend-specific settings |
| `engine.segmentation` | Trigger words and silence timeouts shared by listen and dictation |
| `engine.sound_feedback` | Start and stop cues played by `listen()` |
| `auto_start` | Have the server start the engine during startup |
| `auto_start_dictation` | Start a persistent dictation after server auto-start |

The bundled module keys are `deepgram_v1` for general and multilingual transcription and `deepgram_v2` for English Flux models with integrated turn detection. Both accept `api_key_env`, which is preferable to committing a literal key. See [config.example.json](config.example.json) for a minimal config and [the configuration spec](specs/configuration.md) for the complete schema.

Logging is an application concern, not an engine configuration block. Set the server log level on the command line:

```bash
uv run asr-engine-mcp --config config.json --log-level DEBUG
```

## Key concepts

### Utterances and segments

An **utterance** is one result from the ASR backend, interim or final. A **segment** is produced by the engine's `Segmenter` and may aggregate several final utterances. Open segments include the current interim text; closed segments have an `end_reason`.

### Segmentation modes

| Mode | Segment closes when |
|---|---|
| `utterance` | Each final utterance arrives |
| `trigger_word` | A final utterance contains a configured trigger word |
| `timeout` | Initial silence or end-of-speech silence exceeds its timeout |

The trigger-word utterance closes the segment but is not included in its transcript. In timeout mode, the initial-silence and end-of-speech timers are independent, and whichever fires first supplies the `end_reason`.

The normal always-on engine uses `utterance` mode. Aggregation is activated explicitly by `listen`, a dictation session, or the server's `auto_start_dictation` behavior. When the session ends, the engine returns to `utterance` mode.

### Modular ASR architecture

`ASREngine` is independent of any speech-recognition provider. It loads one backend module from `asr_engine.modules.REGISTRY`, selected by `engine.module.type`, and communicates with it through the `ASRModule` interface.

```text
ASREngine ──▶ REGISTRY[engine.module.type] ──▶ ASRModule ──▶ ASR provider
                                                     │
                                                     └──▶ SpeechUtterance
```

The module owns provider-specific configuration, connection management, audio streaming, and conversion of provider responses into `SpeechUtterance` values. Audio capture, segmentation, tools, and MCP resources remain provider-independent.

| Module | Provider API | Intended use |
|---|---|---|
| `deepgram_v1` | Deepgram Listen v1 | General and multilingual transcription with Nova models |
| `deepgram_v2` | Deepgram Listen v2 | English conversational transcription with Flux turn detection |

To add another cloud or local backend, implement `ASRModule.start(...)` and `stop()`, declare the module's supported and default audio formats, emit `SpeechUtterance` values, and register the class under a new module key. `ASREngine`, `AsrTools`, and the MCP server then support it without provider-specific changes.

### Audio format selection and compatibility

The application chooses the desired end-to-end audio format with `engine.audio.sample_rate`, `engine.audio.channels`, and `engine.audio.encoding`. This is the format the engine intends to deliver to the selected ASR module; it is not necessarily the input device's native format.

Each module declares the sample rates, channel counts, and encodings it supports, together with a default for each dimension. When the engine is constructed, it compares the configured `AudioFormat` with the selected module's capabilities and resolves each dimension according to `engine.audio.on_unsupported_format`:

| Policy | If the configured format is unsupported |
|---|---|
| `error` | Construction fails with an error listing the module's supported values |
| `fallback` | The unsupported dimension is replaced by the module's declared default and a warning is logged |

The resolved format is then given to both the audio source and the ASR module. For live capture, the input stream is opened at the resolved sample rate and channel count—PortAudio may perform device-level conversion—and the capture pipeline produces the resolved encoding. For file input, the decoded file must already match the resolved sample rate and channel count; files are validated and encoded as requested, but are not resampled.

## Examples

The runnable consumers under [`examples/`](examples/) are not part of the wheel and are not installed as console scripts:

| Example | Demonstrates |
|---|---|
| [`gradio_demo`](examples/gradio_demo/) | Direct, in-process `ASREngine` integration in a browser UI |
| [`mcp_client`](examples/mcp_client/) | Subscribing to rolling MCP resources |
| [`asr_to_terminal`](examples/asr_to_terminal/) | Typing server-owned segments into the focused terminal |

See the [examples guide](examples/README.md) for shared setup and links to each example's detailed documentation.

## Development and project documentation

The repository keeps design specifications alongside the code:

- [Specifications](specs/_index.md) describe the intended design and current implementation status.
- Fast tests live in `tests/`; opt-in live Deepgram tests live in `tests-e2e/`.

```bash
uv sync --dev
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest tests/
```

## License

`asr-engine` is available under the [MIT License](LICENSE).
