# ASR MCP — Implementation Plans

Implementation plans for ASR MCP — each plan turns a settled part of a spec (see [specs/_index.md](../specs/_index.md)) into concrete, buildable steps. Plans are ordered by their date-time filename prefix (`YYYYMMDDHHmm_`) and were executed in that order, each building on the previous ones.

## Plans

<!-- One row per plan, chronological by filename prefix. Keep the Status column in sync with each plan's `**Status:**` line. -->

| Plan | Description | Status |
|---|---|---|
| [202603170910_project-setup.md](202603170910_project-setup.md) | `uv` init, dependencies, source tree, entry points | Done |
| [202603170911_config.md](202603170911_config.md) | Config dataclasses, loading, validation, CLI arg parsing | Done |
| [202603170912_audio-capture.md](202603170912_audio-capture.md) | `sounddevice` input stream → `asyncio.Queue` | Done |
| [202603170913_asr-module-interface.md](202603170913_asr-module-interface.md) | ABC, `ASRResult`, `ResultCallback`, registry | Done |
| [202603170914_deepgram-module.md](202603170914_deepgram-module.md) | WebSocket streaming, KeepAlive, reconnection | Done |
| [202603170915_asr-engine.md](202603170915_asr-engine.md) | Wires audio + module, manages pause/resume/status | Done |
| [202603170916_mcp-server.md](202603170916_mcp-server.md) | Resource `asr://result`, tools, StreamableHTTP, startup wiring | Done |
| [202603170917_demo-client.md](202603170917_demo-client.md) | Subscribe, log results, clean disconnect | Done |
| [202603171719_e2e-testing.md](202603171719_e2e-testing.md) | FileAudioSource, engine injection, in-process server+client tests | Done |
| [202603181546_asr-to-terminal.md](202603181546_asr-to-terminal.md) | TerminalTyper, AsrToTerminal state machine, unit tests, e2e tests | Done |
| [202603191300_listen-tool.md](202603191300_listen-tool.md) | `auto_start` config, `speech_utils`, `ListenSession`, `listen` tool, e2e tests | Done |
| [202603200955_listen-streaming.md](202603200955_listen-streaming.md) | Progress notifications for `listen` tool: `on_final_committed` callback, `ctx: Context`, `progress_callback` in `McpToolClient` | Done |
| [202603201532_end-of-utterance-detector.md](202603201532_end-of-utterance-detector.md) | Rename `ListenSession` → `EndOfUtteranceDetector`, wire into `AsrToTerminal` with `trigger_word`/`timeout` mode support | Done |
| [202603201617_sound-feedback.md](202603201617_sound-feedback.md) | Play bundled WAV cues at `listen` start/stop; `audio.output_device` config; `SoundFeedback` module | Done |

## Status legend

- **Todo** — written, not yet started
- **In progress** — actively being implemented
- **Done** — implemented, verified (lint/type-check/tests pass), and merged
