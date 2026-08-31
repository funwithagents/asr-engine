---
code:
  - src/asr_mcp/config.py
tests:
  - tests/test_config.py
---

# Configuration

**Status:** Implemented

## Config File

The server reads a JSON config file at startup, passed as a CLI argument:

```bash
uv run asr-mcp-server --config config.json
```

## Schema

```json
{
  "server": {
    "host": "0.0.0.0",
    "port": 8000
  },
  "audio": {
    "device": null,
    "output_device": null
  },
  "asr": {
    "type": "<module_type>",
    ...module-specific fields...
  },
  "engine": {
    "auto_start": true
  },
  "listen": {
    "end_of_utterance_mode": "trigger_word",
    "trigger_words": ["submit", "enter", "validate", "send", "confirm", "go",
                      "envoyer", "valider", "confirmer", "soumettre", "entree", "entrée"],
    "initial_silence_timeout_s": 10.0,
    "end_of_speech_timeout_s": 5.0,
    "sound_feedback": true
  }
}
```

### `server` block

| Field | Type | Default | Description |
|---|---|---|---|
| `host` | string | `"127.0.0.1"` | Host to bind the HTTP server to |
| `port` | integer | `8000` | Port to bind the HTTP server to |

### `audio` block

| Field | Type | Default | Description |
|---|---|---|---|
| `device` | string or null | `null` | Audio input device name or index. `null` = system default |
| `output_device` | string or null | `null` | Audio output device name or index for sound feedback playback. `null` = system default |
| `audio_file` | string or null | `null` | Path to a WAV file (16 kHz, mono, s16) to stream **instead of** the live input device. When set, the server feeds this file through a `FileAudioSource` at real-time pace. Primarily a testing hook — see [e2e-testing.md](e2e-testing.md). |
| `trailing_silence_s` | float | `0.0` | (`audio_file` only) Seconds of silence appended after the file ends, so the ASR backend receives the tail-silence it needs to emit a final result. Ignored for live capture. |

### `asr` block

| Field | Type | Required | Description |
|---|---|---|---|
| `type` | string | yes | Identifies which ASR module to load (e.g. `"deepgram"`) |
| *(other fields)* | any | depends | Module-specific configuration, parsed by the module |

### `engine` block

| Field | Type | Default | Description |
|---|---|---|---|
| `auto_start` | boolean | `true` | If `true`, ASR starts automatically at server startup (existing behaviour). If `false`, the engine is initialised (config validated, audio device checked) but not started — use the `start` tool or `listen` tool to begin capture. |

### `listen` block

Controls the behaviour of the `listen` MCP tool.

| Field | Type | Default | Description |
|---|---|---|---|
| `end_of_utterance_mode` | string | `"trigger_word"` | How to detect end of speech: `"trigger_word"` or `"timeout"`. |
| `trigger_words` | list of strings | see below | Words that end the session in `trigger_word` mode. Replaces the built-in default list entirely when specified. |
| `initial_silence_timeout_s` | float | `10.0` | (`timeout` mode only) Seconds of silence from session start before giving up. |
| `end_of_speech_timeout_s` | float | `5.0` | (`timeout` mode only) Seconds of silence after the last ASR event (interim or final) before ending the session. |
| `sound_feedback` | boolean | `true` | Play start/stop audio cues during `listen`. Set to `false` to disable. |

**Default trigger words:**
```
submit, enter, validate, send, confirm, go,
envoyer, valider, confirmer, soumettre, entree, entrée
```

**`end_of_utterance_mode` behaviour summary:**

| Mode | Ends when | Timeouts |
|---|---|---|
| `trigger_word` | A final result contains a trigger word (case-insensitive substring match) | None — waits indefinitely |
| `timeout` | Silence for `end_of_speech_timeout_s` after last event, or `initial_silence_timeout_s` with no speech at all | Both timers active |

## Example: Deepgram config

```json
{
  "server": {
    "host": "0.0.0.0",
    "port": 8000
  },
  "audio": {
    "device": null
  },
  "asr": {
    "type": "deepgram_v1",
    "api_key": "YOUR_DEEPGRAM_API_KEY",
    "language": "en-US",
    "model": "nova-3"
  }
}
```

## Validation

- The server must fail fast at startup with a clear error message if:
  - The config file is missing or not valid JSON
  - Required fields (`asr.type`) are absent
  - The specified `asr.type` is unknown
  - Module-specific required fields (e.g. `api_key`) are missing
- The specified audio device is verified when audio capture first starts, and an
  unknown device raises a clear error there — at startup when `engine.auto_start`
  is `true`, or on the first `start`/`listen` call when it is `false`.
