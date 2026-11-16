# asr-engine

A real-time speech-recognition engine for Python. Choose an ASR module, construct an `ASREngine`, and receive what is said as it is said, through async callbacks or one `await engine.listen()`.

```text
AudioSource ──▶ ASREngine ──▶ [VAD gate] ──▶ ASRModule ──▶ cloud API or local model
                  │           optional: forwards only speech
                  │
                  ├──▶ SpeechUtterance callbacks   (each result of the backend)
                  └──▶ SpeechSegment callbacks     (utterances grouped by the engine)
```

The engine owns audio capture, the audio format, segmentation and the lifecycle. Speech recognition comes from a module you install as an extra: the Deepgram, Mistral Voxtral or OpenAI cloud APIs, any server compatible with OpenAI's transcription protocols, or a local model (Kyutai, or any mlx-audio model on Apple Silicon). There is no default provider. A built-in `fake` module stands in for a backend in tests and demos.

An optional [VAD gate](docs/vad.md) (voice activity detection) sits in front of the module and forwards only speech, so a hosted backend is not billed for silence and a local model does not compute on it. Batch models, which transcribe a whole utterance at once, need it to know where an utterance ends.

Agent-friendly tools are included as an optional interface over the same engine, and an MCP server is available through the `mcp` extra. You need neither to use `asr-engine` in a Python application.

**Start here:** the [quick start](#quick-start) below, then [docs/index.md](docs/index.md) for the documentation by task.

## Installation

This project requires Python 3.11+ on Linux, macOS or Windows, and uses [`uv`](https://docs.astral.sh/uv/) for dependency and environment management.

```bash
git clone https://github.com/funwithagents/asr-engine.git
cd asr-engine
uv sync --no-default-groups
```

`--no-default-groups` is what keeps that install minimal: a bare `uv sync` also syncs the `dev` and `demo` groups, which builds the full contributor environment (see [How it is developed](#how-it-is-developed)).

Live capture uses `sounddevice`, which requires PortAudio. The macOS and Windows wheels bundle it; on Debian or Ubuntu:

```bash
sudo apt-get install libportaudio2
```

The base installation contains the engine, the tools and the `fake` module, but no speech provider and no server. Install the provider you want as an extra, and add the `mcp` extra to run the MCP server:

```bash
uv sync --no-default-groups --extra deepgram               # one provider (every extra: docs/modules/index.md)
uv sync --no-default-groups --extra deepgram --extra mcp   # a provider and the MCP server
uv sync --no-default-groups --extra deepgram --extra silero   # a provider and the optional VAD gate
uv sync --no-default-groups --extra all                    # every extra that installs together

# For an installed package:
pip install 'asr-engine[deepgram]'
pip install 'asr-engine[mcp,deepgram]'
pip install 'asr-engine[all]'
```

Selecting a module whose extra is not installed fails at engine construction with an `ImportError` naming the extra to install.

## Quick start

This first program needs no extra, no API key and no microphone. It uses the `fake` module, which replays a scripted transcript, to show the API end to end:

```python
import asyncio

from asr_engine import ASREngine, ASREngineConfig, ScriptableAudioSource, SpeechSegment


async def main() -> None:
    config = ASREngineConfig.from_dict(
        {
            "module": {
                "type": "fake",
                "utterances": [
                    {"text": "hello from the engine", "start_s": 0.5, "end_s": 1.5}
                ],
            }
        }
    )
    heard = asyncio.Event()

    async def on_segment(segment: SpeechSegment) -> None:
        print(segment.transcript, "(final)" if segment.is_final else "")
        if segment.is_final:
            heard.set()

    engine = ASREngine(
        config,
        on_speech_segment=on_segment,
        audio_source=ScriptableAudioSource(),  # silence, standing in for a microphone
    )

    await engine.start()
    await heard.wait()
    await engine.stop()


asyncio.run(main())
```

It prints the transcript growing word by word, then the final segment. `fake` recognizes nothing: it is a test double, there to show the API and to [test your own code](docs/testing-your-application.md).

### With real speech

To transcribe your microphone, change the `module` block and drop `audio_source`. The rest of the program stays the same:

```python
config = ASREngineConfig.from_dict(
    {"module": {"type": "deepgram-v1", "api_key_env": "DEEPGRAM_API_KEY"}}
)
engine = ASREngine(config, on_speech_segment=on_segment)  # the default microphone
```

- **Hosted:** the example above uses Deepgram (`uv sync --no-default-groups --extra deepgram`, and `DEEPGRAM_API_KEY` in the environment). Mistral and OpenAI work the same way with their own block.
- **Local:** on an Apple Silicon Mac, `{"module": {"type": "mlx-audio"}, "vad": {"type": "silero"}}` runs a Parakeet model on the machine, with no key (`--extra mlx-audio --extra silero`).

[Choosing a module](docs/choosing-a-module.md) compares them.

## How the engine works

- **`ASREngine`** owns the audio source, the selected module and segmentation. Constructing one starts nothing; the caller owns its lifecycle.
- An **`ASRModule`** connects the engine to one backend and turns its results into `SpeechUtterance` values, interim and final. Exactly one module is selected when the engine is constructed.
- The engine groups utterances into **`SpeechSegment`** values according to a segmentation mode: one segment per utterance, until a trigger word, or until a silence. Segmentation is the engine's, so it behaves the same with every module.
- An optional **VAD gate** in front of the module forwards only speech, and tells the module when the speaker has stopped. It cuts the cost of a hosted backend and is required by batch models. [The VAD page](docs/vad.md) has the setting that suits each module.

An engine runs in three ways:

| Call | What it does |
|---|---|
| `await engine.start()` … `await engine.stop()` | Continuous recognition; results arrive through the callbacks |
| `segment = await engine.listen()` | Starts, waits for one segment to close, stops, and returns it |
| `await engine.start_dictation()` | On a running engine, aggregates speech into longer segments for a while |

[Running the engine](docs/running-the-engine.md) has the details, and [segmentation](docs/segmentation.md) the modes.

## Modules

A module is a swappable recognition backend, selected by the `type` of the `module` block. Hosted modules call a cloud API or a compatible server; local ones run a model in process. There is no default.

| Module | Kind | Extra | Backend | Credentials |
|---|---|---|---|---|
| [`deepgram-v1`](docs/modules/deepgram.md) | hosted | `deepgram` | Deepgram Listen v1: general and multilingual | API key |
| [`deepgram-v2`](docs/modules/deepgram.md) | hosted | `deepgram` | Deepgram Flux: English, with turn detection | API key |
| [`mistral-realtime`](docs/modules/mistral.md) | hosted | `mistral` | Mistral Voxtral realtime: 45 languages | API key |
| [`openai-realtime-compatible`](docs/modules/openai-realtime-compatible.md) | hosted | `openai-compatible` | OpenAI Realtime transcription, or a compatible server | API key |
| [`openai-transcriptions-compatible`](docs/modules/openai-transcriptions-compatible.md) | hosted | `openai-compatible` | OpenAI's `/v1/audio/transcriptions`, or a compatible server (Groq, whisper.cpp, vLLM, …); needs the VAD gate | API key |
| [`kyutai`](docs/modules/kyutai.md) | local | `kyutai-mlx` or `kyutai-torch` | Kyutai streaming STT: English and French | None |
| [`mlx-audio`](docs/modules/mlx-audio.md) | local | `mlx-audio` | Any mlx-audio model on Apple Silicon (Parakeet, Qwen3-ASR, Whisper, …); needs the VAD gate | None |
| [`fake`](docs/modules/fake.md) | test double | Base installation | A scripted transcript, no recognition | None |

Each module's page has its install line, every config field and its troubleshooting. [docs/modules/index.md](docs/modules/index.md) compares them and lists every extra.

## Documentation

Start at [docs/index.md](docs/index.md), the documentation by task. The pages used most:

| | |
|---|---|
| [docs/running-the-engine.md](docs/running-the-engine.md) | The callbacks, utterances and segments, `start`, `listen`, dictation |
| [docs/configuration.md](docs/configuration.md) | The engine block, the loaders, credentials |
| [docs/modules/index.md](docs/modules/index.md) | Every module, with one page each |
| [docs/choosing-a-module.md](docs/choosing-a-module.md) | Hosted or local, streaming or batch, languages, cost |
| [docs/segmentation.md](docs/segmentation.md) | Closing a segment on each utterance, a trigger word or a silence |
| [docs/audio-input.md](docs/audio-input.md) | The microphone, file input, your own audio source, the audio format |
| [docs/vad.md](docs/vad.md) | The optional speech gate, and the setting that suits each module |
| [docs/agents-and-mcp.md](docs/agents-and-mcp.md) | `AsrTools` for an agent, and the MCP server with its two resources |
| [docs/testing-your-application.md](docs/testing-your-application.md) | Deterministic tests with the `fake` module |
| [docs/troubleshooting.md](docs/troubleshooting.md) | A missing extra, an unsupported sample rate, a missing VAD, no transcription |

## Examples

The runnable consumers under [examples/](examples/README.md) are not part of the package:

| Example | Integration | Demonstrates |
|---|---|---|
| [gradio_demo](examples/gradio_demo/) | Direct engine import | A browser UI over an in-process engine: devices, modules, start, listen, dictation, live utterances and segments |
| [mcp_client](examples/mcp_client/) | MCP resources | Subscribing to the server's rolling transcription resources |
| [asr_to_terminal](examples/asr_to_terminal/) | MCP resources | Typing what is said into the focused terminal |

[examples/config/](examples/config/) holds one complete config per module, in the MCP server's format (a `server` block and an `engine` block). With the `mcp` extra and the module's extra installed, `uv run asr-engine-mcp --config examples/config/deepgram-v1.json` starts a server as is; the `engine` block is what a library config takes. They hold no secrets: a hosted module reads its key from the environment variable named in `api_key_env`.

## Feedback and contributions

Bug reports, questions, reports from a platform or a provider account the author does not test on, and requests for a backend are welcome at [the issue tracker](https://github.com/funwithagents/asr-engine/issues). The project is maintained by one person and is not taking external pull requests for now; [CONTRIBUTING.md](CONTRIBUTING.md) says why and how to help anyway.

## How it is developed

`asr-engine` is a spec-driven project: every feature is designed before it is coded, pinned by tests at two levels, and checked by CI on every change.

- **Specs first.** Each concept has one spec under [specs/](specs/_index.md), the normative design with its rationale and open questions; each module has its own under [specs/modules/](specs/modules/). An implementation plan under [plans/](plans/_index.md) turns a settled spec into code. Each spec names the code and tests it governs, and tests keep that map, the documentation's links, its module tables and its examples honest.
- **Two test tiers.** `tests/` is fast and deterministic: no network, no credentials, no audio hardware. `tests-e2e/` runs the pipeline in real time and calls the real providers, skipping one whose key or weights are missing ([specs/testing.md](specs/testing.md), [specs/e2e-testing.md](specs/e2e-testing.md)).
- **CI on every change.** The static gate, the fast tier on Linux (x86_64 and arm64), macOS and Windows, and the live tier on Linux ([specs/ci.md](specs/ci.md)).
- **[AGENTS.md](AGENTS.md) is the operating manual** for anyone, or any coding agent, working in the repository: the project map, the commands, how the specs and statuses are kept in step with the code.

The same checks, locally:

```bash
uv sync                      # the dev and demo groups: every extra but the Kyutai ones, and Gradio
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest tests/         # the fast tier
uv run pytest tests-e2e/     # the live tier: real providers where a key is set
```

## License

Apache-2.0, see [LICENSE](LICENSE) and [NOTICE](NOTICE).
