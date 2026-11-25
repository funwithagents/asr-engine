# Gradio ASR demo

A page to set the engine up and try it without writing a config by hand. It does not start or connect to the MCP server: it uses `ASREngine` directly, in-process.

The page has three tabs:

- **Setup** (it opens there): choose the module and the input device, fill their fields, the voice activity detection and the segmentation; **Check** what the machine is missing, **Install missing extras**, **Prepare** the models (a local model, the Silero VAD model: each downloaded with its progress shown; skipped, the first start downloads it itself), **Test** the config with a light real call (a hosted provider lists its models with your key; a local model loads once); then **Save**.
- **Secrets**: the keys the config names (`DEEPGRAM_API_KEY`, …), where each comes from, and a place to save one in the demo's own `.env`.
- **Listen**: start a session with the saved config, run a single-shot `listen`, start and stop a dictation, and watch the utterances and segments arrive.

It starts on the `fake` module ([default_config.json](default_config.json), written into the folder at the first launch), which needs nothing installed and no key: Start replays a short script on your microphone's audio clock, so you can see the pipeline work before choosing a real module.

## Run

From the repository root:

```bash
uv sync
uv run python -m examples.gradio_demo
```

Then open the address it prints (`http://127.0.0.1:7860` by default).

```text
--folder DIR   The demo's folder: its config.json and its .env (default: ~/.config/asr-engine-demo)
--config FILE  Run this config file as it is, instead of a folder (it is never written)
--env PATH     The .env the keys are read from and saved to (default: the one next to the config)
--host HOST    The address to serve on (default: 127.0.0.1)
--port PORT    The port to serve on (default: gradio's)
```

The panels come from the `app-setup` project, in the repository's `demo` dependency group, which `uv sync` installs for contributors; it is not a dependency of the `asr-engine` package.

## Three ways to launch

A config is the engine block itself, the same document everywhere: a folder's `config.json`, a file given with `--config`, any file of [examples/configs/](../configs/), and what `ASREngineConfig.from_json_file` reads.

- **From the demo's folder** (the default, or `--folder DIR`): the Setup tab edits and saves the folder's `config.json`; nothing starts until you press Start.
- **From a config file**, run as it is:

  ```bash
  uv run python -m examples.gradio_demo --config examples/configs/deepgram-v2.json
  ```

  The file is never written: the Setup tab is read-only (Check, Install, Prepare and Test still work, there is no Save). The session starts at launch, and the page opens on the Listen tab; when the config cannot run yet (a missing extra, a key not set), the Listen tab says what is missing instead. A file that does not exist, or is not a JSON object, stops the demo at launch with the reason.
- **The config app**, to edit a config file on a page without running anything:

  ```bash
  uv run python -m examples.config_app speech.json [--env PATH] [--host HOST] [--port PORT]
  ```

  It has the Setup and Secrets tabs only: Save writes the engine block back to the file, Revert reads it again. The file must exist (start from a copy of one in `examples/configs/`).

**Where the keys are.** Keys come from the `.env` next to the config: the folder's, or the one in the config file's directory, unless `--env` names another. Keys you save on the Secrets tab go there; a variable your shell sets wins over it. The repository ignores `.env` files, so a key saved next to `examples/configs/` cannot be committed.

**A folder made by an earlier version** holds the config wrapped in an `"asr"` key, which the demo cannot run any more (Start says `unknown field(s) asr`). Delete the folder (`~/.config/asr-engine-demo` by default) once: the next launch writes the default config again.

## The session

Start and Listen read the **saved** config. When it has problems or the machine is missing something it cannot start without (an extra, a key, a local file the config names), they say what instead of starting. A model not prepared yet does not stop it: the session downloads it as it starts, which takes longer. While a session runs, the config is locked: Save and Reset are disabled on the Setup tab until you press Stop. Stop releases the engine, so the next Start runs whatever was saved in between.

## Segmentation controls

The mode selector drives both single-shot listening and dictation:

- `utterance` closes on the first final utterance;
- `trigger_word` aggregates until a configured trigger word is spoken; and
- `timeout` closes after initial silence or end-of-speech silence.

The trigger words and the timeouts are set on the Setup tab (Segmentation, under its advanced fields). Dictation is available while a session runs. Its "end on first final segment" option chooses between a one-segment session and a persistent session that runs until Stop dictation.
