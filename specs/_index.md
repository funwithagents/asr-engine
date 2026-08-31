# ASR MCP

ASR MCP is a real-time Automatic Speech Recognition server exposed over the Model Context Protocol. It captures audio continuously from a system input device, streams it to a pluggable ASR backend (Deepgram first), and publishes the latest utterance and the latest aggregated segment as two rolling MCP resources (`asr://utterance`, `asr://segment`) over StreamableHTTP, alongside tools to start, stop, query, and `listen` for speech. The design revolves around one always-on asyncio pipeline — audio capture in a thread, everything else async on one event loop — with the engine aggregating utterances into segments that clients subscribe to rather than a full transcript history.

## Specs

<!-- One row per concept spec. Keep the Status column in sync with each spec's `**Status:**` line. -->

| Spec | Description | Status |
|---|---|---|
| [project.md](project.md) | Project structure and tooling: Python version, packaging with uv, layout conventions, ruff/pyright | Implemented |
| [testing.md](testing.md) | Testing strategy: two-tier `tests/`/`tests-e2e/` split, functional-test philosophy | Implemented |
| [overview.md](overview.md) | Goals, components, constraints, non-goals | Implemented |
| [architecture.md](architecture.md) | System diagram, concurrency model, data flow | Implemented |
| [engine.md](engine.md) | `ASREngine` + `Segmenter`: `SpeechUtterance`/`SpeechSegment`, `on_speech_utterance`/`on_speech_segment` callbacks, `set_segment_mode`, `listen` | Implemented |
| [configuration.md](configuration.md) | Config file schema, fields, validation rules | Implemented |
| [mcp-server.md](mcp-server.md) | Resources `asr://utterance` + `asr://segment`, tools (`start`, `stop`, `is_running`, `listen`), `auto_start` config, server lifecycle | Implemented |
| [asr-module-interface.md](asr-module-interface.md) | ABC, audio format contract, registry, reconnection | Implemented |
| [deepgram-module.md](deepgram-module.md) | WebSocket details, config fields, message mapping | Implemented |
| [demo-client.md](demo-client.md) | CLI, log format, behavior | Implemented |
| [e2e-testing.md](e2e-testing.md) | File-based e2e pipeline: audio source abstraction, fixture format, assertions | Implemented |
| [asr-to-terminal.md](asr-to-terminal.md) | Progressive terminal injection via xdotool/ydotool, consuming the server's `asr://segment` resource | Implemented |
| [sound-feedback.md](sound-feedback.md) | Bundled WAV cues at `listen` start/stop; `SoundFeedback` module; `audio.output_device` and `listen.sound_feedback` config | Implemented |

Each spec also opens with a YAML **frontmatter** block declaring the `code:` and `tests:` files it governs — the spec → code/tests mapping the spec-drift checks use to scope what they compare. Keep it current when files move, and see [AGENTS.md](../AGENTS.md) ("Spec frontmatter") for the full convention.

## Status legend

- **Not started** — no design decisions made yet
- **Draft** — actively being brainstormed/defined, contains open questions
- **Stable** — design settled, reviewed and validated (open questions are deferrals only), **ready to implement but not necessarily implemented yet**. This is the design-review gate, before code is written.
- **Implemented** — a **Stable** spec that a `Done` plan has built: the code now exists and matches the spec (design and code in sync)
- **Updated** — an **Implemented** spec since edited in a way that needs new code, so the code no longer matches it; a new implementation plan is needed (or in progress) to catch up. Returns to **Implemented** once that plan is `Done`.
