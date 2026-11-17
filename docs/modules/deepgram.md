# `deepgram-v1` and `deepgram-v2`

**Kind:** hosted · **Backend:** Deepgram Listen v1 and v2 · **Extra:** `deepgram` · **Credentials:** API key · **VAD gate:** optional

Two modules stream audio to [Deepgram](https://deepgram.com) over the official SDK's WebSockets and share one extra:

| Module | API | Best for | Default model |
|---|---|---|---|
| `deepgram-v1` | Listen v1 | General and multilingual transcription | `nova-3` |
| `deepgram-v2` | Listen v2 | English conversation, with Flux's built-in turn detection | `flux-general-en` |

```bash
uv sync --no-default-groups --extra deepgram
# For an installed package:
pip install 'asr-engine[deepgram]'
```

## `deepgram-v1`

```json
{
  "module": {
    "type": "deepgram-v1",
    "api_key_env": "DEEPGRAM_API_KEY",
    "model": "nova-3",
    "language": "multi"
  }
}
```

| Field | Required | Default | Description |
|---|---|---|---|
| `api_key_env` | one of `api_key_env` or `api_key` | — | Name of the environment variable holding the API key. Recommended for file-based configuration |
| `api_key` | one of `api_key_env` or `api_key` | — | Literal API key. Takes precedence over `api_key_env` |
| `model` | no | `nova-3` | A Deepgram model served by Listen v1 |
| `language` | no | `multi` | BCP-47 language code, or `multi` for automatic multilingual detection |
| `punctuate` | no | `true` | Automatic punctuation |
| `interim_results` | no | `true` | Emit interim results while speech is in progress |

## `deepgram-v2`

```json
{
  "module": {
    "type": "deepgram-v2",
    "api_key_env": "DEEPGRAM_API_KEY",
    "model": "flux-general-en",
    "eot_threshold": 0.7,
    "eot_timeout_ms": 2000
  }
}
```

| Field | Required | Default | Description |
|---|---|---|---|
| `api_key_env` | one of `api_key_env` or `api_key` | — | Name of the environment variable holding the API key |
| `api_key` | one of `api_key_env` or `api_key` | — | Literal API key. Takes precedence over `api_key_env` |
| `model` | no | `flux-general-en` | A Deepgram Flux-family model |
| `eot_threshold` | no | `0.7` | End-of-turn confidence threshold, 0.5 to 0.9. Higher waits for more certainty before closing the turn |
| `eot_timeout_ms` | no | `2000` | Silence after which the turn is closed whatever the confidence, in milliseconds |

## Audio and behaviour

- Both modules accept 8000, 16000, 24000, 44100 and 48000 Hz, mono, `linear16` or `mulaw`. The 16000 Hz default works as is.
- Without a VAD gate, audio is streamed continuously and Deepgram bills the silence too. With `"vad": {"type": "silero"}` the bill follows speech time.
- For `deepgram-v2`, use `{"type": "silero", "min_silence_duration_ms": 1500}`: the longer window lets Flux end the turn by its own judgement, and the gate only removes the idle time between utterances ([vad.md](../vad.md)).
- A lost connection is retried with a backoff; `engine.status()["connected"]` reports the state.

## Troubleshooting

- `environment variable 'DEEPGRAM_API_KEY' (api_key_env) is not set or is empty`: the variable must be set in the process that runs the engine, not only in your interactive shell.
- `requires 'api_key' or 'api_key_env' in config`: the module block has neither field.
- `deepgram-v1 module: 'punctuate' must be true or false` (or another field): a field has the wrong type or is out of range. Booleans are `true`/`false`, not strings; `eot_threshold` is from 0.5 to 0.9; `eot_timeout_ms` is a positive integer.
- The engine starts but `connected` stays `false`: the key is rejected or the network is unreachable; the log names the cause at each retry.

## See also

- [specs/modules/deepgram.md](../../specs/modules/deepgram.md): the modules' contract, with the WebSocket details and the message mapping.
- [examples/config/deepgram-v1.json](../../examples/config/deepgram-v1.json) and [examples/config/deepgram-v2.json](../../examples/config/deepgram-v2.json): complete server configs, with the VAD gate on.
