# ASR modules

A module is a swappable speech-recognition backend. Its `type` selects an entry of the module registry, and every other field of the `module` block belongs to that backend. A module owns authentication, the connection or the loaded model, reconnection, and the conversion of the backend's results into `SpeechUtterance` values. Audio capture, segmentation, the tools and the MCP server stay the same whichever module is selected.

There is no default: `module.type` must always name the module you chose.

| Module | Kind | Extra | Backend | Credentials | VAD gate | Default model |
|---|---|---|---|---|---|---|
| [`deepgram-v1`](deepgram.md) | hosted | `deepgram` | Deepgram Listen v1 | API key | optional | `nova-3` |
| [`deepgram-v2`](deepgram.md) | hosted | `deepgram` | Deepgram Listen v2 (Flux) | API key | optional | `flux-general-en` |
| [`mistral-realtime`](mistral.md) | hosted | `mistral` | Mistral Voxtral 3 realtime | API key | optional | `voxtral-mini-transcribe-realtime-3` |
| [`openai-realtime-compatible`](openai-realtime-compatible.md) | hosted | `openai-compatible` | OpenAI Realtime transcription, or any server speaking its protocol | API key (optional on other servers) | optional | `gpt-4o-transcribe` |
| [`openai-transcriptions-compatible`](openai-transcriptions-compatible.md) | hosted | `openai-compatible` | OpenAI's `/v1/audio/transcriptions`, or any server implementing it | API key (optional on other servers) | required | `gpt-4o-transcribe` |
| [`kyutai`](kyutai.md) | local | `kyutai-mlx` or `kyutai-torch` | Kyutai streaming STT, on-device | None | optional | `kyutai/stt-1b-en_fr-candle` |
| [`mlx-audio`](mlx-audio.md) | local | `mlx-audio` | Any mlx-audio STT model, on-device (Apple Silicon) | None | required | `mlx-community/parakeet-tdt-0.6b-v3` |
| [`fake`](fake.md) | test double | Base installation | Replays a scripted transcript; no recognition | None | optional | — |

- **Kind.** A *hosted* module calls a cloud API, or a server you run that speaks the same protocol: it needs a network and usually a key. A *local* module runs a model inside the process: no network once its weights are downloaded, no key, no per-minute cost. `fake` is a *test double* for tests and demos, never a substitute for a real module.
- **VAD gate.** *Required* means the module is a batch model that needs the [VAD gate](../vad.md) to know when an utterance ends; *optional* means it works with or without it.

Each module's page has its install line, a minimal config, every field and its troubleshooting. [choosing-a-module.md](../choosing-a-module.md) helps narrow the list down.

## Installing a module

Each module ships in an extra. The base installation holds the engine, `AsrTools` and `fake`, and no provider. Selecting a module whose extra is not installed fails at engine construction with an `ImportError` naming the extra.

From a source checkout (`--no-default-groups` keeps the install minimal; a bare `uv sync` builds the full contributor environment):

```bash
uv sync --no-default-groups --extra deepgram               # deepgram-v1, deepgram-v2
uv sync --no-default-groups --extra mistral                # mistral-realtime
uv sync --no-default-groups --extra openai-compatible      # openai-realtime-compatible, openai-transcriptions-compatible
uv sync --no-default-groups --extra mlx-audio --extra silero   # mlx-audio (Apple Silicon), with the VAD gate it needs
uv sync --no-default-groups --extra kyutai-mlx             # kyutai on Apple Silicon (MLX)
uv sync --no-default-groups --extra kyutai-torch           # kyutai elsewhere (PyTorch)
uv sync --no-default-groups --extra all                    # every extra that installs together
```

For an installed package:

```bash
pip install 'asr-engine[deepgram]'
pip install 'asr-engine[mistral]'
pip install 'asr-engine[openai-compatible]'
pip install 'asr-engine[mlx-audio,silero]'
pip install 'asr-engine[kyutai-mlx]'      # or 'asr-engine[kyutai-torch]'
pip install 'asr-engine[all]'
```

Two more extras combine with any module:

| Extra | Adds |
|---|---|
| `silero` | The [VAD gate](../vad.md). Required by `mlx-audio` and `openai-transcriptions-compatible`, optional for the others |
| `mcp` | The [MCP server](../agents-and-mcp.md) (`asr-engine-mcp`) |

**`kyutai` and `mlx-audio` cannot share an environment.** The Kyutai packages pin older versions of `mlx`, `sounddevice` and `huggingface-hub` than mlx-audio needs. `all` therefore includes `mlx-audio` and leaves the two Kyutai extras out; install Kyutai in an environment of its own.

## Module names

Module types are written with hyphens. The older underscore spellings (`deepgram_v1`, `mistral_realtime`, `mlx_audio`, `openai_realtime_compatible`, `openai_transcriptions_compatible`) still work, and using one logs a deprecation warning naming the new type.

## Adding a module

To integrate another cloud or local backend, implement the `ASRModule` interface: declare the audio formats it supports and its defaults, emit `SpeechUtterance` values, and register it under a new key as a lazy entry naming the extra that provides its dependencies. A batch model (one that transcribes a whole utterance at once) starts from `BatchASRModule` instead. `ASREngine`, `AsrTools` and the MCP server then use it with no provider-specific change.

The contract is in the [ASR module interface specification](../../specs/asr-module-interface.md) and the [batch module specification](../../specs/batch-asr-module.md); the checklist of files to touch is the interface specification's ["Adding a module"](../../specs/asr-module-interface.md#adding-a-module-checklist).
