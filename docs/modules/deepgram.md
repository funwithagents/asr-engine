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
| `api_key_env` | no | `DEEPGRAM_API_KEY` | Name of the environment variable holding the API key. The key itself is never written in a config |
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
| `api_key_env` | no | `DEEPGRAM_API_KEY` | Name of the environment variable holding the API key |
| `model` | no | `flux-general-en` | A Deepgram Flux-family model |
| `eot_threshold` | no | `0.7` | End-of-turn confidence threshold, 0.5 to 0.9. Higher waits for more certainty before closing the turn |
| `eot_timeout_ms` | no | `2000` | Silence after which the turn is closed whatever the confidence, in milliseconds |

## Audio and behaviour

- Both modules accept 8000, 16000, 24000, 44100 and 48000 Hz, mono, `linear16` or `mulaw`. The 16000 Hz default works as is.
- Without a VAD gate, audio is streamed continuously and Deepgram bills the silence too. With `"vad": {"type": "silero"}` the bill follows speech time.
- For `deepgram-v2`, use `{"type": "silero", "min_silence_duration_ms": 1500}`: the longer window lets Flux end the turn by its own judgement, and the gate only removes the idle time between utterances ([vad.md](../vad.md)). With a shorter window the gate can close before Flux has decided. The module then asks Flux to end the turn at once (its `ForceEndTurn` message), and the final arrives about 0.2 s later. That final comes from the gate's timing, not Flux's judgement, so a pause in mid-sentence can split the sentence.
- A lost connection is retried with a backoff; `engine.status()["connected"]` reports the state.

## Troubleshooting

- `Deepgram Nova module: no key under 'DEEPGRAM_API_KEY'`: the variable must be set in the process that runs the engine, not only in your interactive shell, or the key passed in code (`ASREngine(config, secrets={"DEEPGRAM_API_KEY": key})`).
- `Deepgram Nova module does not take 'api_key'`: a key written in the config is refused; keep it in an environment variable, named by `api_key_env`.
- `Deepgram Nova module 'punctuate' must be a boolean` (or another field; `Deepgram Flux` for `deepgram-v2`): a field has the wrong type or is out of range. Booleans are `true`/`false`, not strings; `eot_threshold` is from 0.5 to 0.9; `eot_timeout_ms` is a positive integer.
- The engine starts but `connected` stays `false`: the key is rejected or the network is unreachable; the log names the cause at each retry.

## See also

- [specs/modules/deepgram.md](../../specs/modules/deepgram.md): the modules' contract, with the WebSocket details and the message mapping.
- [examples/configs/deepgram-v1.json](../../examples/configs/deepgram-v1.json) and [examples/configs/deepgram-v2.json](../../examples/configs/deepgram-v2.json): complete server configs, with the VAD gate on.
