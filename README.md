# asr-engine

A real-time Automatic Speech Recognition (ASR) **engine** written in Python, with an **MCP server** on top. It captures audio from any system audio input device (microphone, loopback, virtual device, …), streams it to a speech recognition backend, and exposes live transcription as utterances and aggregated segments.

Use it two ways:

- **Directly** — `import asr_engine`, construct an `ASREngine` from an `ASREngineConfig`, set `on_speech_utterance` / `on_speech_segment` callbacks (or call `listen()`), and consume results in your own Python program.
- **Over MCP** — run the bundled StreamableHTTP server (`asr-engine-mcp`) and connect any MCP-compatible AI agent or client.

```python
from asr_engine.config import ASREngineConfig, ModuleConfig
from asr_engine.engine import ASREngine


async def on_segment(seg):
    print(seg.transcript, seg.is_final, seg.end_reason)


engine = ASREngine(
    ASREngineConfig(module=ModuleConfig(type="deepgram_v1", extra={"api_key": "…"})),
    on_speech_segment=on_segment,
)
await engine.start()
```

---

## MCP interface

### Tools

| Tool | Description |
|---|---|
| `start` | Start audio capture and ASR streaming |
| `stop` | Stop audio capture and ASR streaming |
| `is_running` | Return `{ "running": bool, "connected": bool }` |
| `listen` | Blocking single-shot capture — returns `{ "transcript": str, "end_reason": str }` |
| `start_dictation` | Non-blocking: switch the always-on stream into an aggregating mode (`end_on_final_segment`, `segmentation_mode`). Segments arrive via `asr://segment` |
| `stop_dictation` | End the active dictation; revert the stream to `utterance` (engine keeps running) |
| `is_dictation_running` | Return `{ "dictating": bool, "segmentation_mode": str }` |
| `set_dictation_default_segmentation_mode` / `set_listen_default_segmentation_mode` | Set the default mode `start_dictation` / `listen` fall back to |

### Resources

Two rolling resources, both `application/json` and both subscribable:

**`asr://utterance`** — the latest ASR utterance (one atomic result, interim or final), updated on every event:

```json
{
  "transcript": "Hello, how are you?",
  "is_final": true,
  "confidence": 0.98,
  "timestamp": "2026-03-17T10:23:47.456Z"
}
```

**`asr://segment`** — the latest *segment*, an aggregation of utterances. The always-on stream is `utterance` mode (one segment per final utterance) unless a dictation session (or `listen`) is active. `is_final` is `false` while the segment grows and `true` (with an `end_reason`) when it closes:

```json
{
  "transcript": "the sky is blue",
  "is_final": true,
  "end_reason": "trigger_word",
  "timestamp": "2026-03-17T10:23:47.456Z"
}
```

---

## Prerequisites

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/)
- A [Deepgram](https://deepgram.com) API key
- For `asr-to-terminal`: `xdotool` (X11), or `ydotool` plus a running
  `ydotoold` with access to `/dev/uinput` (Wayland)

---

## Installation

```bash
git clone <repo-url>
cd asr-engine
uv sync
```

---

## Configuration

Copy the example config and export the Deepgram API key named by
`api_key_env`:

```bash
cp config.example.json config.json
export DEEPGRAM_API_KEY="..."
```

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
    "sound_feedback": { "enabled": true, "output_device": null },
    "logging": { "level": "INFO" },
    "audio": { "device": null },
    "module": {
      "type": "deepgram_v2",
      "api_key_env": "DEEPGRAM_API_KEY",
      "model": "flux-general-en"
    }
  }
}
```

The top level has just two blocks: `server` (an MCP-only concern) and `engine` — the whole `engine` block maps to one `ASREngineConfig`, the single argument to `ASREngine(config=…)`. A direct importer builds `ASREngineConfig` from the `engine` block alone and never needs `server`.

### Configuration reference

#### `server` (MCP only)

| Field | Default | Description |
|---|---|---|
| `host` | `"127.0.0.1"` | Bind address. Use `"0.0.0.0"` for LAN access. |
| `port` | `8000` | HTTP port |

#### `engine`

| Field | Default | Description |
|---|---|---|
| `auto_start` | `true` | Start ASR at server launch. Set to `false` to use the `listen` tool instead. |
| `auto_start_dictation` | `false` | Begin in a persistent dictation at startup so `asr://segment` aggregates from the start (mode = `dictation_default_segmentation_mode`). Requires `auto_start=true`. |
| `listen_default_segmentation_mode` | `"trigger_word"` | Mode `listen()` uses when its caller passes none: `"utterance"`, `"trigger_word"`, or `"timeout"` |
| `dictation_default_segmentation_mode` | `"trigger_word"` | Mode `start_dictation()` uses when its caller passes none: `"utterance"`, `"trigger_word"`, or `"timeout"` |
| `segmentation` | see below | Trigger words + timeouts, shared by the always-on stream, `listen`, and dictation |
| `sound_feedback` | see below | Start/stop audio cues, played by the engine during `listen` |
| `logging` | `{ "level": "INFO" }` | Level applied to the `asr_engine` package logger at construction |
| `audio` | see below | Audio input / file-source settings |
| `module` | — | ASR backend selection (required) |

#### `engine.segmentation`

| Field | Default | Description |
|---|---|---|
| `trigger_words` | see below | Words that close a segment in `trigger_word` mode |
| `initial_silence_timeout_s` | `10.0` | (`timeout` mode) Seconds with no speech before closing |
| `end_of_speech_timeout_s` | `5.0` | (`timeout` mode) Seconds of silence after last event before closing |

Default trigger words: `submit`, `enter`, `validate`, `send`, `confirm`, `go`, `envoyer`, `valider`, `confirmer`, `soumettre`, `entree`, `entrée`

#### `engine.sound_feedback`

| Field | Default | Description |
|---|---|---|
| `enabled` | `true` | Play audio cues at `listen` start and stop. `false` installs a no-op. |
| `output_device` | `null` | Output device name or index for cue playback. `null` = system default |

#### `engine.audio`

| Field | Default | Description |
|---|---|---|
| `device` | `null` | Audio input device name. `null` = system default |
| `audio_file` | `null` | Stream a WAV file instead of the live device (testing hook) |
| `trailing_silence_s` | `0.0` | (`audio_file` only) Silence appended after the file ends |

#### `engine.module`

| Field | Required | Description |
|---|---|---|
| `type` | yes | Module to use: `"deepgram_v1"` or `"deepgram_v2"` |
| `api_key` | one of | Deepgram API key (literal) |
| `api_key_env` | one of | Name of an environment variable holding the key — keeps the secret out of committed configs. Provide at least one of `api_key` / `api_key_env`; `api_key` takes precedence when both are set. |
| `model` | no | Model name (e.g. `"nova-3"`, `"flux-general-en"`) |
| `language` | no | (v1 only) BCP-47 language code or `"multi"` |
| `eot_threshold` | no | (v2 only) End-of-turn confidence threshold, default `0.7` |
| `eot_timeout_ms` | no | (v2 only) Silence timeout before forced turn-end, default `5000` |

---

## Running

```bash
# Start the MCP server
uv run asr-engine-mcp --config config.json

# Monitor live transcription (push client)
uv run asr-mcp-client --server http://127.0.0.1:8000/mcp

# Type speech into the active terminal
uv run asr-to-terminal --server http://127.0.0.1:8000/mcp
```

The MCP endpoint is at `http://<host>:<port>/mcp`.

---

## Gradio demo

A browser UI that drives the engine **directly** (no MCP server, no agent) — pick
an input device and ASR module, start/stop always-on capture, run a single-shot
`listen`, start/stop a dictation session, and watch utterances and segments stream in.
It's an example under [examples/gradio_demo/](examples/gradio_demo/), not part of
the library.

```bash
export DEEPGRAM_API_KEY="..."                 # default config: deepgram_v1 via this env var
uv run python -m examples.gradio_demo.app     # then open http://127.0.0.1:7860
```

Optional flags:

- `--config config.json` — build the engine from an existing config file's
  `engine` block (its `server` block is ignored) instead of the built-in default.
- `--host` / `--port` — bind address for the UI (default `127.0.0.1:7860`).

Gradio ships in the `demo` dependency group, which is synced by default, so
`uv sync` already installs it. It's kept out of the library's runtime
dependencies, so `import asr_engine` and the MCP server stay lean.

---

## Connecting an MCP client or AI agent

Point your MCP client at `http://<host>:<port>/mcp`. For Claude Code or other agents that support MCP server configuration:

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

The agent can then:
- Subscribe to `asr://utterance` (raw results) or `asr://segment` (aggregated) for a continuous live transcription feed
- Call the `listen` tool to collect a single spoken utterance on demand
- Call `start` / `stop` / `is_running` to control the engine

---

## Key concepts

### Modular ASR backend

The server uses a **pluggable module architecture**: one ASR backend is active at a time, selected via the `engine.module.type` config field. Currently two Deepgram modules are provided:

| Module key | API | Models | Best for |
|---|---|---|---|
| `deepgram_v1` | Deepgram Listen v1 | Nova-3, Nova-2, … | Multi-language, general transcription |
| `deepgram_v2` | Deepgram Listen v2 | Flux family | English conversational AI, built-in turn detection |

**`deepgram_v1`** sends 16kHz PCM audio over a WebSocket. Deepgram's
`is_final` flag marks a committed transcript chunk; the adapter intentionally
does not use the separate `speech_final` endpointing signal, so a final result
is not necessarily a natural end-of-utterance boundary.

**`deepgram_v2`** targets Flux models and uses Deepgram's integrated **EndOfTurn** detection: the model itself decides when a conversational turn is complete, providing a confidence score and a configurable threshold. This produces more natural utterance boundaries for dialogue use cases.

**Adding your own module** is straightforward — the interface is intentionally minimal:

1. Create a class that extends `ASRModule` (in [src/asr_engine/modules/base.py](src/asr_engine/modules/base.py)) and implements two async methods: `start(audio_queue, on_utterance, on_connected)` and `stop()`. The engine feeds raw 16kHz PCM audio chunks via `audio_queue`; call `on_utterance(SpeechUtterance(...))` whenever your backend produces a transcript.
2. Register it by name in the `REGISTRY` dict in [src/asr_engine/modules/\_\_init\_\_.py](src/asr_engine/modules/__init__.py).
3. Set `"module": { "type": "<your-name>", ... }` in your config's `engine` block.

The server, engine, and MCP tools need no changes. This makes it easy to plug in any ASR backend, such a local open-source model, a different cloud API, or anything that can consume a stream of PCM audio and emit transcripts.

### Interim and final results

All ASR results carry an `is_final` flag:

- **Interim (`is_final: false`)** — the model's best guess so far, updated continuously as you speak. Useful for displaying live feedback.
- **Final (`is_final: true`)** — the committed transcript for a completed utterance. Reliable and punctuated.

```json
{ "transcript": "hello how are",  "is_final": false, "confidence": null,  "timestamp": "..." }
{ "transcript": "Hello, how are you?", "is_final": true,  "confidence": 0.98, "timestamp": "..." }
```

The live MCP resource `asr://utterance` is updated on **every** result (interim and final). Clients that need only committed text should filter on `is_final: true`.

### Utterances and segments

The engine emits two streams. An **utterance** is a single ASR result (interim or final); a **segment** is an aggregation of utterances closed by a condition. The engine's `Segmenter` owns all segmentation — the `listen` tool and `asr-to-terminal` consume segments rather than re-implementing the logic. The always-on stream is `utterance` mode; a **dictation** session (`start_dictation`, or `auto_start_dictation` at startup) or `listen` switches it into one of the three modes below and reverts to `utterance` when it ends:

**`utterance` mode**

Each final utterance is its own segment (1:1). `end_reason` is `"utterance"`.

**`trigger_word` mode**

The segment accumulates finals until one contains a configured trigger word (case-insensitive substring match). The trigger-word utterance is not included in the segment transcript — it fires the action, not the text. No timeouts apply. `end_reason` is `"trigger_word"`.

Default trigger words: `submit`, `enter`, `validate`, `send`, `confirm`, `go`, `envoyer`, `valider`, `confirmer`, `soumettre`, `entree`, `entrée`

**`timeout` mode**

Two independent timers govern the segment:

| Timer | Default | Fires when |
|---|---|---|
| Initial silence | 10 s | No speech at all since the segment began |
| End-of-speech | 5 s | No ASR event received after the last interim or final result |

Whichever fires first closes the segment. The `end_reason` is then `"end_of_speech_timeout"` or `"initial_silence_timeout"`.

---

## Two usage patterns: push and pull

### Push — always-on streaming via MCP resource subscriptions

With `engine.auto_start: true` (the default), the ASR engine starts at server startup and runs continuously. Clients **subscribe** to the `asr://utterance` and/or `asr://segment` MCP resources and receive a push notification every time they change — no polling required.

```
Audio input → ASREngine → asr://utterance + asr://segment resources
                                  ↓ push notifications
                             MCP clients (subscribed)
```

This pattern suits scenarios where a client or agent wants live transcription fed to it continuously.

**Available push clients:**

- **`asr-mcp-client`** — a demo CLI that subscribes to `asr://utterance` and logs each result to stdout. Useful for testing and monitoring.
- **`asr-to-terminal`** — subscribes to `asr://segment` and injects keystrokes into the active terminal window (see below).
- **Any MCP client** — connect to `http://<host>:<port>/mcp`, subscribe to a resource, and receive live transcription events.

### Pull — on-demand capture via the `listen` tool

With `engine.auto_start: false`, the ASR engine is idle until a client explicitly requests speech. The `listen` MCP tool manages the full lifecycle:

```
MCP client calls listen
  → engine starts
  → speech is captured and accumulated
  → end-of-utterance condition met
  → engine stops
  → transcript returned to caller
```

This pattern suits agents that need to collect a single user utterance on demand — for example, asking the user a question and waiting for a spoken answer.

The `listen` tool also streams **incremental progress** via `notifications/progress` as each final result is committed, so callers can display partial transcripts without waiting for the full session to complete.

> The push and pull patterns are mutually exclusive: `listen` returns an error if `auto_start` is `true` or if the engine is already running.

---

## How MCP resource subscriptions work

A key feature of this server is that it exposes live ASR data through the **MCP resource subscription mechanism**, enabling a proper push model over HTTP.

Standard HTTP is request/response. To receive live data from a running server without polling, this server uses **MCP's StreamableHTTP transport** combined with resource subscriptions:

1. A client connects to `http://<host>:<port>/mcp` and subscribes to `asr://utterance` or `asr://segment`.
2. The server keeps the connection open and sends `notifications/resources/updated` messages each time the resource changes.
3. The client receives these notifications in real time, then reads the new resource value.

This gives clients event-driven notification that a rolling resource changed,
without polling. The notification carries the resource URI, not the event
payload: the client then reads the latest value. Fast consecutive updates may
therefore be coalesced, so these resources are latest-value snapshots rather
than a lossless event log.

The `AsrResourceClient` class ([src/asr_engine/resource_client.py](src/asr_engine/resource_client.py)) implements this subscription pattern and can be reused as a building block for any client that needs to consume a live MCP resource.

---

## `asr-to-terminal`

`asr-to-terminal` is a client that subscribes to the server's `asr://segment` resource and **types the transcription into your active terminal window** using keystroke injection (`xdotool` on X11, `ydotool` on Wayland).

**How it works:**

- Each `asr://segment` update is typed progressively — the client backspaces the changed suffix and types the new text, giving a live "typeahead" effect as the segment grows.
- When a segment **closes** (`is_final: true`), the client sends Enter and starts fresh.

Segmentation — including the mode (`trigger_word` / `timeout` / `utterance`), trigger words, and timeouts — is configured **on the server** via the `engine` config block, so every client shares the same behaviour. `asr-to-terminal` itself only needs the server URL and display server.

This makes it possible to dictate text directly into any terminal application using your voice.

```bash
uv run asr-to-terminal
uv run asr-to-terminal --server http://192.168.1.10:8000/mcp
uv run asr-to-terminal --display-server wayland
```

**System requirements:** `xdotool` (X11), or `ydotool` with a running
`ydotoold` that can access `/dev/uinput` (Wayland), must be installed and
configured separately.

```bash
sudo apt-get install xdotool   # X11
```
