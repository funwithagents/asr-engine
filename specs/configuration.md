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

The file has two top-level blocks: `server` (an MCP-only concern — host/port) and
`engine` (everything the `ASREngine` itself needs). A direct importer of the
library builds an `ASREngineConfig` from the `engine` block alone and never needs
`server`.

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
    }
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

The whole `engine` block maps to one `ASREngineConfig` dataclass, the single
argument to `ASREngine(config=...)` (see [engine.md](engine.md)). It carries the
scalar engine settings plus five nested sub-blocks.

| Field | Type | Default | Description |
|---|---|---|---|
| `auto_start` | boolean | `true` | If `true`, ASR starts automatically at server startup. If `false`, the engine is initialised but not started — use the `start` or `listen` tool to begin capture. |
| `auto_start_dictation` | boolean | `false` | If `true`, the server starts a persistent dictation session (mode = `dictation_default_segmentation_mode`, never self-ending) immediately after auto-starting, so the always-on `asr://segment` stream aggregates from startup. **Requires `auto_start=true`** (validated). |
| `listen_default_segmentation_mode` | string | `"trigger_word"` | The segmentation mode `listen()` uses when its caller passes no explicit mode. One of `"utterance"`, `"trigger_word"`, or `"timeout"`. |
| `dictation_default_segmentation_mode` | string | `"trigger_word"` | The segmentation mode `start_dictation()` uses when its caller passes no explicit mode. One of `"utterance"`, `"trigger_word"`, or `"timeout"`. |
| `segmentation` | object | see below | Segmentation parameters (trigger words + timeouts) shared by the always-on stream, `listen`, **and** dictation. |

> **No always-on `segmentation_mode`.** The always-on `asr://segment` stream is
> `utterance` mode (one segment per final utterance) unless a *dictation* session
> is active. To aggregate utterances (trigger-word or timeout), either set
> `auto_start_dictation=true` so the server begins in a persistent dictation at
> startup, or have a consumer call the `start_dictation` tool / use `listen` —
> all of which switch the engine's segmentation mode and revert to `utterance`
> when they end. This is what `asr-to-terminal` relies on for continuous
> aggregation (see [asr-to-terminal.md](asr-to-terminal.md)). See
> [engine.md](engine.md).
| `sound_feedback` | object | see below | Start/stop audio cues, owned and played by the engine during `listen`. |
| `audio` | object | see below | Audio input / file-source settings. |
| `module` | object | — | ASR backend selection and its module-specific fields. **Required.** |

#### `engine.segmentation` sub-block

The single source of segmentation parameters. The always-on segmenter, `listen`,
and dictation all read these — `listen` and dictation only ever change the
*mode*, never these params (see [engine.md](engine.md)).

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

> **No `engine.logging` block.** Logging is an application-layer concern: the
> library configures nothing, and the `asr-engine-mcp` server's log level is set
> by the `--log-level` CLI flag (default `INFO`), not by config. See
> [project.md](project.md) and [mcp-server.md](mcp-server.md).

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

The three format fields (`sample_rate`/`channels`/`encoding`) form the desired
`AudioFormat`. Live capture opens its stream at that rate/channels (PortAudio
does any device conversion) and transcodes to the encoding; `mulaw` silence is
`0xFF`, not zero bytes, so silence is transcoded rather than assumed. WAV file
sources are validated against the configured rate/channels (they are **not**
resampled) and re-encoded to `mulaw` when asked.

#### `engine.module` sub-block

| Field | Type | Required | Description |
|---|---|---|---|
| `type` | string | yes | Identifies which ASR module to load (e.g. `"deepgram_v1"`). |
| *(other fields)* | any | depends | Module-specific configuration, parsed by the module. |

**Default trigger words:**
```
submit, enter, validate, send, confirm, go,
envoyer, valider, confirmer, soumettre, entree, entrée
```

**Segmentation mode behaviour summary** (applies to `listen` and dictation; the
always-on stream is fixed to `utterance`):

| Mode | Segment closes when | Timeouts |
|---|---|---|
| `utterance` | Every final utterance (one segment each) | None |
| `trigger_word` | A final utterance contains a trigger word (case-insensitive substring match) | None |
| `timeout` | Silence for `end_of_speech_timeout_s` after last event, or `initial_silence_timeout_s` with no speech at all | Both timers active |

All three modes are valid for `listen` and dictation. In `utterance` mode a
`listen` returns after exactly one final utterance, and a dictation with
`end_on_final_segment=true` ends after one — coherent, if degenerate.

## Example: Deepgram config

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
    }
  }
}
```

## Validation

- The server must fail fast at startup with a clear error message if:
  - The config file is missing or not valid JSON
  - The required field `engine.module.type` is absent
  - The specified `engine.module.type` is unknown
  - Module-specific required fields (e.g. `api_key` / `api_key_env`) are missing
  - `engine.listen_default_segmentation_mode` is not one of `utterance` / `trigger_word` / `timeout`
  - `engine.dictation_default_segmentation_mode` is not one of `utterance` / `trigger_word` / `timeout`
  - `engine.auto_start_dictation` is `true` while `engine.auto_start` is `false`
  - `engine.audio.encoding` is not one of `linear16` / `mulaw`
  - `engine.audio.on_unsupported_format` is not one of `error` / `fallback`
- The configured audio format is reconciled against the selected module's
  declared support at engine construction (startup). Under the default
  `on_unsupported_format="error"`, an unsupported `sample_rate` / `channels` /
  `encoding` raises a clear `ValueError` listing the module's supported values;
  under `"fallback"` it logs a warning and uses the module's default for that
  dimension.
- The specified audio device is verified when audio capture first starts, and an
  unknown device raises a clear error there — at startup when `engine.auto_start`
  is `true`, or on the first `start`/`listen` call when it is `false`.
