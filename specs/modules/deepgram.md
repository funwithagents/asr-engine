---
code:
  - src/asr_engine/modules/deepgram/__init__.py
  - src/asr_engine/modules/deepgram/v1.py
  - src/asr_engine/modules/deepgram/v2.py
  - src/asr_engine/modules/deepgram/_config.py
  - scripts/probe_deepgram_flux_silence.py
tests:
  - tests/modules/test_deepgram_v1.py
  - tests/modules/test_deepgram_v2.py
---

# Deepgram ASR Modules

**Status:** Implemented

Two Deepgram modules are provided, targeting different API versions and use cases. They are **optional**: neither is a default, and neither is importable without the `deepgram` extra.

| Key | Class | API | Best for |
|---|---|---|---|
| `deepgram-v1` | `DeepgramV1Module` | Listen v1 (Nova-3, Nova-2, …) | Multi-language, general transcription |
| `deepgram-v2` | `DeepgramV2Module` | Listen v2 (Flux) | English conversational AI, built-in turn detection |

## Installation

Both modules ship in the `deepgram` extra (see [asr-module-interface.md](../asr-module-interface.md) "Optional dependencies (extras)"), which pulls `deepgram-sdk>=7.9,<8` (7.9 adds the v2 `send_force_end_turn()` that [VAD gating](#vad-gating-1) uses; the upper bound is explained under [KeepAlive](#keepalive-1)):

```bash
pip install 'asr-engine[deepgram]'
# from source
uv sync --extra deepgram
```

Without it, `deepgram-v1` / `deepgram-v2` still appear in the registry, but constructing an engine with either raises the registry's `ImportError` naming the `deepgram` extra. Each module file imports the SDK at top level; the lazy registry keeps that import off the `import asr_engine` path.

## API key resolution

Both modules resolve their API key via `resolve_api_key` (see [asr-module-interface.md](../asr-module-interface.md)). Provide **either**:

- `api_key` — a literal key, or
- `api_key_env` — the **name** of an environment variable holding the key.

`api_key` wins if both are present. `api_key_env` keeps secrets out of config files so the config can be committed. If neither is set — or the named variable is unset/empty — construction raises `ValueError`.

---

## deepgram-v1 — Listen v1 (Nova-3)

### Overview

Uses the Deepgram Listen v1 WebSocket API with any v1-compatible model. `SpeechUtterance.is_final` maps from Deepgram's `is_final`, meaning the backend has committed that transcript chunk. The module deliberately does not use `speech_final`, so a final v1 result is not necessarily a natural end-of-utterance boundary.

### Configuration Fields

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `api_key` | string | one of¹ | — | Deepgram API key (literal) |
| `api_key_env` | string | one of¹ | — | Name of an env var holding the key |
| `model` | string | no | `"nova-3"` | Deepgram model name |
| `language` | string | no | `"multi"` | BCP-47 language code, or `"multi"` for auto-detection |
| `punctuate` | boolean | no | `true` | Enable automatic punctuation |
| `interim_results` | boolean | no | `true` | Enable interim results |

¹ Provide at least one of `api_key` / `api_key_env`; `api_key` takes precedence when both are set (see [API key resolution](#api-key-resolution)).

Validated in `__init__`: `model` and `language` are non-empty strings, `punctuate` and `interim_results` are booleans. A wrong type raises `ValueError` naming the field (`deepgram-v1 module: 'punctuate' must be true or false`) at engine construction, not at the first connection.

### Example Config

```json
{
  "type": "deepgram-v1",
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
- **Query params:** `model`, `encoding`, `sample_rate`, `channels`, `language`, `punctuate`, `interim_results` — `encoding`/`sample_rate`/`channels` come from the reconciled `AudioFormat` passed to `start()`, not hardcoded.
- **SDK:** `async with client.listen.v1.connect(...) as conn:`
- **Declared audio-format support:** `SUPPORTED_SAMPLE_RATES = {8000, 16000, 24000, 44100, 48000}`, `SUPPORTED_CHANNELS = {1}`, `SUPPORTED_ENCODINGS = {"linear16", "mulaw"}`; defaults 16000 / 1 / `linear16` (see [asr-module-interface.md](../asr-module-interface.md)).

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

Note: `speech_final` (endpointing signal) is intentionally not used. It depends on endpointing configuration and is not reliably set by all models (notably nova-3).

### KeepAlive

`v1` has a public `send_keep_alive()` method. It is called when no audio chunk arrives within 5 seconds.

### VAD gating

`VAD_GATING = "supported"` ([vad.md](../vad.md)). On an `END_OF_SPEECH` marker the audio loop calls `finalize()`, which sends Deepgram's `Finalize` message (`conn.send_finalize()`, distinct from `send_close_stream()`): the server commits the audio it has received and answers with a normal `is_final` result on the existing receive path. The connection and its context are kept, and the next utterance goes out on it. Nothing is sent for the marker itself. During a gated gap the keep-alive above fires as usual. A marker read while reconnecting is dropped with the audio.

---

## deepgram-v2 — Listen v2 (Flux)

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
| `eot_timeout_ms` | int | no | `2000` | Silence timeout before forced turn-end (ms) |

¹ Provide at least one of `api_key` / `api_key_env`; `api_key` takes precedence when both are set (see [API key resolution](#api-key-resolution)).

Validated in `__init__`: `model` is a non-empty string, `eot_threshold` a number from 0.5 to 0.9, `eot_timeout_ms` a positive integer. An invalid value raises `ValueError` naming the field at engine construction, not at the first connection. The two modules share these checks (`modules/deepgram/_config.py`).

### Example Config

```json
{
  "type": "deepgram-v2",
  "api_key": "YOUR_DEEPGRAM_API_KEY",
  "model": "flux-general-en",
  "eot_threshold": 0.7,
  "eot_timeout_ms": 2000
}
```

### WebSocket Connection

- **URL:** `wss://api.deepgram.com/v2/listen`
- **Auth:** `Authorization: Token <api_key>` header
- **Query params:** `model`, `encoding`, `sample_rate`, `eot_threshold`, `eot_timeout_ms` — `encoding`/`sample_rate` come from the reconciled `AudioFormat` passed to `start()`.
- **Declared audio-format support:** same as `deepgram-v1` — `SUPPORTED_SAMPLE_RATES = {8000, 16000, 24000, 44100, 48000}`, `SUPPORTED_CHANNELS = {1}`, `SUPPORTED_ENCODINGS = {"linear16", "mulaw"}`; defaults 16000 / 1 / `linear16`. Flux takes the same raw `encoding`/`sample_rate` parameters as v1 (verified live at 44.1 kHz — see [Encoding](https://developers.deepgram.com/docs/encoding)).
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
- Non-`ListenV2TurnInfo` messages (e.g. `Connected`) are ignored. A `Warning` is logged at debug level: the one expected is `FORCE_END_TURN_NO_ACTIVE_TURN`, when a `ForceEndTurn` crosses an `EndOfTurn` already on its way (see [VAD gating](#vad-gating-1)).

### KeepAlive

When no audio arrives within 5 seconds, the module sends `{"type": "KeepAlive"}`. The v2 SDK has no public `send_keep_alive()`, as v1 has, so the message goes through the socket client's private `_send(dict)`. That call is confined to one function, `send_keep_alive(conn)` in `v2.py`, to be switched to a public method when the SDK adds one. Two guards keep the private call from breaking silently:

- The `deepgram` extra is pinned below the next major version (`deepgram-sdk>=7.9,<8`).
- A fast-tier test sends the keepalive through the SDK's real `AsyncV2SocketClient` over a recording websocket and checks the JSON that reaches it. An SDK release that removes or changes `_send` fails that test.

### VAD gating

`VAD_GATING = "supported"` ([vad.md](../vad.md)). Flux's turn ends on silence it hears, or on the `ForceEndTurn` control message: Flux then ends the turn on all the audio it has received and emits a normal `EndOfTurn` (`trigger: "manual"`). The module tracks whether a turn is open — set by any non-`EndOfTurn` `TurnInfo` carrying a transcript, cleared by `EndOfTurn` — and `finalize()` depends on it:

- **No turn open.** Flux already ended the turn on the silence the gate let through. `finalize()` sends nothing. This is the normal path with the recommended `min_silence_duration_ms` of about 1500, which keeps Flux's contextual judgement in charge.
- **Turn open.** The gate closed before Flux decided. `finalize()` sends **`ForceEndTurn`** through the SDK's `send_force_end_turn()`, and the `EndOfTurn` follows in about 0.2 s. No silence is sent, so nothing extra is billed.
- **Turn reported after the marker.** The gate can close before Flux's first transcript for the utterance arrives, and a `ForceEndTurn` with no turn open is ignored. So it is sent as soon as that transcript shows the turn is open (once per gap).

The open-turn flag can lag the server: an `EndOfTurn` may already be on its way when `ForceEndTurn` is sent. Flux then ignores the message with a `FORCE_END_TURN_NO_ACTIVE_TURN` warning and keeps the connection, so the race costs nothing.

The connection is kept in every case. A marker read while reconnecting is dropped with the audio.

**Measured** (`scripts/probe_deepgram_flux_silence.py`, live, `flux-general-en`, `eot_threshold` 0.7, `eot_timeout_ms` 2000, on the 44.1 kHz fixtures). On 2026-11-12, before `ForceEndTurn` was available:

| Measure | Result |
|---|---|
| `EndOfTurn` after speech ends, on real-time silence | **0.17 s** ("Hello. How are you today?") and **1.16 s** ("The sky is blue."): contextual, and inside a 1500 ms hangover in both cases |
| Stop sending 0.3 s after the speech (a closed gate), nothing more | **No `EndOfTurn`**: the turn stays open indefinitely |
| Same, then 2.5 s of silence in one unpaced message | `EndOfTurn` **0.6 s** after the burst (Flux consumes it faster than real time) |
| After `EndOfTurn` | `Update` events for the next turn carry an empty transcript until speech starts (they are ignored, and do not open a turn) |

On 2026-11-23 (`--force-end-turn --force-again`, deepgram-sdk 7.19), "The sky is blue.", "Hello. How are you today?" and "I am very happy to meet you.", each with the gate closing 0.3 s and 0.0 s after the speech:

| Measure | Result |
|---|---|
| `ForceEndTurn` with a turn open | `EndOfTurn` (`trigger: "manual"`) **0.13–0.23 s** later, in all four runs |
| Its transcript | Complete in every run, the 0.0 s case included. Its `audio_end` reaches past the last `Update` (1.92 s against 1.68 s): Flux transcribes the audio it has received before ending the turn, and may send one last `Update` first |
| `ForceEndTurn` with no turn open (Flux had already ended it, or sent again after) | A `Warning`, `FORCE_END_TURN_NO_ACTIVE_TURN`: "the request was ignored". The connection stays open |

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
