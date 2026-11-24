# Gradio ASR demo

A page to set the engine up and try it without writing a config by hand. It does not start or connect to the MCP server: it uses `ASREngine` directly, in-process.

The page has three tabs:

- **Setup** (it opens there): choose the module and the input device, fill their fields, the voice activity detection and the segmentation; **Check** what the machine is missing, **Install missing extras**, **Download missing files** (a local model, the Silero VAD model), **Test** the config with a light real call (a hosted provider lists its models with your key; a local model loads once); then **Save**.
- **Secrets**: the keys the config names (`DEEPGRAM_API_KEY`, …), where each comes from, and a place to save one in the demo's own `.env`.
- **Listen**: start a session with the saved config, run a single-shot `listen`, start and stop a dictation, and watch the utterances and segments arrive.

It starts on the `fake` module, which needs nothing installed and no key: Start replays a short script on your microphone's audio clock, so you can see the pipeline work before choosing a real module.

## Run

From the repository root:

```bash
uv sync
uv run python -m examples.gradio_demo
```

Then open the address it prints (`http://127.0.0.1:7860` by default).

```text
--folder PATH  The demo's folder: its config.json and its .env (default: ~/.config/asr-engine-demo)
--host HOST    The address to serve on (default: 127.0.0.1)
--port PORT    The port to serve on (default: gradio's)
```

The panels come from the `module-setup` project, in the repository's `demo` dependency group, which `uv sync` installs for contributors; it is not a dependency of the `asr-engine` package. Keys you save on the Secrets tab go to the folder's `.env`; a variable your shell sets wins over it.

## The session

Start and Listen read the **saved** config. When it has problems or the machine is missing something (an extra, a key, a model), they say what instead of starting. While a session runs, the config is locked: Save and Reset are disabled on the Setup tab until you press Stop. Stop releases the engine, so the next Start runs whatever was saved in between.

## Segmentation controls

The mode selector drives both single-shot listening and dictation:

- `utterance` closes on the first final utterance;
- `trigger_word` aggregates until a configured trigger word is spoken; and
- `timeout` closes after initial silence or end-of-speech silence.

The trigger words and the timeouts are set on the Setup tab (Segmentation, under its advanced fields). Dictation is available while a session runs. Its "end on first final segment" option chooses between a one-segment session and a persistent session that runs until Stop dictation.
