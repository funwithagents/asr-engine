# Deepgram ASR Module

## Overview

Implements `ASRModule` using the Deepgram real-time streaming WebSocket API, **Listen v2** (Flux).

- **Module type key:** `"deepgram"`
- **API:** Deepgram Listen v2 — streaming WebSocket with built-in turn detection (Flux model)
- **SDK:** `deepgram-sdk` Python package, `client.listen.v2.connect()`

## Why Listen v2 (Flux)

Listen v2 uses the Flux model, which has built-in contextual turn detection. It replaces the manual `speech_final` heuristic from v1 with explicit `EndOfTurn` events, making the `is_final` signal more reliable and easier to reason about.

## Configuration Fields

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `api_key` | string | yes | — | Deepgram API key |
| `language` | string | no | `"en-US"` | BCP-47 language code |
| `model` | string | no | `"flux-general-en"` | Deepgram model name |
| `punctuate` | boolean | no | `true` | Enable automatic punctuation |
| `interim_results` | boolean | no | `true` | Enable interim results |
| `eot_threshold` | float | no | `0.7` | End-of-turn confidence threshold (0.5–0.9) |
| `eot_timeout_ms` | int | no | `5000` | Silence timeout before forced turn-end (ms) |

## Example Config

```json
{
  "type": "deepgram",
  "api_key": "YOUR_DEEPGRAM_API_KEY",
  "language": "en-US",
  "model": "flux-general-en",
  "punctuate": true,
  "interim_results": true,
  "eot_threshold": 0.7,
  "eot_timeout_ms": 5000
}
```

## WebSocket Connection

- **URL:** `wss://api.deepgram.com/v2/listen`
- **Auth:** `Authorization: Token <api_key>` header
- **Query params:** derived from config (`language`, `model`, `punctuate`, `interim_results`, `eot_threshold`, `eot_timeout_ms`, `encoding=linear16`, `sample_rate=16000`, `channels=1`)
- **SDK usage:** `async with client.listen.v2.connect(...) as conn:`

## Message Handling

### Sending audio

Raw PCM chunks from the audio queue are sent as binary WebSocket frames via `conn.send_media(chunk)`.

Deepgram v2 closes the connection after 10 seconds of inactivity (no audio, no keepalive). When no audio chunk is available within 5 seconds (e.g. during a pause), send a KeepAlive message as a raw text WebSocket frame:

```json
{ "type": "KeepAlive" }
```

Note: the v2 SDK does not expose `send_keep_alive()` as a method; the raw JSON must be sent directly via `conn.send(json.dumps({"type": "KeepAlive"}))`.

### Receiving transcripts

The SDK fires events via handlers registered with `conn.on(EventType.X, handler)`.

#### Results events (interim and segment-final)

Deepgram sends `Results` messages continuously:

```json
{
  "type": "Results",
  "channel": {
    "alternatives": [
      {
        "transcript": "hello how are",
        "confidence": 0.95
      }
    ]
  },
  "is_final": false
}
```

- `is_final: false` → interim update; emit `ASRResult(is_final=False)`
- `is_final: true` → segment finalized (turn may continue); store as latest segment, emit `ASRResult(is_final=False)`
- Discard results with an empty transcript

The module must track the transcript and confidence of the latest `is_final: true` segment to use when `EndOfTurn` fires.

#### EndOfTurn event (turn complete)

When the Flux model determines the speaker has finished their turn, it emits an `EndOfTurn` event. This is the signal for `ASRResult(is_final=True)`.

On `EndOfTurn`: emit `ASRResult` using the last stored `is_final: true` segment transcript and confidence, with `is_final=True`. If no segment was stored (e.g. the turn had no speech), do nothing.

After emitting, reset the stored segment state.

#### Mapping summary

| Deepgram event | `ASRResult.is_final` | Action |
|---|---|---|
| `Results` with `is_final=false` | `False` | Emit if transcript non-empty |
| `Results` with `is_final=true` | `False` | Store + emit if transcript non-empty |
| `EndOfTurn` | `True` | Emit using stored segment; reset state |

## Reconnection

On WebSocket disconnection or error:
1. Log the error
2. Wait with exponential backoff (1s, 2s, 4s, 8s max)
3. Re-establish the WebSocket connection
4. Resume sending audio chunks

Audio chunks received during reconnection are drained from the queue and discarded (to avoid sending stale audio after reconnect).

Note: each new v2 connection resets Deepgram-side timestamps to 00:00:00. This is acceptable since `asr://result` only holds the latest utterance, not a timestamped history.

## Event Handler Async Bridge

The SDK invokes event handlers synchronously from its internal dispatch loop. Since `on_result` is an async callback, handlers must bridge to the running event loop:

```python
loop.create_task(on_result(ASRResult(...)))
```

`loop` is captured with `asyncio.get_running_loop()` at the start of `start()`.

## Dependencies

- `deepgram-sdk` (official Deepgram Python SDK)
