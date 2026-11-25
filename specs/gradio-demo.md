---
code:
  - examples/gradio_demo/__init__.py
  - examples/gradio_demo/__main__.py
  - examples/gradio_demo/controller.py
  - examples/gradio_demo/app.py
  - examples/gradio_demo/default_config.json
  - examples/config_app/__init__.py
  - examples/config_app/__main__.py
  - pyproject.toml
tests:
  - tests/examples/test_gradio_demo.py
  - tests/examples/test_config_app.py
---

# Gradio Demo App

**Status:** Implemented

## Purpose

A page to try the engine without writing a config by hand: choose a module and an input device, configure them, install their extras, download their models, set the key, then listen. It is the engine's demo, built on [setup.md](setup.md): its Setup and Secrets panels, the app's folder and the checks behind them come from the `app-setup` project, whose specs are the reference for them; this spec says what the engine's demo puts around them: its section, its page and its listening session.

It is a **direct-import consumer** of the library: it builds an `ASREngine` from an `ASREngineConfig` in-process and calls the engine API directly. It is **not** an MCP client and does **not** go through `AsrTools` (see [tools.md](tools.md)). This demo is the worked example of the "Direct import (no MCP)" usage in [engine.md](engine.md).

## Non-goals

- **Not** an MCP client — see [demo-client.md](demo-client.md) for that.
- **No** browser-microphone capture. Audio comes from a server-side input device via the engine's normal `AudioCapture`; feeding Gradio's streaming-audio component through a custom `AudioSource` is a deferred idea (Open questions).
- Not shipped as a dependency of the package: the page is a dependency group.

## Running it

```
uv run python -m examples.gradio_demo [--folder DIR | --config FILE] [--env PATH] [--host ADDRESS] [--port N]
uv run python -m examples.config_app FILE [--env PATH] [--host ADDRESS] [--port N]
```

(`python -m examples.gradio_demo.app` runs the demo too.) The command line is app-setup's (`add_config_arguments`, `open_source`), plus `--host` (`127.0.0.1` by default) and `--port` (gradio's by default). Three launches:

- **From the demo's folder** (`--folder DIR`, by default `~/.config/asr-engine-demo`): its `config.json`, edited and saved on the Setup tab, and its `.env`. The page opens on its Setup tab, with the `fake` module on a new folder; nothing starts at launch (the demo has no autostart).
- **From a config file** (`--config FILE`), run as it is: a `ConfigFile` that is never written, so the Setup panel is read-only (Check, Install, Prepare and Test still work; there is no Save, and no Reset: a file has no default). The session starts at launch (`starts_at_launch(model, None)` is true for a file): `main` calls `controller.start(model)` before the page is served, so with blockers the Listen tab's message says them instead, exactly as a refused Start does. The page opens on the Listen tab. A file that does not exist or is not a JSON object is refused at launch: `ConfigFileError` becomes `parser.error`, and nothing is served.
- **The config app** (`examples/config_app/`), one file edited with no session: app-setup's `add_config_app_arguments` and `open_config_app(options, APP)` (a writable `ConfigFile`, the demo's own `APP`), served as `config_page(model)`: the Setup and Secrets tabs, Save writing the file back, Revert reading it again. A missing file is refused the same way. It is a module run with `python -m`, not a console script: the published package does not depend on app-setup.

**The keys.** Each launch reads the `.env` next to its config (the folder's, or the one in the file's directory) unless `--env PATH` names another, and loads it under the shell's variables (the shell wins). The Secrets tab saves keys there. `.env` is git-ignored in the repository, so a key saved next to `examples/configs/` cannot be committed.

**The config is the engine block.** The demo's `App` is `bare=True`: the folder's `config.json`, a `--config` file, the config app's file and `default_config.json` are all the engine block itself, the document `ASREngineConfig.from_json_file` and the setup contract take, so any file of `examples/configs/` runs as it is. app-setup wraps it as `{"asr": block}` for the panels only. A folder made by an earlier version holds the wrapped shape, which the engine refuses (`unknown field(s) asr`, shown as Start's blocker): delete the folder once, and the next launch writes the default config again.

`app-setup[ui]` (and through it gradio) is the `demo` dependency group, synced by default with `dev` (`[tool.uv] default-groups = ["dev", "demo"]`), so pyright and the tests see the page: it is never a dependency of the published package. It cannot share an environment with the `kyutai-mlx` extra (gradio's `huggingface-hub` floor is above moshi-mlx's ceiling), declared in `[tool.uv] conflicts`; the Kyutai environment is synced without the default groups anyway.

## What the demo declares

- **One section, `asr`**, whose document is the engine block (`ASREngineConfig.from_dict`'s), described by `asr_engine.setup`, titled "Speech recognition".
- **The host is `asr-engine` itself**, so "Install missing extras" installs this project's own extras (`deepgram`, `silero`, …) from its lock.
- **The default config** is a committed file, `examples/gradio_demo/default_config.json` (one field per line, for review), the engine block alone, loaded into `App(default_config=...)`; app-setup writes it into an empty folder at the first launch. It is the `fake` module with a two-utterance script, so Start shows the pipeline working on the microphone's audio clock before any choice.
- **The app is bare** (`App(..., bare=True)`): its one section's document is the whole config (above).
- **The page**: three tabs, **Listen** first, then app-setup's Setup panel and its Secrets panel, which follows the Setup panel's draft (a key a newly chosen module needs appears before saving). It opens on Setup, or on Listen for a config file run as it is (`model.editable` false).
- **The model**: `AppModel(APP, open_source(options, APP), locked=controller.locked)`, built by `open_demo(argv, controller)`, which also starts a file's session. Building it makes a folder ready (created, the default config written when there is none) and loads the `.env` under the shell's variables.

The input device is chosen on the Setup panel: the "Input device" branch of the section's `audio_source`, whose `device` field lists the machine's inputs (`options("input-devices")`). The module, its fields, the VAD gate and the segmentation settings are chosen there too; the Listen tab has no config of its own.

## The session: a lock while it runs

Something keeps running between two uses (a microphone stream, a connection), so the demo's tab is a **session with a lock**:

- **Start** (always-on capture) and **Listen** (single-shot) read the **saved** config. With blockers (`model.blockers(saved)`: its problems, then the **required** items the machine is missing; a model not prepared yet is not one, the session downloads it as it starts) they say why, as the message, and start nothing. Else they build an `ASREngine` from `model.used_config(saved)["asr"]`, with `base_dir=model.path`: its relative file paths (an `audio_source.audio_file`, a `vad.model_path`, a local model) are relative to the app's folder, or to a config file's directory, the directory app-setup passes to the setup checks as their `base_dir`, so Check and Start look at the same file. Then they start it. A config the engine still refuses (`ConfigError`), or a missing extra (`ImportError`), is said the same way and leaves nothing behind.
- **Stop** stops the session and **releases** the engine: the next Start builds one from the saved config again, so a change saved in between is what the next session runs. A Listen releases its engine when its capture ends or is cancelled.
- **`locked()`** is true from a Start (or Listen) until the engine is released. The Setup panel polls it and disables Save and Reset meanwhile; `model.save` raises `RuntimeError`.

### What the Listen tab says about the Setup tab

- **Unsaved changes.** Above Start, a line says "The Setup tab has unsaved changes: Start uses the saved config." while the Setup panel's draft differs from the saved config, and nothing otherwise: app-setup's `unsaved_notice(model, setup.draft)`, on `setup.draft.change` and `setup.saved.change`.
- **A refused start's reasons stay current.** When the last message is the blockers of a refused Start or Listen, `controller.refresh_blockers(model)` replaces them with the saved config's current blockers, or with "The saved config has nothing missing any more: Start can run it." when there are none; any other message is left as it is. The page calls it when the Listen tab is selected, on `setup.saved.change`, and when a key changes: each tick compares the store's `model.secrets.revision` with the one this browser tab last saw (a `gr.State`) and refreshes only when it moved, so the blockers are not worked out on every tick.

## Concurrency model

The `ASREngine` is async and expects a running event loop; Gradio callbacks are synchronous. The controller owns a **dedicated asyncio event loop running in a background thread** and marshals every engine call onto it with `asyncio.run_coroutine_threadsafe(...)`, blocking the calling handler until it completes (with a timeout). Engine callbacks (`on_speech_utterance`, `on_speech_segment`) fire on that loop thread; the controller appends to thread-safe buffers that the UI reads. This keeps the engine on one loop (the "asyncio throughout" invariant) while Gradio stays synchronous.

## `DemoController`

The UI-agnostic core (`controller.py`, which imports `asr_engine` and `app_setup.app`, never gradio). Holds the loop thread, at most one session's engine and its config, and rolling event state.

```python
class DemoController:
    def __init__(self) -> None: ...

    # A session uses the saved config.
    def locked(self) -> bool: ...

    # The session: blockers, else built from the saved config and started.
    def start(self, model: AppModel) -> None: ...
    # engine.stop() then release, or cancel a listen.
    def stop(self) -> None: ...
    # Single-shot, in the background, cancelable.
    def listen(self, model: AppModel, mode: str | None = None) -> None: ...

    # Dictation, marshaled onto the loop thread; the session must be running.
    def start_dictation(
        self, mode: str | None = None, end_on_final_segment: bool = False
    ) -> None: ...
    def stop_dictation(self) -> None: ...

    # A refused start's message made the saved config's current blockers.
    def refresh_blockers(self, model: AppModel) -> None: ...

    def clear_logs(self) -> None: ...
    def state(self, model: AppModel) -> ControllerState: ...
```

### `listen`

`listen` requires no session running. It is **non-blocking**: it schedules `engine.listen(mode)` as a task on the loop thread and returns immediately. The controller enters the `listening` phase; when the capture completes, its closed `SpeechSegment` is stored as `last_listen`, the engine is released and the phase returns to `stopped`. `mode` is the value of the **Segmentation mode** dropdown; with `None` the engine falls back to its `listen_default_segmentation_mode`.

A listen can be **cancelled**: `stop()` cancels the listen task, which unwinds `engine.listen`'s own cleanup; the phase then returns to `stopped` with a "Listen cancelled." message.

### Dictation

`start_dictation(mode, end_on_final_segment)` and `stop_dictation()` map to the engine methods of the same names and are only valid **while a session runs** (phase `running` → `dictating`). Their `ValueError`s are caught and shown as a message. When a dictation with `end_on_final_segment=True` ends by itself, the phase returns to `running`: the controller reads `engine.dictating` in its snapshot.

**These engine methods schedule work on the engine's event loop, so the controller invokes them on the loop thread**, not from the Gradio handler thread.

### `ControllerState`

| Field | Source |
|---|---|
| `phase: str` | `"stopped"` / `"starting"` / `"running"` / `"dictating"` / `"stopping"` / `"listening"` |
| `connected: bool` | `engine.status()["connected"]` (False without a session) |
| `segmentation_mode: str` | `engine.segmentation_mode` while a session runs, else `utterance` |
| `module_type: str` | the session's module, else the saved config's |
| `vad: str` | the session's `engine.vad`, else the saved config's, one line (`format_vad_config`) |
| `voice_activity: str` | `engine.vad_state` and whether a session is capturing (`format_voice_activity`) |
| `utterance_log`, `segment_log: list[str]` | every event formatted, interims included, most recent last (bounded deques) |
| `last_listen: str` | the last `listen` result |
| `message: str` | the last action's result, a blocker list, or an error |
| `dictating: bool` | `engine.dictating` |
| `can_start` / `can_stop` / `can_listen` / `can_dictate: bool` | derived enablement (below) |

## The Listen tab (`app.py`)

Top to bottom: a line saying the session runs the saved config; **Start** and **Stop**; read-only fields for the **session** phase (with the active mode while `dictating`/`listening`), the **module**, and whether the backend is **connected**; the **voice activity detection** gate in one line and what it is doing right now (`SPEECH`, `silence`, `FAILED OPEN`, `idle`, `no VAD`); the **Segmentation mode** dropdown and the "end dictation on first final segment" checkbox beside Start-dictation / Stop-dictation and **Listen (single-shot)**; the **last activity** message; the utterance and segment logs with a **Clear** button; the last listen result.

Start and Stop are greyed by the session's status: a click greys both at once (an unqueued first step), the handler then renders the state it left, and the timer keeps them in step with a session started or stopped from another tab. Live results refresh by polling `controller.state(model)` every 0.25 s (`gr.Timer`), which also lets the voice-activity field show a short utterance. The poll never rewrites the dropdown's value.

| State | Start | Stop | Listen | Dictation |
|---|---|---|---|---|
| stopped | on | off | on | off |
| starting | off | off | off | off |
| running | off | on | off | **Start** on |
| stopping | off | off | off | off |
| dictating | off | on | off | **Stop** on |
| listening | off | **on** (cancels) | off | off |

## Logging

`controller.py` logs through a module logger. As an application entry point, `app.py`'s `main()` configures logging itself (`logging.basicConfig`), not through the library's private `asr_engine._logging`, so the demo depends only on the public library ([project.md](project.md) "Logging").

## Testing

`tests/examples/test_gradio_demo.py` drives `DemoController` over a real app-setup `AppModel` on a temporary folder (its secrets kept off the environment) or opened from the demo's command line (`open_demo`), with `ASREngine` replaced by a fake in-process engine, so no audio device, network or browser is needed:

- the default config is accepted and has no blockers on any machine;
- the page puts Listen first, then Setup and Secrets, and opens on Setup;
- Start builds the engine from the saved section and locks the config (a save is refused) until Stop, which unlocks it;
- a config saved between two sessions is the one the next runs;
- a config with problems says why and starts nothing; an engine that cannot be built is said and leaves nothing locked;
- Listen runs locked, and Stop cancels it and releases the engine; Listen is refused while a session runs;
- the enablement of each phase; a dictation round trip; dictation refused without a session;
- the gate's view without a gate, from the saved config, then following the session's;
- a relative path runs against the app's folder;
- the default config is the committed file, written into a new folder;
- Start and Stop both greyed while the session starts and while it stops;
- a refused start's reasons follow the saved config (a problem fixed and saved leaves the key it names) and the keys (a key saved bumps the store's revision, and nothing is missing any more); a refresh leaves any other message alone;
- the unsaved-changes line: empty for the saved config, said for an edited draft, empty again once it is saved;
- a config saved in the folder is the engine block alone, and the library reads it as it is;
- a config file runs as it is: its session started at launch with its config, the page on Listen, never written (a save refused, the file unchanged); a file that cannot run says its blockers at launch and starts nothing; its keys come from the `.env` next to it; a missing file stops the demo at launch with the reason; the folder launch starts nothing and opens on Setup.

`tests/examples/test_config_app.py`: the config app's page has the Setup and Secrets tabs, and a save writes the engine block alone back to the file, which the library reads; a missing file stops it at launch with the reason.

The page was also run and driven through its own endpoints (gradio's client): on the default config, Start replayed the fake module's script on the microphone's clock (interims, finals, `utterance` segments), and Stop released it. It was then driven in headless Chromium (Playwright): Check on the Setup tab, Start (Start greyed, Stop enabled), the script's finals in the log, Save disabled on the Setup tab while running, Stop (Start enabled, Stop greyed), Save enabled again. No request of the page was refused (no response at 400 or above, no console error). The `StarletteDeprecationWarning: 'HTTP_422_UNPROCESSABLE_ENTITY'` line gradio prints is not a rejection: gradio reads that deprecated constant on every queued event to build its error map; `main` filters it.

## Open questions (deferred)

- **Browser-mic capture**: feed Gradio's streaming-audio component into the engine via a custom `AudioSource`, instead of a server-side device.
- **Starting at launch from a folder**: the demo never started a session at launch from its folder, so it has no autostart (`--start`/`--no-start`); only a config file starts at launch.
- **Releasing a local model at Stop**: the engine has no `close()`; a released engine's local model (kyutai) is freed when it is collected.
