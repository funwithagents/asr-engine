# ASR MCP — Implementation Details Index

This folder contains notes written **after** each component is implemented.
Each file explains non-obvious choices, gotchas, and deviations from the original specs.

| Plan | File | Description | Written |
|---|---|---|---|
| 01 — Project Setup | [01-project-setup.md](01-project-setup.md) | uv setup, dependency notes | yes |
| 02 — Configuration | [02-config.md](02-config.md) | Config loading, validation notes | yes |
| 03 — Audio Capture | [03-audio-capture.md](03-audio-capture.md) | sounddevice integration notes | yes |
| 04 — ASR Module Interface | [04-asr-module-interface.md](04-asr-module-interface.md) | ABC and registry notes | yes |
| 05 — Deepgram Module | [05-deepgram-module.md](05-deepgram-module.md) | Deepgram SDK integration notes | yes |
| 06 — ASR Engine | [06-asr-engine.md](06-asr-engine.md) | Engine wiring and state machine notes | yes |
| 07 — MCP Server | [07-mcp-server.md](07-mcp-server.md) | Resource, tools, StreamableHTTP notes | yes |
| 08 — Demo Client | [08-demo-client.md](08-demo-client.md) | Client subscription and logging notes | yes |
| 09 — E2E Testing | [09-e2e-testing.md](09-e2e-testing.md) | AudioSource protocol, FileAudioSource, engine injection, e2e tests | yes |

**Legend:** no = not yet written · yes = written

## Convention

Each file in this folder should cover:
- **What was implemented** (brief summary)
- **Deviations from spec** (if any, and why)
- **Non-obvious decisions** (trade-offs, workarounds, SDK quirks)
- **Known limitations** (anything deferred to a later iteration)
