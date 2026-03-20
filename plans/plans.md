# ASR MCP — Implementation Plans Index

Plans are meant to be executed in order. Each plan builds on the previous ones.

| Plan | File | Description | Status |
|---|---|---|---|
| 01 | [Project Setup](01-project-setup.md) | `uv` init, dependencies, source tree, entry points | done |
| 02 | [Configuration](02-config.md) | Config dataclasses, loading, validation, CLI arg parsing | done |
| 03 | [Audio Capture](03-audio-capture.md) | `sounddevice` input stream → `asyncio.Queue` | done |
| 04 | [ASR Module Interface](04-asr-module-interface.md) | ABC, `ASRResult`, `ResultCallback`, registry | done |
| 05 | [Deepgram Module](05-deepgram-module.md) | WebSocket streaming, KeepAlive, reconnection | done |
| 06 | [ASR Engine](06-asr-engine.md) | Wires audio + module, manages pause/resume/status | done |
| 07 | [MCP Server](07-mcp-server.md) | Resource `asr://result`, tools, StreamableHTTP, startup wiring | not started |
| 08 | [Demo Client](08-demo-client.md) | Subscribe, log results, clean disconnect | done |
| 09 | [E2E Testing](09-e2e-testing.md) | FileAudioSource, engine injection, in-process server+client tests | done |
| 10 | [ASR to Terminal](10-asr-to-terminal.md) | TerminalTyper, AsrToTerminal state machine, unit tests, e2e tests | done |
| 11 | [Listen Tool](11-listen-tool.md) | `auto_start` config, `speech_utils`, `ListenSession`, `listen` tool, e2e tests | done |
| 12 | [Listen Streaming](12-listen-streaming.md) | Progress notifications for `listen` tool: `on_final_committed` callback, `ctx: Context`, `progress_callback` in `McpToolClient` | done |
| 13 | [EndOfUtteranceDetector](13-end-of-utterance-detector.md) | Rename `ListenSession` → `EndOfUtteranceDetector`, wire into `AsrToTerminal` with `trigger_word`/`timeout` mode support | done |

**Legend:** not started · in progress · done
