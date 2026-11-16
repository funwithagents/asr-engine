# Running speech recognition locally

Nothing has to leave the machine: the engine can run a model itself, or talk to a speech server you run next to it. This page says which to pick and gives a recipe for each server that was run against the engine.

## Two ways to run a model locally

| | In process | A local server |
|---|---|---|
| How | The module loads the model inside your program | A server holds the model; a module talks to it over HTTP or a WebSocket |
| Modules | [`mlx-audio`](modules/mlx-audio.md) (Apple Silicon), [`kyutai`](modules/kyutai.md) | [`openai-transcriptions-compatible`](modules/openai-transcriptions-compatible.md), [`openai-realtime-compatible`](modules/openai-realtime-compatible.md) |
| To install | One extra | The `openai-compatible` extra, plus the server, installed and started separately |
| Good for | One application on one machine | A model on another machine (a GPU box), shared between programs, or a runtime the engine has no module for (whisper.cpp, faster-whisper) |

Start in process when a module covers your platform: it is one install and one config. Use a server for the rest.

## What to pick

| Your machine | Use |
|---|---|
| Apple Silicon Mac | [`mlx-audio`](modules/mlx-audio.md) in process: Parakeet by default, Whisper and Qwen3-ASR by `model` |
| Apple Silicon Mac, words appearing as you speak | The [mlx-audio server with a streaming model](#mlx-audio-server-streaming) |
| Linux or Windows with an NVIDIA GPU | [speaches](#speaches-faster-whisper), which runs faster-whisper on CUDA |
| Linux or Windows, CPU only | [whisper.cpp](#whispercpp) or [speaches](#speaches-faster-whisper) with a small model; or [`kyutai`](modules/kyutai.md) in process for English and French |

## What every local server has in common

- **No API key.** Leave `api_key_env` out of the module block.
- **Turn interims on for batch servers.** `openai-transcriptions-compatible` has them off by default because a hosted API bills each one. A local server is free, so set `"interim_interval_s": 0.5`.
- **The VAD gate is required** by `openai-transcriptions-compatible` and optional with `openai-realtime-compatible` ([vad.md](vad.md)).
- **Speech before `connected` is lost.** When the engine starts, the module sends the server one request (or opens one session), and many servers load the model at that moment. Until `engine.status()["connected"]` is true, what is said is not transcribed. A model's first load takes seconds; later starts take a fraction of a second.

To check a server once it runs, `scripts/check_local_server.py` plays a fixture through the engine and prints the interims and finals. Each recipe below gives its command.

## Recipes

Run on an Apple M5 Pro on 2026-11-16. The ports are arbitrary; every recipe uses its own so the servers can run side by side.

### mlx-audio server (batch)

[mlx-audio](https://github.com/Blaizzy/mlx-audio) ships an HTTP server, for Apple Silicon. It serves any of its batch models, chosen per request by the `model` field and loaded on first use.

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

Measured: an interim every half second and the final about 0.5 s after the end of speech, with Parakeet v3 and with `mlx-community/whisper-large-v3-turbo-asr-4bit` (add `"language": "en"`). On one machine the in-process [`mlx-audio`](modules/mlx-audio.md) module gives the same models with nothing to start.

### mlx-audio server (streaming)

The same server speaks the Realtime protocol at `/v1/realtime`, for models that support streaming sessions. This is the one recipe here where words arrive while you speak. The model is named in the URL.

```bash
uv run --with 'mlx-audio[stt,server]>=0.6' python -m mlx_audio.server --port 8765 --realtime
```

```json
{
  "audio": { "sample_rate": 24000 },
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

### whisper.cpp

[whisper.cpp](https://github.com/ggml-org/whisper.cpp) runs Whisper in C++, on the GPU of an Apple Silicon Mac and on CUDA, Vulkan or the CPU elsewhere. Its server takes one model file, chosen when it starts.

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

### speaches (faster-whisper)

[speaches](https://github.com/speaches-ai/speaches) serves [faster-whisper](https://github.com/SYSTRAN/faster-whisper), the Whisper runtime to pick on an NVIDIA GPU. It has no package: it runs from its Docker image (`ghcr.io/speaches-ai/speaches:latest-cuda` or `latest-cpu`) or from a clone.

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

Measured on the CPU of a Mac, where faster-whisper has no GPU support: 0.8 to 1 s per request with the `small` model, so the final comes about 1.5 s after the end of speech and only some interims get through. A CUDA machine is what this server is for; it was not measured there.

**Use `openai-transcriptions-compatible` with speaches, not the realtime module.** Its `/v1/realtime` endpoint was run too, and it is not a good fit:

- it sends no interims, so it gives nothing the batch endpoint does not;
- it ignores the session settings the module sends (the engine [warns about it](modules/openai-realtime-compatible.md#troubleshooting)) and keeps its own turn detection, which cannot be turned off;
- with the VAD gate on, the server drops the connection when the gate ends an utterance, so `engine.vad` cannot be used with it;
- from a clone it needs `LOOPBACK_HOST_URL=http://127.0.0.1:8082` in its environment to work at all (speaches issue 559).

## Not run yet

- **vLLM** serves batch models (Whisper and others) on `/v1/audio/transcriptions`, which `openai-transcriptions-compatible` should reach like the servers above. It needs a Linux machine with an NVIDIA GPU and was not run. Its `/v1/realtime` endpoint speaks its own dialect, which no module implements.
- **LiteLLM, NVIDIA Dynamo and LocalAI** document the Realtime protocol that `openai-realtime-compatible` speaks. None was run.

## See also

- [choosing-a-module.md](choosing-a-module.md): local against hosted, streaming against batch.
- [vad.md](vad.md): the gate every batch recipe here needs.
- [modules/openai-transcriptions-compatible.md](modules/openai-transcriptions-compatible.md) and [modules/openai-realtime-compatible.md](modules/openai-realtime-compatible.md): every field of the two modules.
