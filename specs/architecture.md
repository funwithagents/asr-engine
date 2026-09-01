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

```
┌─────────────────────────────────────────────────────────┐
│                      MCP Server Process                  │
│                                                         │
│  ┌─────────────┐     ┌──────────────┐                   │
│  │ Audio       │────▶│ ASR Module   │                   │
│  │ Capture     │     │ (e.g.        │                   │
│  │ (PyAudio /  │     │  Deepgram)   │                   │
│  │  sounddevice│     │              │                   │
│  └─────────────┘     └──────┬───────┘                   │
│                             │ utterances                │
│                             ▼                           │
│                      ┌──────────────┐                   │
│                      │ ASR Engine   │  (background task) │
│                      │ + Segmenter  │                   │
│                      └──────┬───────┘                   │
│                utterances / │ segments (notify)         │
│                             ▼                           │
│                      ┌──────────────┐                   │
│                      │ Resource     │                   │
│                      │ Manager      │                   │
│                      │ asr://utter. │                   │
│                      │ asr://segment│                   │
│                      └──────┬───────┘                   │
│                             │                           │
│                      ┌──────┴───────┐                   │
│                      │  Tools layer │  (AsrTools)        │
│                      │  start/stop  │                   │
│                      │  listen      │                   │
│                      └──────┬───────┘                   │
│                             │                           │
│                      ┌──────┴───────┐                   │
│                      │  MCP Layer   │                   │
│                      │  Resources   │                   │
│                      │  Tools       │                   │
│                      └──────┬───────┘                   │
│                             │ StreamableHTTP            │
└─────────────────────────────┼───────────────────────────┘
                              │
              ┌───────────────┴────────────────┐
              │                                │
     ┌────────▼────────┐             ┌─────────▼───────┐
     │  Demo Client    │             │  Any MCP Client  │
     │  (logs results) │             │  (Claude, etc.)  │
     └─────────────────┘             └──────────────────┘
```

## Concurrency Model

The server runs as a single Python process with an `asyncio` event loop:

- **Audio capture** runs in a separate thread (audio I/O is blocking) and pushes raw PCM chunks into an `asyncio.Queue`
- **ASR module** is an `async` component that reads from the queue and maintains a WebSocket connection to the ASR backend
- **MCP server** runs on the same event loop, serving HTTP requests and pushing resource update notifications to subscribed clients

## Data Flow

1. Audio thread captures PCM audio chunks from the selected input device at the
   engine's reconciled `AudioFormat` (rate/channels via PortAudio; encoding via an
   in-pipeline transcode — see [asr-module-interface.md](asr-module-interface.md))
2. Chunks are placed into a shared `asyncio.Queue`
3. The ASR module reads chunks and streams them to the ASR backend (e.g. Deepgram WebSocket), reporting that same `AudioFormat`
4. The backend returns transcription events (interim / final) as `SpeechUtterance`s
5. The ASR engine emits each utterance and, via its `Segmenter`, aggregates
   utterances into `SpeechSegment`s according to the current segment mode
   (see [engine.md](engine.md))
6. The engine updates the `asr://utterance` and `asr://segment` resources and
   calls `notify_resource_updated`
7. Subscribed MCP clients receive the updated resources

Segmentation (trigger-word / timeout / one-per-utterance) is owned entirely by
the engine — see [engine.md](engine.md) for the detail.
