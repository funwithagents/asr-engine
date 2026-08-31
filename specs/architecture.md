---
code:
  - src/asr_mcp/audio.py
  - src/asr_mcp/engine.py
  - src/asr_mcp/server.py
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
│                             │ ASR results               │
│                             ▼                           │
│                      ┌──────────────┐                   │
│                      │ ASR Engine   │  (background task) │
│                      │              │                   │
│                      └──────┬───────┘                   │
│                             │ notify on update          │
│                             ▼                           │
│                      ┌──────────────┐                   │
│                      │ Resource     │                   │
│                      │ Manager      │                   │
│                      │ asr://result │                   │
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

1. Audio thread captures PCM audio chunks from the selected input device
2. Chunks are placed into a shared `asyncio.Queue`
3. The ASR module reads chunks and streams them to the ASR backend (e.g. Deepgram WebSocket)
4. The backend returns transcription events (interim / final)
5. The ASR engine updates the current resource value and calls `notify_resource_updated`
6. Subscribed MCP clients receive the updated resource
