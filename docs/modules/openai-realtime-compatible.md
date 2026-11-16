# `openai-realtime-compatible`

**Kind:** hosted · **Backend:** OpenAI Realtime transcription, or any server speaking its protocol · **Extra:** `openai-compatible` · **Credentials:** API key (optional on other servers) · **VAD gate:** optional

A protocol module: it talks to any server that implements OpenAI's Realtime transcription API over a WebSocket. Hosted OpenAI is the default; LiteLLM, NVIDIA Dynamo, LocalAI and others expose the same protocol in front of their own models.

```bash
uv sync --no-default-groups --extra openai-compatible
# For an installed package:
pip install 'asr-engine[openai-compatible]'
```

```json
{
  "audio": { "sample_rate": 24000 },
  "module": {
    "type": "openai-realtime-compatible",
    "api_key_env": "OPENAI_API_KEY",
    "model": "gpt-4o-transcribe",
    "language": "en",
    "prompt": "validate"
  }
}
```

**`audio.sample_rate` must be `24000`**, the only rate the module accepts. With the 16000 default, engine construction fails with the supported values.

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

## Behaviour

- With `gpt-4o-transcribe`, the text of an utterance arrives once the server has closed it, so the interims come in a burst just before the final. `gpt-realtime-whisper` streams words as they are spoken.
- With `"vad": {"type": "silero"}` the gate ends each utterance and only speech is sent ([vad.md](../vad.md)).
- To check an endpoint's behaviour, run `uv run python scripts/probe_openai_realtime.py` from a checkout (`--url` for another server).

## Troubleshooting

- Engine construction fails on the sample rate: add `"audio": {"sample_rate": 24000}` to the engine block.
- No final ever arrives with `turn_detection: false`: nothing ends the utterances. Add an `engine.vad` block.
- A missing-key error against hosted OpenAI: set the variable named by `api_key_env` in the process running the engine. Against another server the key is optional and no `Authorization` header is sent without one.

## See also

- [specs/modules/openai-realtime-compatible.md](../../specs/modules/openai-realtime-compatible.md): the module's contract and the protocol messages it uses.
- [examples/config/openai-realtime-compatible.json](../../examples/config/openai-realtime-compatible.json): a complete server config, with the VAD gate on.
- [`openai-transcriptions-compatible`](openai-transcriptions-compatible.md): the batch HTTP endpoint, for servers that only implement `/v1/audio/transcriptions`.
