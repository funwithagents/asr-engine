# Troubleshooting

Most configuration mistakes fail when the engine is constructed, with a message naming the field. This page lists the common ones by symptom. Errors that belong to one backend are on that module's page.

## A module requires an optional dependency

```text
Deepgram Nova module requires the optional dependency 'deepgram' which is not installed.
```

The module's extra is not installed in the environment running the engine. Install it: `uv sync --no-default-groups --extra deepgram` from a checkout, or `pip install 'asr-engine[deepgram]'`. The same message with `Silero VAD` asks for the `silero` extra. An application can find out before building the engine: `asr_engine.setup.check_setup` lists each missing extra ([setup.md](setup.md)). Every extra is listed in [modules/index.md](modules/index.md#installing-a-module).

## `asr-engine-mcp` exits with an install hint

The MCP server needs the `mcp` extra: `uv sync --no-default-groups --extra <module> --extra mcp`, or `pip install 'asr-engine[mcp,<module>]'`.

## Unknown ASR type

`Unknown ASR type '...'. Available: ...` means `module.type` is not a registered module. Types are written with hyphens (`deepgram-v1`); the message lists the valid ones.

## The audio source block is refused

- `engine.audio is now engine.audio_source`: the block was renamed and has a `type`. Write `"audio_source": {"type": "input-device", "device": ...}`, or remove the block to read the default input device.
- `... does not apply to type '...'`: the block holds a field of another type, such as `audio_file` with `"input-device"`.
- `engine.audio_source.type is 'custom', but no source was given`: a `custom` source is passed in code, as `ASREngine(config, audio_source=...)`. The MCP server cannot take one; use `"input-device"` or `"file"` there.
- `An audio source was passed to ASREngine, but engine.audio_source.type is '...'`: set the type to `"custom"`. A config with no `audio_source` block reads the input device, so a source passed with it would be ignored; the engine refuses instead.

## The module does not support the sample rate

```text
... does not support engine.module.audio_format.sample_rate=16000. Supported: [24000]. ...
```

`module.audio_format` asks for a rate, channel count or encoding the module cannot take. The message lists the supported ones. Remove the field to use the module's default, or pick a listed value. A source at another rate needs no setting: the engine converts it ([audio-input.md](audio-input.md#audio-format-and-conversion)).

## The module needs a VAD

```text
ASR module 'mlx-audio' needs a VAD to know when an utterance ends: add an engine.vad block
```

`mlx-audio` and `openai-transcriptions-compatible` are batch modules. Add `"vad": {"type": "silero"}` to the engine block and install the `silero` extra ([vad.md](vad.md)).

## An API key is missing

- `no key under '...': set the environment variable, or pass it in secrets`: the variable named by `api_key_env` (or the module's default, such as `DEEPGRAM_API_KEY`) is not set in the process running the engine. A variable exported in one terminal, or defined only in a shell profile, is not visible to a service started elsewhere. The error is a `MissingSecretError`, whose `variable` names it.
- `does not take 'api_key'`: a key written in the config is refused. Keep it in an environment variable named by `api_key_env`, or pass it in code.
- `api.openai.com needs a key`: an OpenAI-protocol module pointed at hosted OpenAI has no default variable; set `"api_key_env": "OPENAI_API_KEY"`.

## The audio device is not found

`Audio device '...' not found. Available devices: ...` lists the input devices the system reports. Use one of those names, or `null` for the default input. On Linux, an import error mentioning PortAudio means the system library is missing: `sudo apt-get install libportaudio2`.

## A file input is rejected

- `audio_format does not apply to type 'file'`: a file is read at its own sample rate and channel count, and converted for the module when needed. Remove the field.
- `Audio file not found` or `not a readable audio file`: the path in `audio_source.audio_file` is wrong, or the file is not something `libsndfile` decodes.

## The engine runs but nothing is transcribed

- Check `engine.status()`. `connected: false` means the backend is unreachable or rejected the key; the log names the cause at each retry. A local model reports `connected: true` only once its weights are downloaded and loaded, which takes a while the first time.
- Check that the right input device is selected and, on macOS, that the terminal or application has microphone permission.
- With a VAD gate, `engine.vad_state` tells whether speech is reaching the module. If it never opens, lower `threshold`; if it opens on noise, raise it.
- Raise the log level (`--log-level DEBUG` on the MCP server) to see the module's messages.

## The last words of a file are missing

A streaming backend finalizes an utterance on the silence that follows it, and a file ends abruptly. Set `audio_source.trailing_silence_s` to a couple of seconds.

## `listen` or dictation is refused

- `ASR is already running. Stop it before calling listen.`: `listen()` starts and stops the engine itself, so it needs a stopped engine. On a running engine, use a dictation session.
- `ASR is not running. Start it before calling start_dictation.`: a dictation runs on top of a started engine.
- `ASR is already running.`: `start()` was called on a running engine. With the MCP server, `auto_start: true` already started it.
- `A listen is already in progress.`: one `listen()` runs at a time.

See [running-the-engine.md](running-the-engine.md#the-three-ways-compared).

## A segment never closes

In `trigger_word` mode a segment closes only when a final utterance contains one of `segmentation.trigger_words`. Check the list, and that the backend transcribes the word as you wrote it; a vocabulary hint helps ([segmentation.md](segmentation.md#with-a-vad-gate)).

## `kyutai` and `mlx-audio` do not install together

Their dependencies conflict. Use one environment for each ([modules/index.md](modules/index.md#installing-a-module)).

## A backend's own errors

Key, model and download errors specific to one backend are at the end of its page: [`deepgram-v1` / `deepgram-v2`](modules/deepgram.md#troubleshooting), [`mistral-realtime`](modules/mistral.md#troubleshooting), [`openai-realtime-compatible`](modules/openai-realtime-compatible.md#troubleshooting), [`openai-transcriptions-compatible`](modules/openai-transcriptions-compatible.md#troubleshooting), [`kyutai`](modules/kyutai.md#troubleshooting), [`mlx-audio`](modules/mlx-audio.md#troubleshooting).
