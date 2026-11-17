# Configuration

`ASREngine` takes one `ASREngineConfig`. This page covers how to build it and what the engine block contains. Each module's own fields are on [its page](modules/index.md); the `vad` block is in [vad.md](vad.md).

## Creating the configuration

Most applications build the configuration from the ASR part of their own configuration file:

```python
from asr_engine import ASREngine, ASREngineConfig

config = ASREngineConfig.from_dict(application_config["asr"])
engine = ASREngine(config)
```

This fits an application whose file also holds TTS or other settings. The dictionary given to `from_dict()` must have the [engine block shape](#the-engine-block).

### Loading from other sources

Use the constructor that matches where the configuration comes from:

```python
from asr_engine import ASREngineConfig

# Already-parsed dictionary
config = ASREngineConfig.from_dict(engine_data)

# JSON string containing an engine block
config = ASREngineConfig.from_json(engine_json)

# JSON file whose root is an engine block
config = ASREngineConfig.from_json_file("asr.json")
```

All three expect an engine block, without the MCP `server` wrapper. A complete `{server, engine}` file is read by `MCPServerConfig` ([agents-and-mcp.md](agents-and-mcp.md#load-the-server-configuration)).

### Building it in Python

Configuration written directly in Python can use the nested dataclasses:

```python
from asr_engine import ASREngineConfig, AudioSourceConfig, ModuleConfig

config = ASREngineConfig(
    audio_source=AudioSourceConfig(type="input-device"),
    module=ModuleConfig(
        type="deepgram-v1",
        extra={"api_key_env": "DEEPGRAM_API_KEY"},
    ),
)
```

Direct dataclass construction expects trusted values. The `from_*` constructors normalize and validate configuration that comes from outside: a value of the wrong type (`"false"` for a boolean, a negative timeout) or a field the block does not define (a misspelling) raises a `ValueError` naming the field, such as `engine.segmentation.end_of_speech_timeout_s`. Only the `module` and `vad` blocks take other fields, which the module validates itself.

## The engine block

A complete engine block looks like this. Only `module` is required:

```json
{
  "auto_start": true,
  "auto_start_dictation": false,
  "listen_default_segmentation_mode": "trigger_word",
  "dictation_default_segmentation_mode": "trigger_word",
  "segmentation": {
    "trigger_words": ["send", "submit"],
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
  "vad": null,
  "module": {
    "type": "deepgram-v1",
    "api_key_env": "DEEPGRAM_API_KEY",
    "model": "nova-3",
    "language": "multi"
  }
}
```

| Block or field | Purpose | Details |
|---|---|---|
| `module` | Selects an ASR module by its `type` and carries that module's own settings. Required | [modules/](modules/index.md) |
| `audio_source` | Where the audio comes from: an input device, a file, or a source your code passes in. Its `type` defaults to `"input-device"` | [audio-input.md](audio-input.md) |
| `vad` | Optional speech gate in front of the module. Absent or `null` by default | [vad.md](vad.md) |
| `segmentation` | The trigger words and silence timeouts used by listen and dictation | [segmentation.md](segmentation.md) |
| `sound_feedback` | The start and stop cues played by `listen()`, and the device they play on | [the sound-feedback spec](../specs/sound-feedback.md) |
| `listen_default_segmentation_mode` | Mode used by `listen()` when none is given. Defaults to `trigger_word` | [segmentation.md](segmentation.md) |
| `dictation_default_segmentation_mode` | Mode used by a dictation session when none is given. Defaults to `trigger_word` | [segmentation.md](segmentation.md) |
| `auto_start` | Hosting policy: the MCP server starts the engine at startup. Defaults to `true` | [agents-and-mcp.md](agents-and-mcp.md) |
| `auto_start_dictation` | Hosting policy: the MCP server enters a persistent dictation after auto-start. Requires `auto_start: true` | [agents-and-mcp.md](agents-and-mcp.md) |

`auto_start` and `auto_start_dictation` are instructions for an application that hosts the engine. The bundled MCP server applies them at startup; an `ASREngine` you construct yourself does not act on them.

Module types are written with hyphens (`deepgram-v1`). The older underscore spellings (`deepgram_v1`, `mistral_realtime`, …) still work and log a deprecation warning naming the new type.

For every field, default and validation rule, see the [configuration specification](../specs/configuration.md).

## Credentials

A hosted module takes either a literal API key or the name of an environment variable holding it:

```json
{
  "type": "deepgram-v1",
  "api_key_env": "DEEPGRAM_API_KEY"
}
```

`api_key_env` keeps secrets out of configuration files. If both `api_key` and `api_key_env` are present, the literal `api_key` wins. Credentials are resolved when the engine constructs the module, not when `ASREngineConfig.from_dict()` parses the configuration, so a missing key fails at `ASREngine(config)`.

## Logging

Logging is the application's concern. Importing and constructing the engine configures no handler and no level; its records go to the `asr_engine` logger and its children. The `asr-engine-mcp` command sets the level from its `--log-level` flag, not from the configuration file.
