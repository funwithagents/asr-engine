# `openai-transcriptions-compatible`

**Kind:** hosted · **Backend:** OpenAI's `/v1/audio/transcriptions`, or any server implementing it · **Extra:** `openai-compatible` · **Credentials:** API key (optional on other servers) · **VAD gate:** required

A protocol module for batch transcription over HTTP. Hosted OpenAI is the default; Groq, speaches, the whisper.cpp server, vLLM and others implement the same endpoint, hosted or on your own machine.

To run it against a server on your own machine, [local-speech-recognition.md](../local-speech-recognition.md) lists the servers that were run (whisper.cpp, speaches, the mlx-audio server) with their commands.

Each utterance is uploaded as one WAV file once it has ended, so the module needs the [VAD gate](../vad.md) to know where utterances end. Install the `silero` extra with it:

```bash
uv sync --no-default-groups --extra openai-compatible --extra silero
# For an installed package:
pip install 'asr-engine[openai-compatible,silero]'
```

```json
{
  "module": {
    "type": "openai-transcriptions-compatible",
    "api_key_env": "OPENAI_API_KEY",
    "model": "gpt-4o-transcribe",
    "language": "en"
  },
  "vad": { "type": "silero" }
}
```

| Field | Default | Description |
|---|---|---|
| `url` | `https://api.openai.com/v1/audio/transcriptions` | Endpoint URL, used as is. Point it at any server implementing it: Groq (`https://api.groq.com/openai/v1/audio/transcriptions`), or a local speaches, whisper.cpp or vLLM server |
| `api_key_env` / `api_key` | — | Name of the environment variable holding the API key, or the literal key. Required for hosted OpenAI, optional for other servers |
| `model` | `gpt-4o-transcribe` | The server's model name: `gpt-4o-mini-transcribe`, `whisper-1`, `whisper-large-v3-turbo`, … |
| `language` | `null` | Language code (ISO 639-1 on OpenAI); `null` lets the server detect it |
| `prompt` | `null` | Vocabulary and style hints, such as your trigger words |
| `extra_fields` | `{}` | More form fields for the server, e.g. `{"temperature": 0}` |
| `request_timeout_s` | `30.0` | Timeout of each HTTP request |
| `interim_interval_s` | `0` | Interims are off: each one re-uploads the growing utterance, which a hosted API bills. Set e.g. `0.5` against a free local server |
| `max_utterance_s` | `30.0` | Cut a longer utterance into several finals |
| `stop_timeout_s` | `10.0` | How long stopping waits for the last requests |

## Behaviour

- The final arrives one request after the speaker stops: 0.5 to 1 s on hosted OpenAI, as measured.
- Audio is 16000 Hz mono `linear16`, the default.
- When the engine starts, the module sends one short request to check the URL, the key and the model, so a wrong setting shows up at once and not at the first utterance.

## Troubleshooting

- `needs a VAD to know when an utterance ends`: add `"vad": {"type": "silero"}` to the engine block and install the `silero` extra.
- The engine starts but `connected` stays `false`: the start-up request failed. The log gives the HTTP status; check `url`, the key and `model`.
- The first words after a start are missing: a local server loads its model on the start-up request, and speech before `connected` is not transcribed. Wait for `connected`, or load the model on the server beforehand.
- No interim results: that is the default here. Set `interim_interval_s`, keeping in mind that each interim is a billed request on a hosted API.

## See also

- [specs/modules/openai-transcriptions-compatible.md](../../specs/modules/openai-transcriptions-compatible.md): the module's contract.
- [specs/batch-asr-module.md](../../specs/batch-asr-module.md): how batch modules buffer, decode and produce interims.
- [examples/config/openai-transcriptions-compatible.json](../../examples/config/openai-transcriptions-compatible.json): a complete server config.
- [local-speech-recognition.md](../local-speech-recognition.md): recipes for local servers.
- [`openai-realtime-compatible`](openai-realtime-compatible.md): the streaming protocol, for servers that speak OpenAI's Realtime API.
