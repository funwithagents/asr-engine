# `mistral-realtime`

**Kind:** hosted · **Backend:** Mistral Voxtral 3 realtime · **Extra:** `mistral` · **Credentials:** API key · **VAD gate:** optional

The module streams audio to Mistral's Voxtral realtime transcription API over a WebSocket. It transcribes 45 languages with automatic detection, detects the end of a turn, and can be told which words to favour.

```bash
uv sync --no-default-groups --extra mistral
# For an installed package:
pip install 'asr-engine[mistral]'
```

```json
{
  "module": {
    "type": "mistral-realtime",
    "api_key_env": "MISTRAL_API_KEY",
    "language": "en",
    "context_bias": ["validate"]
  }
}
```

| Field | Default | Description |
|---|---|---|
| `api_key_env` / `api_key` | — (one required) | Name of the environment variable holding the API key, or the literal key |
| `model` | `voxtral-mini-transcribe-realtime-3` | Voxtral realtime model |
| `url` | `wss://api.mistral.ai/v1/audio/transcriptions/realtime` | Endpoint override, for a proxy |
| `language` | `null` | Two-letter ISO 639-1 code; `null` auto-detects |
| `streaming_delay_ms` | `480` | How far the text runs behind the audio: 80 to 1200, or 2400. Lower is faster, higher is more accurate |
| `context_bias` | `[]` | Words and phrases to favour, such as your trigger words |
| `turn_detection` | `true` | Close utterances on Voxtral's end-of-turn events. `false` relies on the silence timer. An object overrides the server's thresholds |
| `final_margin_ms` | `100` | Delay between the end-of-turn event and the final, so late text still lands in it |
| `finalize_after_silence_s` | `2.0` | Fallback: close an utterance after this long with no new text |
| `idle_close_s` | `20.0` | Close the session after this long without audio; it reopens on the next chunk |
| `stop_timeout_s` | `5.0` | How long stopping waits for the server to finish the transcription |

## Audio and behaviour

- Supported sample rates are 16000, 24000 and 44100 Hz, mono, `linear16`.
- Voxtral bills per audio minute ($0.008), silence included. Without a VAD gate the cost follows how long the engine runs, not how much is said; with `"vad": {"type": "silero"}` it follows speech time ([vad.md](../vad.md)).
- The text of an utterance arrives as append-only pieces: an interim never rewrites earlier words.
- To check the live API's behaviour, run `uv run python scripts/probe_mistral_realtime.py` from a checkout, with the key in the environment.

## Troubleshooting

- An error log naming HTTP 401 means the key was rejected: check `api_key` / `api_key_env`. HTTP 403 usually means the `model` is unknown or not available to the account. The module keeps retrying, so fixing the key in place recovers without a restart.
- A `ValueError` at engine construction names the field at fault, for example a `streaming_delay_ms` outside 80 to 1200 that is not 2400.

## See also

- [specs/modules/mistral.md](../../specs/modules/mistral.md): the module's contract, with the protocol and the measurements it rests on.
- [examples/config/mistral-realtime.json](../../examples/config/mistral-realtime.json): a complete server config, with the VAD gate on.
