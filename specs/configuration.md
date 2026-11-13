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
    "audio": {
      "device": null,
      "audio_file": null,
      "trailing_silence_s": 0.0,
      "sample_rate": 16000,
      "channels": 1,
      "encoding": "linear16",
      "on_unsupported_format": "error"
    },
    "module": {
      "type": "<module_type>",
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

#### `engine.audio` sub-block

| Field | Type | Default | Description |
|---|---|---|---|
| `device` | string / null | `null` | Audio input device name or index. `null` = system default. |
| `audio_file` | string / null | `null` | Path to an audio file (WAV, MP3, … — anything `libsndfile`/`soundfile` decodes) to stream **instead of** the live input device, fed through a `FileAudioSource` at real-time pace. The file's own rate/channels must match `sample_rate`/`channels` (files are validated, not resampled). Primarily a testing hook — see [e2e-testing.md](e2e-testing.md). |
| `trailing_silence_s` | float | `0.0` | (`audio_file` only) Seconds of silence appended after the file ends, so the ASR backend receives the tail-silence it needs to emit a final result. Ignored for live capture. |
| `sample_rate` | integer | `16000` | Desired end-to-end sample rate delivered to the ASR module. Reconciled against the module's declared support (see [asr-module-interface.md](asr-module-interface.md)). |
| `channels` | integer | `1` | Desired channel count. Reconciled like `sample_rate`; modules generally declare mono-only. |
| `encoding` | string | `"linear16"` | Wire encoding delivered to the module: `"linear16"` (s16 PCM) or `"mulaw"` (G.711 μ-law, transcoded in-pipeline from the captured s16). Reconciled like `sample_rate`. |
| `on_unsupported_format` | string | `"error"` | Policy when a configured dimension isn't in the module's supported set: `"error"` fails fast at engine construction with a message listing supported values; `"fallback"` uses the module's declared default for that dimension and logs a warning. |

The three format fields (`sample_rate`/`channels`/`encoding`) form the desired `AudioFormat`. Live capture opens its stream at that rate/channels (PortAudio does any device conversion) and transcodes to the encoding; `mulaw` silence is `0xFF`, not zero bytes, so silence is transcoded rather than assumed. WAV file sources are validated against the configured rate/channels (they are **not** resampled) and re-encoded to `mulaw` when asked.

#### `engine.module` sub-block

| Field | Type | Required | Description |
|---|---|---|---|
| `type` | string | yes | Identifies which ASR module to load (e.g. `"deepgram_v1"`). There is **no default module**. Provider modules need their extra installed (see [asr-module-interface.md](asr-module-interface.md) "Optional dependencies (extras)"); `"fake"` is a test double for tests only (see [modules/fake.md](modules/fake.md)). |
| *(other fields)* | any | depends | Module-specific configuration, parsed by the module. |

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
| `min_silence_duration_ms` | integer | `500` | Consecutive non-speech before the gate closes and the final is requested (the hangover). > 0. Raise it to about `1500` for a backend with its own turn detection (`deepgram_v2`, `kyutai`), so the backend still ends the turn itself — see [vad.md](vad.md) "Letting the backend end the turn first". |
| `speech_pad_ms` | integer | `300` | Audio kept from before the detected onset. ≥ 0. |
| *(other fields)* | any | — | Passed to the VAD module, like `engine.module`'s extra fields. `silero` takes `model_path` (a local copy of its model, for machines without network access) and rejects any other. |

It maps to `ASREngineConfig.vad: VadConfig | None` (`type`, the four gate parameters, and `extra`). Any pipeline `sample_rate` / `encoding` the ASR module supports is valid with VAD on: the gate analyses its own copy of the audio and forwards the original bytes.

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

Complete server configs for every ASR module ship in `examples/config/` (`deepgram_v1.json`, `deepgram_v2.json`, `mistral_realtime.json`, `openai_realtime_compatible.json`, `kyutai.json`), each with the `engine.vad` setting recommended for its module ([vad.md](vad.md) "Which modules to use with VAD"). `tests/examples/test_configs.py` checks they parse and that their module and VAD combination is accepted.

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
    "audio": { "device": null },
    "module": {
      "type": "deepgram_v1",
      "api_key_env": "DEEPGRAM_API_KEY",
      "language": "en-US",
      "model": "nova-3"
    },
    "vad": { "type": "silero" }
  }
}
```

## Example: Voxtral config

Requires the `mistral` extra (`pip install 'asr-engine[mistral]'`). See [modules/mistral.md](modules/mistral.md) for the module fields. A complete file ships as `examples/config/mistral_realtime.json`, with `"vad": {"type": "silero"}` (needs the `silero` extra).

```json
{
  "server": {
    "host": "0.0.0.0",
    "port": 8000
  },
  "engine": {
    "auto_start": true,
    "audio": { "device": null },
    "module": {
      "type": "mistral_realtime",
      "api_key_env": "MISTRAL_API_KEY",
      "language": "en",
      "context_bias": ["validate"]
    },
    "vad": { "type": "silero" }
  }
}
```

## Example: OpenAI Realtime config (24 kHz)

Requires the `openai-realtime-compatible` extra (`pip install 'asr-engine[openai-realtime-compatible]'`). See [modules/openai-realtime-compatible.md](modules/openai-realtime-compatible.md) for the module fields, and for pointing `url` at another compatible server. **`engine.audio.sample_rate` must be `24000`**, the only rate hosted OpenAI accepts. A complete file ships as `examples/config/openai_realtime_compatible.json`, with `"vad": {"type": "silero"}` (needs the `silero` extra).

```json
{
  "server": {
    "host": "0.0.0.0",
    "port": 8000
  },
  "engine": {
    "auto_start": true,
    "audio": { "device": null, "sample_rate": 24000 },
    "module": {
      "type": "openai_realtime_compatible",
      "api_key_env": "OPENAI_API_KEY",
      "model": "gpt-4o-transcribe",
      "language": "en",
      "prompt": "validate"
    },
    "vad": { "type": "silero" }
  }
}
```

## Example: Kyutai config (local model, 24 kHz)

Requires the `kyutai-mlx` (Apple Silicon) or `kyutai-torch` extra. No key: the model runs on-device (see [modules/kyutai.md](modules/kyutai.md) for the module fields). A complete file ships as `examples/config/kyutai.json`, with `"vad": null`: the model is local and free and has its own end-of-turn head, so the gate is rarely worth it. Do not confuse `module.vad` (that head) with `engine.vad` (the gate).

```json
{
  "server": {
    "host": "0.0.0.0",
    "port": 8000
  },
  "engine": {
    "auto_start": true,
    "audio": { "device": null, "sample_rate": 24000 },
    "module": {
      "type": "kyutai",
      "backend": "auto",
      "hf_repo": "kyutai/stt-1b-en_fr-candle",
      "vad": true
    }
  }
}
```

**`engine.audio.sample_rate` must be `24000`.** The module declares a strict 24 kHz contract and the default is `16000`, so a Kyutai config that omits it fails fast at engine construction with the reconciliation error (see [Validation](#validation)) — unless `on_unsupported_format` is `"fallback"`, which resolves to the module's 24 kHz default with a warning.

## Example: test config (fake module)

For tests only — a keyless, deterministic scripted module (see [modules/fake.md](modules/fake.md)):

```json
{
  "engine": {
    "audio": { "audio_file": "tests-e2e/fixtures/sample_44100_theskyisblue.mp3", "sample_rate": 44100 },
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
  - The specified `engine.module.type` is unknown
  - The specified module's optional dependencies (its extra) are not installed — checked at engine construction, not at config parsing, with an `ImportError` naming the extra to install
  - Module-specific required fields (e.g. `api_key` / `api_key_env`) are missing
  - `engine.listen_default_segmentation_mode` is not one of `utterance` / `trigger_word` / `timeout`
  - `engine.dictation_default_segmentation_mode` is not one of `utterance` / `trigger_word` / `timeout`
  - `engine.auto_start_dictation` is `true` while `engine.auto_start` is `false`
  - `engine.audio.encoding` is not one of `linear16` / `mulaw`
  - `engine.audio.on_unsupported_format` is not one of `error` / `fallback`
  - `engine.vad` is present and not an object, or its `type` is missing or not a non-empty string
  - `engine.vad.threshold` is not a number strictly between 0 and 1, or one of `min_speech_duration_ms` (≥ 0), `min_silence_duration_ms` (> 0), `speech_pad_ms` (≥ 0) is not an integer within its bound — each error names `engine.vad.<field>`
  - The specified `engine.vad.type` is unknown, its extra is not installed, or its own fields are invalid — checked at engine construction, like `engine.module.type`
  - `engine.vad` is set with an ASR module that does not support VAD gating, or is absent with a module that requires it — checked at engine construction (see [asr-module-interface.md](asr-module-interface.md) "VAD gating")
- The configured audio format is reconciled against the selected module's declared support at engine construction (startup). Under the default `on_unsupported_format="error"`, an unsupported `sample_rate` / `channels` / `encoding` raises a clear `ValueError` listing the module's supported values; under `"fallback"` it logs a warning and uses the module's default for that dimension.
- The specified audio device is verified when audio capture first starts, and an unknown device raises a clear error there — at startup when `engine.auto_start` is `true`, or on the first `start`/`listen` call when it is `false`.
