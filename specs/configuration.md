---
code:
  - src/asr_engine/config.py
tests:
  - tests/test_config.py
---

# Configuration

**Status:** Implemented

## Config File

The MCP server reads a JSON config file at startup, passed as a CLI argument:

```bash
uv run asr-engine-mcp --config config.json
```

The file has two top-level blocks: `server` (an MCP-only concern — host/port) and `engine` (everything the `ASREngine` itself needs). The whole file maps to an **`MCPServerConfig`** (the `server` + `engine` wrapper); only the `asr-engine-mcp` entry point needs it. A direct importer of the library builds an `ASREngineConfig` from the `engine` block alone and never needs `server`.

### Constructors

Both config classes expose the same three-rung constructor ladder — pick the rung matching what you have in hand:

| Constructor | Input | Notes |
|---|---|---|
| `from_dict(d)` | an already-parsed dict | where all validation lives |
| `from_json(text)` | a JSON string | parses `text`, then `from_dict` |
| `from_json_file(path)` | a path to a JSON file | reads the file as UTF-8 (on every platform, not the locale default), then `from_json` |

`MCPServerConfig`'s constructors take the **whole-file** shape (`{server, engine}`); `ASREngineConfig`'s take the **`engine` block** shape (`module`, `segmentation`, … at top level) — *not* a whole config file. `MCPServerConfig.from_dict` parses the `server` block itself and delegates the `engine` block to `ASREngineConfig.from_dict`, so both paths run identical engine validation and produce identical engine configs. `MCPServerConfig.from_json_file` is the MCP server's load path (`asr-engine-mcp --config …`).

## Schema

```json
{
  "server": {
    "host": "127.0.0.1",
    "port": 8000
  },
  "engine": {
    "auto_start": true,
    "auto_start_dictation": false,
    "listen_default_segmentation_mode": "trigger_word",
    "dictation_default_segmentation_mode": "trigger_word",
    "segmentation": {
      "trigger_words": ["submit", "enter", "validate", "send", "confirm", "go",
                        "envoyer", "valider", "confirmer", "soumettre", "entree", "entrée"],
      "initial_silence_timeout_s": 10.0,
      "end_of_speech_timeout_s": 5.0
    },
    "sound_feedback": {
      "enabled": true,
      "output_device": null
    },
    "audio_source": {
      "type": "input-device",
      "device": null,
      "audio_format": { "sample_rate": null, "channels": null, "encoding": null }
    },
    "module": {
      "type": "<module_type>",
      "audio_format": { "sample_rate": null, "channels": null, "encoding": null },
      "...": "...module-specific fields..."
    },
    "vad": null
  }
}
```

### `server` block (MCP only)

Consumed by the MCP entry point, not by `ASREngine`.

| Field | Type | Default | Description |
|---|---|---|---|
| `host` | string | `"127.0.0.1"` | Host to bind the HTTP server to |
| `port` | integer | `8000` | Port to bind the HTTP server to |

### `engine` block → `ASREngineConfig`

The whole `engine` block maps to one `ASREngineConfig` dataclass, the single argument to `ASREngine(config=...)` (see [engine.md](engine.md)). It carries the scalar engine settings plus five nested sub-blocks and the optional `vad` block.

**`ASREngineConfig.from_dict(engine_block)`** is the public, in-memory constructor for direct importers: it takes the raw `engine` block (the object under the top-level `"engine"` key, **not** the whole file) and returns a validated `ASREngineConfig`, running exactly the same validation as file loading (see [Validation](#validation)). This is the "build from the `engine` block alone" path — no `server` block, no environment reads (module `api_key`/`api_key_env` are carried through as extra fields and resolved later at engine construction, so a config with unset credentials still builds). `from_json`/`from_json_file` (see [Constructors](#constructors)) layer JSON string and file loading over it; `from_json_file` reads a file whose top-level object *is* an `engine` block, not a whole `config.json`. `MCPServerConfig.from_dict` delegates its `engine` block here, so the direct and MCP entry points produce identical engine configs.

| Field | Type | Default | Description |
|---|---|---|---|
| `auto_start` | boolean | `true` | If `true`, ASR starts automatically at server startup. If `false`, the engine is initialised but not started — use the `start` or `listen` tool to begin capture. |
| `auto_start_dictation` | boolean | `false` | If `true`, the server starts a persistent dictation session (mode = `dictation_default_segmentation_mode`, never self-ending) immediately after auto-starting, so the always-on `asr://segment` stream aggregates from startup. **Requires `auto_start=true`** (validated). |
| `listen_default_segmentation_mode` | string | `"trigger_word"` | The segmentation mode `listen()` uses when its caller passes no explicit mode. One of `"utterance"`, `"trigger_word"`, or `"timeout"`. |
| `dictation_default_segmentation_mode` | string | `"trigger_word"` | The segmentation mode `start_dictation()` uses when its caller passes no explicit mode. One of `"utterance"`, `"trigger_word"`, or `"timeout"`. |
| `segmentation` | object | see below | Segmentation parameters (trigger words + timeouts) shared by the always-on stream, `listen`, **and** dictation. |

> **No always-on `segmentation_mode`.** The always-on `asr://segment` stream is `utterance` mode (one segment per final utterance) unless a *dictation* session is active. To aggregate utterances (trigger-word or timeout), either set `auto_start_dictation=true` so the server begins in a persistent dictation at startup, or have a consumer call the `start_dictation` tool / use `listen` — all of which switch the engine's segmentation mode and revert to `utterance` when they end. This is what `asr-to-terminal` relies on for continuous aggregation (see [asr-to-terminal.md](asr-to-terminal.md)). See [engine.md](engine.md).
| `sound_feedback` | object | see below | Start/stop audio cues, owned and played by the engine during `listen`. |
| `audio` | object | see below | Audio input / file-source settings. |
| `module` | object | — | ASR backend selection and its module-specific fields. **Required.** |
| `vad` | object / null | `null` | Optional voice-activity gate in front of the ASR module. Omitted or `null`: no VAD, and the pipeline is unchanged. |

#### `engine.segmentation` sub-block

The single source of segmentation parameters. The always-on segmenter, `listen`, and dictation all read these — `listen` and dictation only ever change the *mode*, never these params (see [engine.md](engine.md)).

| Field | Type | Default | Description |
|---|---|---|---|
| `trigger_words` | list of strings | see below | Words that close a segment in `trigger_word` mode. Replaces the built-in default list entirely when specified. |
| `initial_silence_timeout_s` | float | `10.0` | (`timeout` mode only) Seconds of silence from segment start before closing. |
| `end_of_speech_timeout_s` | float | `5.0` | (`timeout` mode only) Seconds of silence after the last event before closing a segment. |

#### `engine.sound_feedback` sub-block

| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | boolean | `true` | Play start/stop audio cues during `listen`. `false` installs a no-op. |
| `output_device` | string / integer / null | `null` | Output device name or index for cue playback. `null` = system default. |

> **No `engine.logging` block.** Logging is an application-layer concern: the library configures nothing, and the `asr-engine-mcp` server's log level is set by the `--log-level` CLI flag (default `INFO`), not by config. See [project.md](project.md) and [mcp-server.md](mcp-server.md).

#### `engine.audio_source` sub-block

Where the audio comes from. `type` selects the other fields the block takes; a field of another type is an error. The block is optional: without it, or without a `type`, the source is the default input device.

| `type` | Source | Other fields |
|---|---|---|
| `"input-device"` | A live input device (`AudioCapture`): a microphone, a line input, a loopback or virtual device. Hyphenated like the module types | `device`, `audio_format` |
| `"file"` | An audio file, streamed at real-time pace (`FileAudioSource`). Primarily a testing hook — see [e2e-testing.md](e2e-testing.md) | `audio_file`, `trailing_silence_s` |
| `"custom"` | An `AudioSource` the application passes to `ASREngine(config, audio_source=...)` ([audio-source.md](audio-source.md)). Not usable with the MCP server, which is given no source | none |

| Field | Type | Default | Description |
|---|---|---|---|
| `type` | string | `"input-device"` | `"input-device"`, `"file"` or `"custom"`. |
| `device` | string / null | `null` | (`input-device`) Audio input device name or index. `null` = system default. |
| `audio_format` | object / null | `null` | (`input-device`) The format the device is opened at: an [`audio_format` object](#the-audio_format-object). A field left unset follows the module's format. |
| `audio_file` | string | — (required for `file`) | (`file`) Path to an audio file (WAV, MP3, … — anything `libsndfile`/`soundfile` decodes). Its own rate and channel count are used; the engine converts them to the module's when they differ ([format-conversion.md](format-conversion.md)). |
| `trailing_silence_s` | float | `0.0` | (`file`) Seconds of silence appended after the file ends, so the ASR backend receives the tail-silence it needs to emit a final result. |

The type is checked against how the engine is built: `"custom"` with no source passed, or a source passed with another type, is an error at engine construction. It maps to `ASREngineConfig.audio_source: AudioSourceConfig`.

> This block was `engine.audio`, with no `type`. The old block is refused with a message naming the new one. Its `sample_rate`, `channels` and `encoding` now sit in `audio_format`, and `on_unsupported_format` is gone: an unsupported format is converted, not refused.

#### `engine.module` sub-block

| Field | Type | Required | Description |
|---|---|---|---|
| `type` | string | yes | Identifies which ASR module to load (e.g. `"deepgram-v1"`). There is **no default module**. Provider modules need their extra installed (see [asr-module-interface.md](asr-module-interface.md) "Optional dependencies (extras)"); `"fake"` is a test double for tests only (see [modules/fake.md](modules/fake.md)). |
| `audio_format` | object / null | no | The format the module is fed, for every module: an [`audio_format` object](#the-audio_format-object). A field left unset is the module's own default. Read by the engine, not passed to the module. |
| *(other fields)* | any | depends | Module-specific configuration, parsed by the module. |

#### The `audio_format` object

The same object sets a format in `engine.module` (what the module is fed) and in a `input-device` `engine.audio_source` (what the device is opened at). Every field is optional.

```json
{ "sample_rate": 24000, "channels": 1, "encoding": "linear16" }
```

| Field | Type | Description |
|---|---|---|
| `sample_rate` | integer / null | Sample rate in Hz. |
| `channels` | integer / null | Channel count. Every bundled module takes mono. |
| `encoding` | string / null | `"linear16"` (s16 PCM) or `"mulaw"` (G.711 μ-law). |

How an unset field is resolved, in order:

1. **The module format** (`engine.module_audio_format`): each field of `engine.module.audio_format`, else the module's own default. A set value must be one the module supports.
2. **The source format** (`engine.audio_format`), by source type:
   - `input-device`: each field of `engine.audio_source.audio_format`, else the module format's value;
   - `file`: the file's own rate and channel count, in the module format's encoding;
   - `custom`: the format the source declares, else the module format.

When the two formats differ, the engine converts the source's audio into the module's. The rules, examples and the converter are in [format-conversion.md](format-conversion.md). `mulaw` silence is `0xFF`, not zero bytes, so the sources transcode silence rather than assume it.

#### `engine.vad` sub-block

Optional. When present, the engine puts a speech gate between the audio source and the ASR module: silence is dropped, and each utterance's final is requested when the speaker stops. The design, which modules it suits and how to tune it per backend are in [vad.md](vad.md).

```json
"vad": {
  "type": "silero",
  "threshold": 0.5,
  "min_speech_duration_ms": 250,
  "min_silence_duration_ms": 500,
  "speech_pad_ms": 300
}
```

| Field | Type | Default | Description |
|---|---|---|---|
| `type` | string | — (required when the block is present) | VAD module to load, a key of the VAD registry. Built in: `"silero"` (needs the `silero` extra; its 2.3 MB model is downloaded on first use). |
| `threshold` | float | `0.5` | Speech probability at or above which a frame counts as speech. Strictly between 0 and 1. |
| `min_speech_duration_ms` | integer | `250` | Consecutive speech before the gate opens. ≥ 0. |
| `min_silence_duration_ms` | integer | `500` | Consecutive non-speech before the gate closes and the final is requested (the hangover). > 0. Raise it to about `1500` for a backend with its own turn detection (`deepgram-v2`, `kyutai`), so the backend still ends the turn itself — see [vad.md](vad.md) "Letting the backend end the turn first". |
| `speech_pad_ms` | integer | `300` | Audio kept from before the detected onset. ≥ 0. |
| *(other fields)* | any | — | Passed to the VAD module, like `engine.module`'s extra fields. `silero` takes `model_path` (a local copy of its model, for machines without network access) and rejects any other. |

It maps to `ASREngineConfig.vad: VadConfig | None` (`type`, the four gate parameters, and `extra`). Any module format is valid with VAD on: the gate analyses its own copy of the audio and forwards the bytes it received.

**Default trigger words:**
```
submit, enter, validate, send, confirm, go,
envoyer, valider, confirmer, soumettre, entree, entrée
```

**Segmentation mode behaviour summary** (applies to `listen` and dictation; the always-on stream is fixed to `utterance`):

| Mode | Segment closes when | Timeouts |
|---|---|---|
| `utterance` | Every final utterance (one segment each) | None |
| `trigger_word` | A final utterance contains a trigger word (case-insensitive substring match) | None |
| `timeout` | Silence for `end_of_speech_timeout_s` after last event, or `initial_silence_timeout_s` with no speech at all | Both timers active |

All three modes are valid for `listen` and dictation. In `utterance` mode a `listen` returns after exactly one final utterance, and a dictation with `end_on_final_segment=true` ends after one — coherent, if degenerate.

Complete server configs for every ASR module ship in `examples/config/` (`deepgram-v1.json`, `deepgram-v2.json`, `mistral-realtime.json`, `openai-realtime-compatible.json`, `openai-transcriptions-compatible.json`, `kyutai.json`, `mlx-audio.json`), each with the `engine.vad` setting recommended for its module ([vad.md](vad.md) "Which modules to use with VAD"). `tests/examples/test_configs.py` checks they parse and that their module and VAD combination is accepted.

## Example: Deepgram config

Requires the `deepgram` extra (`pip install 'asr-engine[deepgram]'`), plus `silero` for the VAD block (drop the block to run ungated).

```json
{
  "server": {
    "host": "0.0.0.0",
    "port": 8000
  },
  "engine": {
    "auto_start": true,
    "audio_source": { "type": "input-device", "device": null },
    "module": {
      "type": "deepgram-v1",
      "api_key_env": "DEEPGRAM_API_KEY",
      "language": "en-US",
      "model": "nova-3"
    },
    "vad": { "type": "silero" }
  }
}
```

## Example: Voxtral config

Requires the `mistral` extra (`pip install 'asr-engine[mistral]'`). See [modules/mistral.md](modules/mistral.md) for the module fields. A complete file ships as `examples/config/mistral-realtime.json`, with `"vad": {"type": "silero"}` (needs the `silero` extra).

```json
{
  "server": {
    "host": "0.0.0.0",
    "port": 8000
  },
  "engine": {
    "auto_start": true,
    "audio_source": { "type": "input-device", "device": null },
    "module": {
      "type": "mistral-realtime",
      "api_key_env": "MISTRAL_API_KEY",
      "language": "en",
      "context_bias": ["validate"]
    },
    "vad": { "type": "silero" }
  }
}
```

## Example: OpenAI Realtime config (24 kHz)

Requires the `openai-compatible` extra (`pip install 'asr-engine[openai-compatible]'`). See [modules/openai-realtime-compatible.md](modules/openai-realtime-compatible.md) for the module fields, and for pointing `url` at another compatible server. The module is fed 24 kHz, the only rate hosted OpenAI accepts: the microphone is opened at that rate, and a source at another rate is resampled. No `audio_format` is needed. A complete file ships as `examples/config/openai-realtime-compatible.json`, with `"vad": {"type": "silero"}` (needs the `silero` extra).

```json
{
  "server": {
    "host": "0.0.0.0",
    "port": 8000
  },
  "engine": {
    "auto_start": true,
    "audio_source": { "type": "input-device", "device": null },
    "module": {
      "type": "openai-realtime-compatible",
      "api_key_env": "OPENAI_API_KEY",
      "model": "gpt-4o-transcribe",
      "language": "en",
      "prompt": "validate"
    },
    "vad": { "type": "silero" }
  }
}
```

## Example: OpenAI transcriptions config (batch over HTTP)

Requires the `openai-compatible` extra, plus `silero`: **`engine.vad` is mandatory**, as for every batch module ([batch-asr-module.md](batch-asr-module.md)). See [modules/openai-transcriptions-compatible.md](modules/openai-transcriptions-compatible.md) for the module fields, and for pointing `url` at Groq or a local server. A complete file ships as `examples/config/openai-transcriptions-compatible.json`.

```json
{
  "engine": {
    "audio_source": { "type": "input-device", "device": null },
    "module": {
      "type": "openai-transcriptions-compatible",
      "api_key_env": "OPENAI_API_KEY",
      "model": "gpt-4o-transcribe",
      "language": "en"
    },
    "vad": { "type": "silero" }
  }
}
```

## Example: mlx-audio config (local batch model, Apple Silicon)

Requires the `mlx-audio` extra (Apple Silicon), plus `silero`: **`engine.vad` is mandatory**, since a batch module has no end-of-utterance signal of its own ([batch-asr-module.md](batch-asr-module.md)). A complete file ships as `examples/config/mlx-audio.json`.

```json
{
  "engine": {
    "audio_source": { "type": "input-device", "device": null },
    "module": {
      "type": "mlx-audio",
      "model": "mlx-community/Qwen3-ASR-0.6B-8bit",
      "generate_kwargs": { "language": "English" }
    },
    "vad": { "type": "silero" }
  }
}
```

## Example: Kyutai config (local model, 24 kHz)

Requires the `kyutai-mlx` (Apple Silicon) or `kyutai-torch` extra, in an environment without `mlx-audio` ([modules/kyutai.md](modules/kyutai.md) "Installation"). No key: the model runs on-device (see [modules/kyutai.md](modules/kyutai.md) for the module fields). A complete file ships as `examples/config/kyutai.json`, with `"vad": null`: the model is local and free and has its own end-of-turn head, so the gate is rarely worth it. Do not confuse `module.vad` (that head) with `engine.vad` (the gate).

```json
{
  "server": {
    "host": "0.0.0.0",
    "port": 8000
  },
  "engine": {
    "auto_start": true,
    "audio_source": { "type": "input-device", "device": null },
    "module": {
      "type": "kyutai",
      "backend": "auto",
      "hf_repo": "kyutai/stt-1b-en_fr-candle",
      "vad": true
    }
  }
}
```

The module declares a strict 24 kHz contract. No `audio_format` is needed: the microphone is opened at 24 kHz, and a source at another rate is resampled ([format-conversion.md](format-conversion.md)).

## Example: test config (fake module)

For tests only — a keyless, deterministic scripted module (see [modules/fake.md](modules/fake.md)):

```json
{
  "engine": {
    "audio_source": { "type": "file", "audio_file": "tests-e2e/fixtures/sample_44100_theskyisblue.mp3" },
    "module": {
      "type": "fake",
      "utterances": [{ "text": "the sky is blue", "start_s": 0.3, "end_s": 1.2 }]
    }
  }
}
```

## Validation

- The server must fail fast at startup with a clear error message if:
  - The config file is missing or not valid JSON
  - The required field `engine.module.type` is absent
  - `engine.audio_source.type` is not one of `input-device` / `file` / `custom`, the block holds a field its type does not take, or `audio_file` is missing with type `file`
  - The old `engine.audio` block is present
  - The specified `engine.module.type` is unknown
  - The specified module's optional dependencies (its extra) are not installed — checked at engine construction, not at config parsing, with an `ImportError` naming the extra to install
  - Module-specific required fields (e.g. `api_key` / `api_key_env`) are missing
  - `engine.listen_default_segmentation_mode` is not one of `utterance` / `trigger_word` / `timeout`
  - `engine.dictation_default_segmentation_mode` is not one of `utterance` / `trigger_word` / `timeout`
  - `engine.auto_start_dictation` is `true` while `engine.auto_start` is `false`
  - `engine.audio_source.audio_format` or `engine.module.audio_format` is not an object, holds an unknown field, a `sample_rate` or `channels` that is not a positive integer, or an `encoding` that is not `linear16` / `mulaw` (`null` is "not set" throughout)
  - `engine.vad` is present and not an object, or its `type` is missing or not a non-empty string
  - `engine.vad.threshold` is not a number strictly between 0 and 1, or one of `min_speech_duration_ms` (≥ 0), `min_silence_duration_ms` (> 0), `speech_pad_ms` (≥ 0) is not an integer within its bound — each error names `engine.vad.<field>`
  - The specified `engine.vad.type` is unknown, its extra is not installed, or its own fields are invalid — checked at engine construction, like `engine.module.type`
  - `engine.vad` is set with an ASR module that does not support VAD gating, or is absent with a module that requires it — checked at engine construction (see [asr-module-interface.md](asr-module-interface.md) "VAD gating")
- The source and module formats are resolved at engine construction (startup), where these fail with a clear error ([format-conversion.md](format-conversion.md)):
  - a field of `engine.module.audio_format` is not a value the module supports (the message lists them)
  - `engine.audio_source.type` is `custom` and no source was passed to the engine (always the case for the MCP server), or a source was passed with another type
  - the `audio_file` is missing or unreadable
  - the source's channels cannot be converted to the module's (neither side is mono)
- A source format the module does not support is **not** an error: the engine converts it.
- The specified audio device is verified when audio capture first starts, and an unknown device raises a clear error there — at startup when `engine.auto_start` is `true`, or on the first `start`/`listen` call when it is `false`.
