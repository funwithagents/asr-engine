# Running speech recognition locally

Nothing has to leave the machine. A model can run locally in three ways:

| Way | How it works | Module |
|---|---|---|
| **In process** | The module loads the model inside your program | [`mlx-audio`](modules/mlx-audio.md), [`kyutai`](modules/kyutai.md) |
| **A local batch server** | A server holds the model and transcribes one utterance per HTTP request (`POST /v1/audio/transcriptions`) | [`openai-transcriptions-compatible`](modules/openai-transcriptions-compatible.md) |
| **A local streaming server** | A server holds the model and receives the audio as it is captured, over a WebSocket (OpenAI's Realtime protocol) | [`openai-realtime-compatible`](modules/openai-realtime-compatible.md) |

The two server modules are protocol modules: they work with any server that implements the protocol, on the same machine or on another one. The servers below are the ones that were run against the engine, not the only ones that can work.

## The solutions

| Solution | Way | Runs on | Models | Status |
|---|---|---|---|---|
| [`mlx-audio` module](#mlx-audio-in-process) | In process | Apple Silicon | Parakeet, Whisper, Qwen3-ASR and other mlx-audio models | In the live test tier |
| [`kyutai` module](#kyutai-in-process) | In process | Apple Silicon (MLX); other platforms through PyTorch | Kyutai STT, English and French | MLX in the live test tier; PyTorch not verified |
| [whisper.cpp server](#whispercpp-server) | Batch server | macOS, Linux, Windows; Metal, CUDA, Vulkan or CPU | Whisper | Run on macOS |
| [speaches](#speaches-faster-whisper) | Batch server | Linux and macOS from a clone, or its Docker images; CUDA or CPU | Whisper through faster-whisper | Run on a macOS CPU |
| [mlx-audio server, batch](#mlx-audio-server-batch) | Batch server | Apple Silicon | The mlx-audio batch models | Run |
| [mlx-audio server, streaming](#mlx-audio-server-streaming) | Streaming server | Apple Silicon | mlx-audio models with a streaming mode, such as Voxtral realtime | Run |
| [Other compatible servers](#other-compatible-servers) | Batch or streaming | Depends on the server | Depends on the server | Not run |

"Run" means the engine transcribed the test fixture through it, on an Apple M5 Pro on 2026-11-16. A platform listed without that word is what the project behind the solution documents.

## In process, batch server or streaming server

- **In process** is one install and one config, with nothing to start or keep running. The model lives and dies with your program, and only the platforms a module supports are covered.
- **A server** is a second program to install and start. In exchange it can run on another machine (a GPU box), be shared by several programs, and bring a runtime the engine has no module for, such as whisper.cpp or faster-whisper.

Batch and streaming both show text while the person is speaking, in different ways:

| | Batch server, with `interim_interval_s` | Streaming server |
|---|---|---|
| Interims | The module re-sends the growing utterance every `interim_interval_s` and the server transcribes it again from the start | The server sends each new word as it recognizes it |
| What an interim can do | Rewrite the words before it: every interim is a fresh transcription | Extend the text before it |
| Cost | One full transcription per interim; when the server is slower than the interval, interims are skipped | One pass over the audio |
| End of utterance | The [VAD gate](vad.md), required | The server's turn detection, or the VAD gate |
| Final, as measured | About 0.5 s after the end of speech with a fast model | 1.5 to 2.5 s after the end of speech (Voxtral realtime) |
| Models | Any model: every speech model can transcribe a finished utterance | Only models built for streaming |

## What every local server has in common

- **No API key.** Leave `api_key_env` out of the module block.
- **Interims are off by default on a batch server.** `openai-transcriptions-compatible` leaves them off because a hosted API bills each one. A local server is free, so set `"interim_interval_s": 0.5`.
- **The VAD gate is required** by `openai-transcriptions-compatible` and optional with `openai-realtime-compatible` ([vad.md](vad.md)).
- **Speech before `connected` is lost.** When the engine starts, the module sends the server one request (or opens one session), and many servers load the model at that moment. Until `engine.status()["connected"]` is true, what is said is not transcribed. A model's first load takes seconds; later starts take a fraction of a second.

To check a server once it runs, `scripts/check_local_server.py` plays a fixture through the engine and prints the interims and finals. Each server below gives its command. The ports are arbitrary; each uses its own so the servers can run side by side.

## `mlx-audio`, in process

- **Runs on:** Apple Silicon.
- **Models:** any [mlx-audio](https://github.com/Blaizzy/mlx-audio) speech-to-text model, chosen by `model`: Parakeet v3 by default (25 European languages), Whisper, Qwen3-ASR and others.
- **Results:** batch. An interim every half second by re-transcribing the utterance, and the final one decode after the VAD gate closes it (a few hundredths of a second with Parakeet).
- **Best use:** a single program on an Apple Silicon machine. It gives the same models as the mlx-audio server with nothing to start.

Install, config and fields: [modules/mlx-audio.md](modules/mlx-audio.md).

## `kyutai`, in process

- **Runs on:** Apple Silicon through MLX. Other platforms through PyTorch, a backend that has not yet been verified on real hardware.
- **Models:** Kyutai's streaming speech-to-text, English and French.
- **Results:** streaming. Text arrives as the audio does, and the model detects the end of a turn by itself, so no VAD gate is needed.
- **Best use:** English or French with true streaming and nothing to start. Its extras install in an environment of their own, apart from `mlx-audio`.

Install, config and fields: [modules/kyutai.md](modules/kyutai.md).

## whisper.cpp server

- **Runs on:** macOS, Linux and Windows. [whisper.cpp](https://github.com/ggml-org/whisper.cpp) is a C++ program with no Python; it uses Metal on Apple Silicon, CUDA or Vulkan where available, and the CPU otherwise. Run here on macOS with Metal.
- **Models:** Whisper, as ggml model files. The server takes one file, chosen when it starts.
- **Module:** `openai-transcriptions-compatible`.
- **Best use:** Whisper on any platform with a small install, and Whisper on a GPU that is not NVIDIA's.

```bash
brew install whisper-cpp        # or build it from source
curl -L -O https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin
whisper-server -m ggml-large-v3-turbo.bin --port 8081 \
    --inference-path /v1/audio/transcriptions
```

`--inference-path` is what makes the server answer on OpenAI's path; its default is `/inference`.

```json
{
  "module": {
    "type": "openai-transcriptions-compatible",
    "url": "http://127.0.0.1:8081/v1/audio/transcriptions",
    "model": "whisper-1",
    "language": "en",
    "interim_interval_s": 0.5
  },
  "vad": { "type": "silero" }
}
```

```bash
uv run python scripts/check_local_server.py transcriptions \
    --url http://127.0.0.1:8081/v1/audio/transcriptions --model whisper-1
```

- The server ignores `model`; the field still has to be a non-empty string.
- Without `language` the server uses its own default, English (`-l` at launch). Set `"language"` for another one.
- Measured with `ggml-base.en.bin` (141 MB) and `ggml-large-v3-turbo.bin` (1.5 GB): an interim every half second, the final 0.5 to 0.7 s after the end of speech.

## speaches (faster-whisper)

- **Runs on:** Linux and macOS from a clone, or anywhere Docker runs from its images (`ghcr.io/speaches-ai/speaches:latest-cuda` and `latest-cpu`). [speaches](https://github.com/speaches-ai/speaches) serves [faster-whisper](https://github.com/SYSTRAN/faster-whisper), which uses an NVIDIA GPU through CUDA and the CPU otherwise; it has no Apple or AMD GPU support. Run here on a macOS CPU.
- **Models:** Whisper in faster-whisper's format, downloaded by name and chosen per request.
- **Module:** `openai-transcriptions-compatible`.
- **Best use:** Whisper on a machine with an NVIDIA GPU, which is what faster-whisper is built for. It has no Homebrew or PyPI package.

```bash
git clone https://github.com/speaches-ai/speaches.git && cd speaches
uv sync --no-dev
uv run --no-dev uvicorn --factory --host 127.0.0.1 --port 8082 speaches.main:create_app
```

A model has to be downloaded once before it can be used:

```bash
curl -X POST http://127.0.0.1:8082/v1/models/Systran/faster-whisper-small
```

```json
{
  "module": {
    "type": "openai-transcriptions-compatible",
    "url": "http://127.0.0.1:8082/v1/audio/transcriptions",
    "model": "Systran/faster-whisper-small",
    "language": "en",
    "interim_interval_s": 0.5
  },
  "vad": { "type": "silero" }
}
```

```bash
uv run python scripts/check_local_server.py transcriptions \
    --url http://127.0.0.1:8082/v1/audio/transcriptions \
    --model Systran/faster-whisper-small --language en
```

Measured on a CPU (an Apple Silicon Mac, where faster-whisper has no GPU support): 0.8 to 1 s per request with the `small` model, so the final comes about 1.5 s after the end of speech and only some interims get through. It was not measured on CUDA.

speaches also has a `/v1/realtime` endpoint. It was run with `openai-realtime-compatible`, and the batch endpoint above serves it better:

- it sends no interims, so it gives nothing the batch endpoint does not;
- it ignores the session settings the module sends (the engine [warns about it](modules/openai-realtime-compatible.md#troubleshooting)) and keeps its own turn detection, which cannot be turned off;
- with the VAD gate on, the server drops the connection when the gate ends an utterance, so `engine.vad` cannot be used with it;
- from a clone it needs `LOOPBACK_HOST_URL=http://127.0.0.1:8082` in its environment to work at all (speaches issue 559).

## mlx-audio server, batch

- **Runs on:** Apple Silicon.
- **Models:** the mlx-audio batch models, chosen per request by the `model` field and loaded on first use.
- **Module:** `openai-transcriptions-compatible`.
- **Best use:** an mlx-audio model shared by several programs, or kept loaded while your program restarts. For one program, the in-process module does the same with nothing to start.

```bash
uv run --with 'mlx-audio[stt,server]>=0.6' python -m mlx_audio.server --port 8765
```

The server needs mlx-audio's own `server` extra, which the engine's `mlx-audio` extra does not install; `--with` adds it for that command only.

```json
{
  "module": {
    "type": "openai-transcriptions-compatible",
    "url": "http://localhost:8765/v1/audio/transcriptions",
    "model": "mlx-community/parakeet-tdt-0.6b-v3",
    "interim_interval_s": 0.5
  },
  "vad": { "type": "silero" }
}
```

```bash
uv run python scripts/check_local_server.py transcriptions \
    --url http://localhost:8765/v1/audio/transcriptions \
    --model mlx-community/parakeet-tdt-0.6b-v3
```

Measured: an interim every half second and the final about 0.5 s after the end of speech, with Parakeet v3 and with `mlx-community/whisper-large-v3-turbo-asr-4bit` (add `"language": "en"`).

## mlx-audio server, streaming

- **Runs on:** Apple Silicon.
- **Models:** the mlx-audio models that support streaming sessions, such as Voxtral realtime. The model is named in the URL.
- **Module:** `openai-realtime-compatible`. It is the server the streaming way was run with; any other server that speaks the Realtime protocol is used the same way, with its own `url`.
- **Best use:** a streaming model run locally, when interims should come word by word and not by re-transcription.

```bash
uv run --with 'mlx-audio[stt,server]>=0.6' python -m mlx_audio.server --port 8765 --realtime
```

```json
{
  "module": {
    "type": "openai-realtime-compatible",
    "url": "ws://localhost:8765/v1/realtime?model=mlx-community/Voxtral-Mini-4B-Realtime-2602-4bit",
    "model": "mlx-community/Voxtral-Mini-4B-Realtime-2602-4bit"
  }
}
```

```bash
uv run python scripts/check_local_server.py realtime \
    --url 'ws://localhost:8765/v1/realtime?model=mlx-community/Voxtral-Mini-4B-Realtime-2602-4bit' \
    --model mlx-community/Voxtral-Mini-4B-Realtime-2602-4bit
```

Measured with Voxtral Mini 4B Realtime (a 3.2 GB download): one interim per word, about a second behind the speaker, and the final 1.5 to 2.5 s after the end of speech. The server's own turn detection works, and so does the VAD gate with `"turn_detection": false` (add `--turn-detection false --vad` to the check).

## Other compatible servers

Not run against the engine. Each documents one of the two protocols, so the matching module should reach it with its `url` and `model`:

- **vLLM** serves batch models (Whisper and others) on `/v1/audio/transcriptions`, on Linux with an NVIDIA GPU. Its `/v1/realtime` endpoint speaks its own dialect, which no module implements.
- **LiteLLM, NVIDIA Dynamo and LocalAI** document the Realtime protocol that `openai-realtime-compatible` speaks.

A report of how one of them behaves is welcome at the issue tracker.

## See also

- [choosing-a-module.md](choosing-a-module.md): local against hosted, streaming against batch.
- [vad.md](vad.md): the gate every batch solution here needs.
- [modules/openai-transcriptions-compatible.md](modules/openai-transcriptions-compatible.md) and [modules/openai-realtime-compatible.md](modules/openai-realtime-compatible.md): every field of the two modules.
