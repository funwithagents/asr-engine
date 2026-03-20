# ASR MCP — Specifications Index

## Reading Order

| # | Spec | Description | Implemented |
|---|---|---|---|
| 1 | [Overview](overview.md) | Goals, components, constraints, non-goals | no |
| 2 | [Architecture](architecture.md) | System diagram, concurrency model, data flow | no |
| 3 | [Configuration](configuration.md) | Config file schema, fields, validation rules | no |
| 4 | [MCP Server](mcp-server.md) | Resource `asr://result`, tools (`start`, `stop`, `is_running`, `listen`), `auto_start` config, server lifecycle | yes |
| 5 | [ASR Module Interface](asr-module-interface.md) | ABC, audio format contract, registry, reconnection | no |
| 6 | [Deepgram Module](deepgram-module.md) | WebSocket details, config fields, message mapping | no |
| 7 | [Demo Client](demo-client.md) | CLI, log format, behavior | yes |
| 8 | [Project Structure](project-structure.md) | Folder layout, entry points, dependencies | yes |
| 9 | [E2E Testing](e2e-testing.md) | File-based e2e tests: audio source abstraction, fixture format, assertions | yes |
| 10 | [ASR to Terminal](asr-to-terminal.md) | Progressive terminal injection via xdotool/ydotool, mode-based end-of-utterance detection | yes |
| 11 | [End-of-Utterance Detector](end-of-utterance-detector.md) | Shared `EndOfUtteranceDetector`: trigger_word and timeout modes, used by `listen` tool and `AsrToTerminal` | yes |
| 12 | [Sound Feedback](sound-feedback.md) | Bundled WAV cues at `listen` start/stop; `SoundFeedback` module; `audio.output_device` and `listen.sound_feedback` config | yes |

**Legend:** no = not implemented · yes = implemented
