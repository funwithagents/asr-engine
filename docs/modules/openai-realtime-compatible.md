# `openai-realtime-compatible`

**Kind:** hosted · **Backend:** OpenAI Realtime transcription, or any server speaking its protocol · **Extra:** `openai-compatible` · **Credentials:** API key (optional on other servers) · **VAD gate:** optional

A protocol module: it talks to any server that implements OpenAI's Realtime transcription API over a WebSocket. Hosted OpenAI is the default; LiteLLM, NVIDIA Dynamo, LocalAI and others expose the same protocol in front of their own models. A server on your own machine works the same way: [local-speech-recognition.md](../local-speech-recognition.md#mlx-audio-server-streaming) has one that was run, the mlx-audio server.

```bash
uv sync --no-default-groups --extra openai-compatible
# For an installed package:
pip install 'asr-engine[openai-compatible]'
```

```json
{
  "module": {
    "type": "openai-realtime-compatible",
    "api_key_env": "OPENAI_API_KEY",
    "model": "gpt-4o-transcribe",
    "language": "en",
    "prompt": "validate"
  }
}
```

Hosted OpenAI takes 24 kHz only. There is nothing to set: the microphone is opened at 24 kHz, and a file or source at another rate is resampled ([audio-input.md](../audio-input.md#audio-format-and-conversion)). For a server that takes other rates, see `supported_sample_rates` below.

| Field | Default | Description |
|---|---|---|
| `api_key_env` / `api_key` | — | Name of the environment variable holding the API key, or the literal key. Required for hosted OpenAI, optional for other servers |
| `url` | `wss://api.openai.com/v1/realtime?intent=transcription` | WebSocket URL, query included, used as is. Point it at any server speaking the protocol |
| `model` | `gpt-4o-transcribe` | Transcription model: `gpt-4o-mini-transcribe`, `whisper-1`, `gpt-realtime-whisper`, or the server's own |
| `language` | `null` | Two-letter ISO 639-1 code; `null` auto-detects |
| `prompt` | `null` | Vocabulary and style hints, such as your trigger words. Not every model accepts it |
| `turn_detection` | `true` | The server's VAD closes utterances. `false` for a model or server without it (e.g. `gpt-realtime-whisper`); utterances then end on the VAD gate's markers, so set `engine.vad`. An object overrides the server's settings |
| `noise_reduction` | `null` | `"near_field"` or `"far_field"` |
| `stop_timeout_s` | `5.0` | How long stopping waits for the last utterances to complete |
| `supported_sample_rates` | `null` (`[24000]`) | The sample rates your server accepts, when they are not hosted OpenAI's: what `module.audio_format.sample_rate` may be set to |
| `default_sample_rate` | `null` (`24000`) | The rate the module is fed unless `module.audio_format.sample_rate` sets another. Set alone, it is the only rate supported |

## Behaviour

- With `gpt-4o-transcribe`, the text of an utterance arrives once the server has closed it, so the interims come in a burst just before the final. `gpt-realtime-whisper` streams words as they are spoken.
- With `"vad": {"type": "silero"}` the gate ends each utterance and only speech is sent ([vad.md](../vad.md)).
- To check an endpoint's behaviour, run `uv run python scripts/probe_openai_realtime.py` from a checkout (`--url` for another server). `scripts/check_local_server.py realtime` runs the whole engine against a local server.

## Troubleshooting

- Engine construction fails on `engine.module.audio_format.sample_rate`: the rate is not one the server is declared to take (24000 for hosted OpenAI). Remove the field, or describe the server with `supported_sample_rates`.
- The session is rejected with a message about the rate: the server does not take the rate it was sent. Fix `supported_sample_rates` / `default_sample_rate` to match it.
- No final ever arrives with `turn_detection: false`: nothing ends the utterances. Add an `engine.vad` block.
- `the server accepted the session but did not apply ...` in the log: the server answered the session setup but kept its own value for the settings named (turn detection, model or language). It usually implements an older version of the protocol. Transcription may still work with the server's settings; if the line names `turn_detection`, do not rely on `"turn_detection": false` or on the VAD gate with that server. speaches is one such server ([details](../local-speech-recognition.md#speaches-faster-whisper)).
- A missing-key error against hosted OpenAI: set the variable named by `api_key_env` in the process running the engine. Against another server the key is optional and no `Authorization` header is sent without one.

## See also

- [specs/modules/openai-realtime-compatible.md](../../specs/modules/openai-realtime-compatible.md): the module's contract and the protocol messages it uses.
- [examples/config/openai-realtime-compatible.json](../../examples/config/openai-realtime-compatible.json): a complete server config, with the VAD gate on.
- [`openai-transcriptions-compatible`](openai-transcriptions-compatible.md): the batch HTTP endpoint, for servers that only implement `/v1/audio/transcriptions`.
