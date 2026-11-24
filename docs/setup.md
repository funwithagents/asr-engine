# Setting the engine up from an application

`asr_engine.setup` tells an application everything it needs to let a person choose and configure speech recognition, before any engine is built: what the config looks like, what must be installed, which key must be set, what must be downloaded, and whether it works. Every function takes or returns plain JSON-compatible data, so it can sit behind a form, an HTTP endpoint or a command line.

All of them work on the **engine block**, the object `ASREngineConfig.from_dict` takes ([configuration.md](configuration.md)).

```python
from asr_engine import setup

config = {"module": {"type": "deepgram-v1"}, "vad": {"type": "silero"}}

setup.check_config(config)  # [] when the engine accepts this config
setup.required_extras(config)  # ["asr-engine[deepgram]", "asr-engine[silero]"]
# The extras, the key, the model files: each "ready", "missing" or "unknown".
setup.check_setup(config)
setup.prepare(config)  # downloads the missing files (here, the Silero model)
# A light real check: {"ok": True, "message": ..., "duration_s": ...}.
setup.probe(config)
```

## The functions

| Function | What it gives | Needs |
|---|---|---|
| `config_schema()` | The engine block as a JSON Schema: one branch per module and per VAD, each field with its type, default, bounds and a description | Nothing |
| `required_extras(config)` | The extras the selected module and VAD need, as `asr-engine[<extra>]`, or `[]` | Nothing |
| `check_config(config)` | What is wrong in the config itself: every reason the engine would refuse it, each with a JSON Pointer to the field and the message the engine raises | Nothing |
| `check_setup(config)` | What this machine is missing to run the config: the module's extra, the environment variable holding its key, its model files, then the VAD's extra and model file | Nothing |
| `prepare(config, on_progress)` | Downloads the missing files; `on_progress` receives `{"id", "fraction", "message"}` | The extras, the network |
| `options("input-devices", config)` | The machine's input devices, after the system default, each with the `audio_source.device` it sets | Nothing |
| `probe(config)` | A light real check: a hosted module makes one free, authenticated listing call with the key (and finds the configured model there); a local module loads its model and releases it, as does the VAD. No microphone, no audio, no transcription. Never raises | Everything the config needs |

"Nothing" means the base install, with no network: `config_schema`, `required_extras` and `check_config` look at the config only, so an application can show and check a config for a module that is not installed yet. `check_setup` also tries the module's imports (a missing extra is one of its answers) and reads the environment.

The usual order is `check_config`, then `check_setup`; install what it says is missing and set the key; `prepare`; `check_setup` again; `probe` when you want to know it works.

## `check_config` and `check_setup`

`check_config` looks at the config: types, ranges, required fields, a module that needs the VAD gate (a batch module) or cannot take it, an audio format the module does not take, hosted OpenAI without a key variable. Its answer is the same on every machine. `check_setup` looks at this machine: are the extras installed, is the key's environment variable set, are the model files there. A config both accept can still fail when the engine runs, for what only the provider knows: a model name it does not serve, a key it rejects. `probe` is the way to find those out first.

Deepgram is the exception for the model: its model list answers without a key and leaves Flux out, so its probe proves the key on the account's project list and leaves the model to the connection.

## Relative paths

`check_setup`, `prepare`, `options` and `probe` take `base_dir`, the folder the config's relative paths are relative to (an audio file, a local VAD model, a local `mlx-audio` model written as `./…`). Pass the folder your config lives in, and give the engine the same one (`ASREngineConfig.from_dict(block, base_dir=folder)`), so the checks and the engine look at the same file. Without it, both use the working directory.

## Keys

A key never sits in the config. `check_setup` lists the variable each module reads, `api_key_env` or its default (`DEEPGRAM_API_KEY`, `MISTRAL_API_KEY`), as `ready` once it is set. The environment read is the process's own: an application keeping keys in a file loads them first. `probe` also takes `secrets={"VARIABLE": key}`, looked up before the environment, as `ASREngine` does ([configuration.md](configuration.md#credentials)).

## The schema's annotations

Beyond standard JSON Schema keywords, a field may carry:

- `x-advanced`: a tuning field most people leave alone;
- `x-secret-env`: the field holds the *name* of an environment variable that contains a key;
- `x-options`: the name to pass to `options` for a list of choices (`input-devices`, on the input device).

A few fields are richer than a form field: Mistral's and OpenAI's `turn_detection` also takes an object of the server's settings, and `generate_kwargs` or `extra_fields` are free-form. The schema describes their usual form; `check_config` checks the rest.

## Local models

`kyutai` and `mlx-audio` have their model in the Hugging Face cache, reported `ready` or `missing`; `prepare` downloads it with the same call the module makes. A local path (`mlx-audio`'s `model`, the VAD's `model_path`) is `ready` or `missing` and never downloaded. The Silero VAD's model is a small pinned file in the engine's cache directory ([vad.md](vad.md)).
