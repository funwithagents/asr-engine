# Deepgram ASR Modules

Two Deepgram modules are provided, targeting different API versions and use cases:

| Key | Class | API | Best for |
|---|---|---|---|
| `deepgram_v1` | `DeepgramV1Module` | Listen v1 (Nova-3, Nova-2, …) | Multi-language, general transcription |
| `deepgram_v2` | `DeepgramV2Module` | Listen v2 (Flux) | English conversational AI, built-in turn detection |

---

## deepgram_v1 — Listen v1 (Nova-3)

### Overview

Uses the Deepgram Listen v1 WebSocket API with any v1-compatible model. Utterance detection is based on `speech_final` — Deepgram signals end-of-utterance after a configurable silence threshold.

### Configuration Fields

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `api_key` | string | yes | — | Deepgram API key |
| `model` | string | no | `"nova-3"` | Deepgram model name |
| `language` | string | no | `"multi"` | BCP-47 language code, or `"multi"` for auto-detection |
| `punctuate` | boolean | no | `true` | Enable automatic punctuation |
| `interim_results` | boolean | no | `true` | Enable interim results |

### Example Config

```json
{
  "type": "deepgram_v1",
  "api_key": "YOUR_DEEPGRAM_API_KEY",
  "model": "nova-3",
  "language": "en-US",
  "punctuate": true,
  "interim_results": true
}
```

### WebSocket Connection

- **URL:** `wss://api.deepgram.com/v1/listen`
- **Auth:** `Authorization: Token <api_key>` header
- **Query params:** `model`, `encoding=linear16`, `sample_rate=16000`, `channels=1`, `language`, `punctuate`, `interim_results`
- **SDK:** `async with client.listen.v1.connect(...) as conn:`

### Message Handling

All transcription arrives as `ListenV1Results` messages (type `"Results"`).

```
msg.channel.alternatives[0].transcript  → transcript
msg.channel.alternatives[0].confidence  → confidence
msg.speech_final                         → ASRResult.is_final
```

- `speech_final=True` → end of utterance → `ASRResult(is_final=True)`
- `speech_final=False` → segment or interim → `ASRResult(is_final=False)`
- Results with empty transcript are discarded.
- Non-`ListenV1Results` messages (e.g. `Metadata`) are ignored.

### KeepAlive

`v1` has a public `send_keep_alive()` method. It is called when no audio chunk arrives within 5 seconds.

---

## deepgram_v2 — Listen v2 (Flux)

### Overview

Uses the Deepgram Listen v2 WebSocket API with the Flux model. Turn detection is model-integrated: the server emits `TurnInfo` messages with an `event` field. `EndOfTurn` signals a completed utterance with high confidence.

Supports only Flux-family models (`flux-general-en`, etc.). Not suitable for non-English or non-Flux models.

### Configuration Fields

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `api_key` | string | yes | — | Deepgram API key |
| `model` | string | no | `"flux-general-en"` | Deepgram Flux model name |
| `eot_threshold` | float | no | `0.7` | End-of-turn confidence threshold (0.5–0.9) |
| `eot_timeout_ms` | int | no | `5000` | Silence timeout before forced turn-end (ms) |

### Example Config

```json
{
  "type": "deepgram_v2",
  "api_key": "YOUR_DEEPGRAM_API_KEY",
  "model": "flux-general-en",
  "eot_threshold": 0.7,
  "eot_timeout_ms": 5000
}
```

### WebSocket Connection

- **URL:** `wss://api.deepgram.com/v2/listen`
- **Auth:** `Authorization: Token <api_key>` header
- **Query params:** `model`, `encoding=linear16`, `sample_rate=16000`, `eot_threshold`, `eot_timeout_ms`
- **SDK:** `async with client.listen.v2.connect(...) as conn:`

### Message Handling

All transcription arrives as `ListenV2TurnInfo` messages (type `"TurnInfo"`).

```
msg.transcript             → transcript (full accumulated turn text)
msg.end_of_turn_confidence → confidence
msg.event                  → dispatch key
```

| `msg.event` | `ASRResult.is_final` | Notes |
|---|---|---|
| `"Update"`, `"StartOfTurn"`, `"EagerEndOfTurn"`, `"TurnResumed"` | `False` | Interim |
| `"EndOfTurn"` | `True` | Turn complete |

- Results with empty transcript are discarded.
- Non-`ListenV2TurnInfo` messages (e.g. `Connected`) are ignored.

### KeepAlive

The v2 SDK has no public `send_keep_alive()`. KeepAlive is sent via the internal `conn._send({"type": "KeepAlive"})` when no audio arrives within 5 seconds.

---

## Shared Behaviour (both modules)

### Concurrency model

Inside each connection attempt, `conn.start_listening()` and `_audio_loop()` run as two concurrent `asyncio.Task`s under `asyncio.wait(FIRST_COMPLETED)`. Either task completing triggers cleanup of the other.

### Reconnection

On WebSocket disconnection or error:
1. Log the error
2. Wait with exponential backoff (1s, 2s, 4s, 8s max)
3. Drain `audio_queue` during backoff to prevent unbounded growth
4. Re-establish the connection
5. Stop retrying only when `stop()` has been called

### Async event handlers

The SDK's `_emit_async` natively supports `async def` handlers — no `loop.create_task()` bridge needed.
