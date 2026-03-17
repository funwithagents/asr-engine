# ASR MCP — Implementation Plans Index

Plans are meant to be executed in order. Each plan builds on the previous ones.

| Plan | File | Description | Status |
|---|---|---|---|
| 01 | [Project Setup](01-project-setup.md) | `uv` init, dependencies, source tree, entry points | done |
| 02 | [Configuration](02-config.md) | Config dataclasses, loading, validation, CLI arg parsing | done |
| 03 | [Audio Capture](03-audio-capture.md) | `sounddevice` input stream → `asyncio.Queue` | not started |
| 04 | [ASR Module Interface](04-asr-module-interface.md) | ABC, `ASRResult`, `ResultCallback`, registry | not started |
| 05 | [Deepgram Module](05-deepgram-module.md) | WebSocket streaming, KeepAlive, reconnection | not started |
| 06 | [ASR Engine](06-asr-engine.md) | Wires audio + module, manages pause/resume/status | not started |
| 07 | [MCP Server](07-mcp-server.md) | Resource `asr://result`, tools, StreamableHTTP, startup wiring | not started |
| 08 | [Demo Client](08-demo-client.md) | Subscribe, log results, clean disconnect | not started |

**Legend:** not started · in progress · done
