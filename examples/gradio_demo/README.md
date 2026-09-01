# Gradio ASR demo

This browser UI demonstrates direct, in-process use of `ASREngine`. It does not start or connect to the MCP server.

The demo lets you:

- choose an input device and bundled ASR module;
- start and stop continuous capture;
- run a single-shot `listen`;
- start and stop a dictation session;
- change segmentation parameters; and
- inspect live utterance and segment updates.

`DemoController` owns the engine on a dedicated asyncio event-loop thread, while the Gradio layer remains a thin synchronous UI. This keeps all engine operations and callbacks on one event loop.

## Setup and run

From the repository root:

```bash
uv sync
export DEEPGRAM_API_KEY="..."
uv run python -m examples.gradio_demo.app
```

Open `http://127.0.0.1:7860`.

Gradio is in the repository's `demo` dependency group. That group is synced by default for contributors but is not a runtime dependency of the `asr-engine` wheel.

## Options

```text
--config PATH  Load the engine block from an ASR Engine JSON config
--host HOST    UI bind host (default: 127.0.0.1)
--port PORT    UI port (default: 7860)
```

When `--config` is omitted, the demo uses `deepgram_v1`, the system-default input device, and `DEEPGRAM_API_KEY`. When a config is supplied, only its `engine` block is used; the MCP `server` block is ignored.

Changing the device or module while stopped causes the controller to construct a fresh engine on the next start or listen. Module-specific settings continue to come from the loaded configuration.

## Segmentation controls

The mode selector drives both single-shot listening and dictation:

- `utterance` closes on the first final utterance;
- `trigger_word` aggregates until a configured trigger word is spoken; and
- `timeout` closes after initial silence or end-of-speech silence.

Dictation is available while continuous capture is running. Its “end on first final segment” option chooses between a one-segment session and a persistent session that runs until Stop dictation.
