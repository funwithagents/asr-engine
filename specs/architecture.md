---
code:
  - src/asr_engine/audio.py
  - src/asr_engine/engine.py
  - src/asr_engine/server.py
tests:
  - tests/test_audio.py
  - tests/test_engine.py
  - tests/test_server.py
---

# Architecture

**Status:** Implemented

## System Diagram

`ASREngine` owns the pipeline: it builds each stage, starts and stops them, and receives the module's utterances. The audio flows left to right; the two stages in brackets exist only when they are needed.

```
                              ASREngine  (builds, starts and stops every stage)
┌──────────────────────────────────────────────────────────────────────────────┐
│                                                                              │
│  AudioSource ──▶ [format converter] ──▶ [VAD gate] ──▶ ASR module ──▶ backend│
│  input device,   source format →        speech only,   Deepgram, Mistral,    │
│  file or custom  module format          + END_OF_SPEECH a local model, …     │
│                                                             │                │
│                                                 SpeechUtterance (each result)│
│                                                             ▼                │
│                                                         Segmenter            │
│                                                             │                │
└───────────────────────────┬─────────────────────────────────┼────────────────┘
                            │ on_speech_utterance             │ on_speech_segment
                            ▼                                 ▼
                 a Python application's callbacks, or the MCP server below
```

- **Format converter**: present when the source's format is not the module's ([format-conversion.md](format-conversion.md)).
- **VAD gate**: present when `engine.vad` is configured ([vad.md](vad.md)).

The engine is usable on its own (`import asr_engine`). `AsrTools` and the MCP server are optional layers over it, in the same process and on the same event loop:

```
┌──────────────────────────────────────────────┐
│ ASREngine                                    │
└───────┬──────────────────────────▲───────────┘
        │ utterances, segments     │ start / stop / listen / dictation
        ▼                          │
┌───────────────────┐      ┌───────┴───────────┐
│ MCP resources     │      │ AsrTools          │  (also registerable directly
│ asr://utterance   │      │ (tools layer)     │   by an in-process agent)
│ asr://segment     │      └───────▲───────────┘
└───────┬───────────┘              │
        │ notify            ┌──────┴───────────┐
        │                   │ MCP tools        │
        ▼                   └──────▲───────────┘
┌──────────────────────────────────┴───────────┐
│ MCP server (StreamableHTTP)                  │
└──────────────────────┬───────────────────────┘
                       │
        any MCP client (the demo client, Claude, …)
```

## Concurrency Model

The engine runs in a single Python process, on one `asyncio` event loop it shares with the tools layer and the MCP server when they are used:

- **Audio capture** runs in a separate thread (audio I/O is blocking) and pushes raw PCM chunks into an `asyncio.Queue`
- **ASR module** is an `async` component that reads from the queue and talks to its backend: a WebSocket connection for the streaming providers, HTTP requests or a model on a worker thread for the others
- **MCP server**, when used, runs on the same event loop, serving HTTP requests and pushing resource update notifications to subscribed clients

## Data Flow

1. Audio thread captures PCM audio chunks from the selected input device in the engine's source format, `engine.audio_format` (rate/channels via PortAudio; encoding via a transcode in the source — see [asr-module-interface.md](asr-module-interface.md))
2. Chunks are placed into an `asyncio.Queue`. When the module is fed another format than the source produces, a format converter reads that queue and feeds its own with the converted chunks ([format-conversion.md](format-conversion.md)); an optional VAD gate does the same after it ([vad.md](vad.md))
3. The ASR module reads chunks and streams them to the ASR backend (e.g. Deepgram WebSocket), reporting the format it is fed (`engine.module_audio_format`)
4. The backend returns transcription events (interim / final) as `SpeechUtterance`s
5. The ASR engine emits each utterance and, via its `Segmenter`, aggregates utterances into `SpeechSegment`s according to the current segment mode (see [engine.md](engine.md))
6. The engine updates the `asr://utterance` and `asr://segment` resources and calls `notify_resource_updated`
7. Subscribed MCP clients receive the updated resources

**Optional VAD stage.** When `engine.vad` is configured, a `VadGate` sits between steps 2 and 3: it reads the source's queue, forwards only the speech to the ASR module (the original bytes, with a pre-roll), and follows each utterance with an `END_OF_SPEECH` marker that makes the module emit its final. The gate runs on the event loop (one cheap model call per ~30 ms frame); the VAD model is loaded once, in a thread. Without `engine.vad` the stage does not exist and the flow above is exact. See [vad.md](vad.md).

Segmentation (trigger-word / timeout / one-per-utterance) is owned entirely by the engine — see [engine.md](engine.md) for the detail.

Audio capture is pluggable through the `AudioSource` seam: the default `AudioCapture` reads a `sounddevice` `InputStream`, but a caller can inject any `AudioSource` (a custom stream, a file, a scripted source) via `ASREngine(config, audio_source=...)`. That seam is a supported public extension point with its own chunk/lifecycle contract — see [audio-source.md](audio-source.md).
