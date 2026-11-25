# Documentation

The documentation of `asr-engine`, by what you want to do. The [README](../README.md) is the landing page: what the engine is, how to install it, and a first quick start.

| I want to… | Read |
|---|---|
| transcribe speech from Python | the README's [quick start](../README.md#quick-start), then [running-the-engine.md](running-the-engine.md): the callbacks, utterances and segments, `start`, `listen` and dictation |
| pick an ASR module | [choosing-a-module.md](choosing-a-module.md): hosted or local, streaming or batch, languages, cost |
| keep the audio on my machine | [local-speech-recognition.md](local-speech-recognition.md): in process, a local batch server or a local streaming server; each solution, where it runs and what it is best used for |
| set up one module | its page under [modules/](modules/index.md): install, a minimal config, every field, troubleshooting |
| write or load a configuration | [configuration.md](configuration.md): the engine block, loading it from a dict or a JSON file, credentials |
| let a person set the engine up from my application (a form, a check of what is missing) | [setup.md](setup.md): the config's schema, its problems, the extras, keys and models it needs, a light real check |
| decide when a segment ends (a trigger word, a silence) | [segmentation.md](segmentation.md): the three modes and their settings |
| read a file, or feed audio my application already has | [audio-input.md](audio-input.md): the microphone, file input, a custom `AudioSource`, the audio format |
| stop paying for silence, or run a batch model | [vad.md](vad.md): the optional speech gate and the setting that suits each module |
| give an agent a `listen` tool, or run the MCP server | [agents-and-mcp.md](agents-and-mcp.md) |
| measure how long the speaker waited for a transcript, or trace an exchange | [segment-timing.md](segment-timing.md): the `timing` of a final segment and its OpenTelemetry span |
| test my application without a microphone or an API key | [testing-your-application.md](testing-your-application.md): the `fake` module and the scriptable audio source |
| find out why the engine does not start or nothing is transcribed | [troubleshooting.md](troubleshooting.md) |

Beyond the user documentation:

- [specs/](../specs/_index.md) is the normative design: every guarantee, with its rationale, one spec per concept and per module.
- [examples/](../examples/README.md) has three runnable applications and, under [examples/configs/](../examples/configs/), one complete server config per module.
- [AGENTS.md](../AGENTS.md) is the operating manual for working in the repository.
