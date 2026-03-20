# asr-mcp

A real-time Automatic Speech Recognition (ASR) **MCP server** written in Python. It captures audio from any system audio input device (microphone, loopback, virtual device, …), streams it to a speech recognition backend, and exposes live transcription results to any MCP-compatible AI agent or client.

---

## MCP interface

### Tools

| Tool | Description |
|---|---|
| `start` | Start audio capture and ASR streaming |
| `stop` | Stop audio capture and ASR streaming |
| `is_running` | Return `{ "running": bool, "connected": bool }` |
| `listen` | Blocking single-shot capture — returns `{ "transcript": str, "end_reason": str }` |

### Resource: `asr://result`

The single rolling resource holding the latest ASR utterance. Updated on every interim and final result.

**Payload schema:**

```json
{
  "transcript": "Hello, how are you?",
  "is_final": true,
  "confidence": 0.98,
  "timestamp": "2026-03-17T10:23:47.456Z"
}
```

---

## Prerequisites

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/)
- A [Deepgram](https://deepgram.com) API key
- For `asr-to-terminal`: `xdotool` (X11) or `ydotool` (Wayland)

---

## Installation

```bash
git clone <repo-url>
cd asr-mcp
uv sync
```

---

## Configuration

Copy the example config and fill in your Deepgram API key:

```bash
cp config.example.json config.json
```

```json
{
  "server": {
    "host": "127.0.0.1",
    "port": 8000
  },
  "audio": {
    "device": null
  },
  "asr": {
    "type": "deepgram_v2",
    "api_key": "YOUR_DEEPGRAM_API_KEY",
    "model": "flux-general-en"
  },
  "engine": {
    "auto_start": true
  },
  "listen": {
    "end_of_utterance_mode": "trigger_word",
    "trigger_words": ["submit", "validate", "send"],
    "initial_silence_timeout_s": 10.0,
    "end_of_speech_timeout_s": 5.0
  }
}
```

### Configuration reference

#### `server`

| Field | Default | Description |
|---|---|---|
| `host` | `"127.0.0.1"` | Bind address. Use `"0.0.0.0"` for LAN access. |
| `port` | `8000` | HTTP port |

#### `audio`

| Field | Default | Description |
|---|---|---|
| `device` | `null` | Audio input device name or index. `null` = system default |

#### `asr`

| Field | Required | Description |
|---|---|---|
| `type` | yes | Module to use: `"deepgram_v1"` or `"deepgram_v2"` |
| `api_key` | yes | Deepgram API key |
| `model` | no | Model name (e.g. `"nova-3"`, `"flux-general-en"`) |
| `language` | no | (v1 only) BCP-47 language code or `"multi"` |
| `eot_threshold` | no | (v2 only) End-of-turn confidence threshold, default `0.7` |
| `eot_timeout_ms` | no | (v2 only) Silence timeout before forced turn-end, default `5000` |

#### `engine`

| Field | Default | Description |
|---|---|---|
| `auto_start` | `true` | Start ASR at server launch. Set to `false` to use `listen` tool instead. |

#### `listen`

| Field | Default | Description |
|---|---|---|
| `end_of_utterance_mode` | `"trigger_word"` | `"trigger_word"` or `"timeout"` |
| `trigger_words` | see above | Words that end the session in `trigger_word` mode |
| `initial_silence_timeout_s` | `10.0` | (`timeout` mode) Seconds with no speech before giving up |
| `end_of_speech_timeout_s` | `5.0` | (`timeout` mode) Seconds of silence after last event before ending |

---

## Running

```bash
# Start the MCP server
uv run asr-mcp-server --config config.json

# Monitor live transcription (push client)
uv run asr-mcp-client --server http://127.0.0.1:8000/mcp

# Type speech into the active terminal
uv run asr-to-terminal --server http://127.0.0.1:8000/mcp
```

The MCP endpoint is at `http://<host>:<port>/mcp`.

---

## Connecting an MCP client or AI agent

Point your MCP client at `http://<host>:<port>/mcp`. For Claude Code or other agents that support MCP server configuration:

```json
{
  "mcpServers": {
    "asr-mcp": {
      "type": "http",
      "url": "http://127.0.0.1:8000/mcp"
    }
  }
}
```

The agent can then:
- Subscribe to `asr://result` for a continuous live transcription feed
- Call the `listen` tool to collect a single spoken utterance on demand
- Call `start` / `stop` / `is_running` to control the engine

---

## Key concepts

### Modular ASR backend

The server uses a **pluggable module architecture**: one ASR backend is active at a time, selected via the `asr.type` config field. Currently two Deepgram modules are provided:

| Module key | API | Models | Best for |
|---|---|---|---|
| `deepgram_v1` | Deepgram Listen v1 | Nova-3, Nova-2, … | Multi-language, general transcription |
| `deepgram_v2` | Deepgram Listen v2 | Flux family | English conversational AI, built-in turn detection |

**`deepgram_v1`** sends 16kHz PCM audio over a WebSocket and relies on Deepgram's `is_final` flag to detect utterance boundaries — the model signals "I'm done with this segment" after a silence threshold.

**`deepgram_v2`** targets Flux models and uses Deepgram's integrated **EndOfTurn** detection: the model itself decides when a conversational turn is complete, providing a confidence score and a configurable threshold. This produces more natural utterance boundaries for dialogue use cases.

### Interim and final results

All ASR results carry an `is_final` flag:

- **Interim (`is_final: false`)** — the model's best guess so far, updated continuously as you speak. Useful for displaying live feedback.
- **Final (`is_final: true`)** — the committed transcript for a completed utterance. Reliable and punctuated.

```json
{ "transcript": "hello how are",  "is_final": false, "confidence": null,  "timestamp": "..." }
{ "transcript": "Hello, how are you?", "is_final": true,  "confidence": 0.98, "timestamp": "..." }
```

The live MCP resource `asr://result` is updated on **every** result (interim and final). Clients that need only committed text should filter on `is_final: true`.

### End-of-utterance detection (for the `listen` tool)

The `listen` tool collects speech until an **end-of-utterance condition** is met. Two modes are available, configured in the `listen` block:

**`trigger_word` mode (default)**

The session ends when any final result contains one of the configured trigger words (case-insensitive substring match). The trigger-word utterance is not included in the returned transcript — it fires the action, not the text. No timeouts apply; the session waits indefinitely.

Default trigger words: `submit`, `enter`, `validate`, `send`, `confirm`, `go`, `envoyer`, `valider`, `confirmer`, `soumettre`, `entree`, `entrée`

**`timeout` mode**

Two independent timers govern the session:

| Timer | Default | Fires when |
|---|---|---|
| Initial silence | 10 s | No speech at all since session start |
| End-of-speech | 5 s | No ASR event received after the last interim or final result |

Whichever fires first ends the session. The return value includes an `end_reason` field: `"trigger_word"`, `"end_of_speech_timeout"`, or `"initial_silence_timeout"`.

---

## Two usage patterns: push and pull

### Push — always-on streaming via MCP resource subscriptions

With `engine.auto_start: true` (the default), the ASR engine starts at server startup and runs continuously. Clients **subscribe** to the `asr://result` MCP resource and receive a push notification every time a new result is emitted — no polling required.

```
Audio input → ASREngine → asr://result resource
                                  ↓ push notifications
                             MCP clients (subscribed)
```

This pattern suits scenarios where a client or agent wants live transcription fed to it continuously.

**Available push clients:**

- **`asr-mcp-client`** — a demo CLI that subscribes to `asr://result` and logs each result to stdout. Useful for testing and monitoring.
- **`asr-to-terminal`** — subscribes to `asr://result` and injects keystrokes into the active terminal window (see below).
- **Any MCP client** — connect to `http://<host>:<port>/mcp`, subscribe to `asr://result`, and receive live transcription events.

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

1. A client connects to `http://<host>:<port>/mcp` and subscribes to `asr://result`.
2. The server keeps the connection open and sends `notifications/resources/updated` messages each time the resource changes.
3. The client receives these notifications in real time, then reads the new resource value.

This means an AI agent or any MCP client gets a genuine event-driven feed of transcription results — no polling, no missed updates — over a plain HTTP connection that works across a local network.

The `AsrResourceClient` class ([src/asr_mcp/resource_client.py](src/asr_mcp/resource_client.py)) implements this subscription pattern and can be reused as a building block for any client that needs to consume a live MCP resource.

---

## `asr-to-terminal`

`asr-to-terminal` is a client that subscribes to the live ASR stream and **types the transcription into your active terminal window** using keystroke injection (`xdotool` on X11, `ydotool` on Wayland).

**How it works:**

- **Interim results** are typed immediately and erased (via Backspace) when a new interim or final arrives — giving a live "typeahead" effect.
- **Final results** replace the interim text with the committed transcript.
- **Submit words** (configurable, same defaults as `listen` trigger words) erase the current interim and send Enter instead of typing the utterance.

This makes it possible to dictate text directly into any terminal application using your voice.

```bash
uv run asr-to-terminal
uv run asr-to-terminal --server http://192.168.1.10:8000/mcp
uv run asr-to-terminal --submit-words submit validate confirm
uv run asr-to-terminal --display-server wayland
```

**System requirements:** `xdotool` (X11) or `ydotool` (Wayland) must be installed separately.

```bash
sudo apt-get install xdotool   # X11
```
