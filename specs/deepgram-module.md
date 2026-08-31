---
code:
  - src/asr_engine/modules/deepgram_v1.py
  - src/asr_engine/modules/deepgram_v2.py
tests:
  - tests/modules/test_deepgram_v1.py
  - tests/modules/test_deepgram_v2.py
---

# Deepgram ASR Modules

**Status:** Implemented

Two Deepgram modules are provided, targeting different API versions and use cases:

| Key | Class | API | Best for |
|---|---|---|---|
| `deepgram_v1` | `DeepgramV1Module` | Listen v1 (Nova-3, Nova-2, …) | Multi-language, general transcription |
| `deepgram_v2` | `DeepgramV2Module` | Listen v2 (Flux) | English conversational AI, built-in turn detection |

## API key resolution

Both modules resolve their API key via `resolve_api_key` (see
[asr-module-interface.md](asr-module-interface.md)). Provide **either**:

- `api_key` — a literal key, or
- `api_key_env` — the **name** of an environment variable holding the key.

`api_key` wins if both are present. `api_key_env` keeps secrets out of config
files so the config can be committed (used by `tests-e2e/e2e.config.json`). If
neither is set — or the named variable is unset/empty — construction raises
`ValueError`.

---

## deepgram_v1 — Listen v1 (Nova-3)

### Overview

Uses the Deepgram Listen v1 WebSocket API with any v1-compatible model. Utterance detection is based on `speech_final` — Deepgram signals end-of-utterance after a configurable silence threshold.

### Configuration Fields

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `api_key` | string | one of¹ | — | Deepgram API key (literal) |
| `api_key_env` | string | one of¹ | — | Name of an env var holding the key |
| `model` | string | no | `"nova-3"` | Deepgram model name |
| `language` | string | no | `"multi"` | BCP-47 language code, or `"multi"` for auto-detection |
| `punctuate` | boolean | no | `true` | Enable automatic punctuation |
| `interim_results` | boolean | no | `true` | Enable interim results |

¹ Provide exactly one of `api_key` / `api_key_env` (see [API key resolution](#api-key-resolution)).

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
msg.is_final                             → SpeechUtterance.is_final
```

- `is_final=True` → Deepgram has committed this segment → `SpeechUtterance(is_final=True)`
- `is_final=False` → interim, transcript still updating → `SpeechUtterance(is_final=False)`
- Results with empty transcript are discarded.
- Non-`ListenV1Results` messages (e.g. `Metadata`) are ignored.

Note: `speech_final` (endpointing signal) is intentionally not used. It depends on
endpointing configuration and is not reliably set by all models (notably nova-3).

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
| `api_key` | string | one of¹ | — | Deepgram API key (literal) |
| `api_key_env` | string | one of¹ | — | Name of an env var holding the key |
| `model` | string | no | `"flux-general-en"` | Deepgram Flux model name |
| `eot_threshold` | float | no | `0.7` | End-of-turn confidence threshold (0.5–0.9) |
| `eot_timeout_ms` | int | no | `5000` | Silence timeout before forced turn-end (ms) |

¹ Provide exactly one of `api_key` / `api_key_env` (see [API key resolution](#api-key-resolution)).

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

| `msg.event` | `SpeechUtterance.is_final` | Notes |
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
