# Deepgram ASR Module

## Overview

Implements `ASRModule` using the Deepgram real-time streaming WebSocket API.

- **Module type key:** `"deepgram"`
- **API:** Deepgram Streaming Speech-to-Text (WebSocket)

## Configuration Fields

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `api_key` | string | yes | — | Deepgram API key |
| `language` | string | no | `"en-US"` | BCP-47 language code |
| `model` | string | no | `"nova-2"` | Deepgram model name |
| `punctuate` | boolean | no | `true` | Enable automatic punctuation |
| `interim_results` | boolean | no | `true` | Enable interim results |

## Example Config

```json
{
  "type": "deepgram",
  "api_key": "YOUR_DEEPGRAM_API_KEY",
  "language": "en-US",
  "model": "nova-2",
  "punctuate": true,
  "interim_results": true
}
```

## WebSocket Connection

- **URL:** `wss://api.deepgram.com/v1/listen`
- **Auth:** `Authorization: Token <api_key>` header
- **Query params:** derived from config (`language`, `model`, `punctuate`, `interim_results`, `encoding=linear16`, `sample_rate=16000`, `channels=1`)

## Message Handling

### Sending audio

Raw PCM chunks from the audio queue are sent as binary WebSocket frames.

A KeepAlive message is sent every 5 seconds when no audio chunk is available, to prevent the connection from timing out:
```json
{ "type": "KeepAlive" }
```

### Receiving transcripts

Deepgram sends JSON messages. The module processes messages of `type = "Results"`:

```json
{
  "type": "Results",
  "channel": {
    "alternatives": [
      {
        "transcript": "hello how are you",
        "confidence": 0.98
      }
    ]
  },
  "is_final": true,
  "speech_final": true
}
```

Mapping to `ASRResult`:
- `transcript` ← `channel.alternatives[0].transcript`
- `is_final` ← `speech_final` (not `is_final` — `speech_final` marks end of an utterance)
- `confidence` ← `channel.alternatives[0].confidence`

Results with an empty transcript are discarded.

## Reconnection

On WebSocket disconnection or error:
1. Log the error
2. Wait with exponential backoff (1s, 2s, 4s, 8s max)
3. Re-establish the WebSocket connection
4. Resume sending audio chunks

Audio chunks received during reconnection are drained from the queue and discarded (to avoid sending stale audio after reconnect).

## Dependencies

- `deepgram-sdk` (official Deepgram Python SDK) or `websockets` for raw WebSocket handling
- Preference: use `deepgram-sdk` for maintainability
