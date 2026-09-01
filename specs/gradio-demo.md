---
code:
  - examples/gradio_demo/controller.py
  - examples/gradio_demo/app.py
tests:
---

# Gradio Demo App

**Status:** Implemented

> **Dictation update (2026-09-01):** the always-on `segmentation_mode` is gone,
> so the demo's live segmentation-mode selector is replaced by **dictation
> controls** (a mode picker + an "end on final segment" toggle + Start/Stop
> dictation) that call the engine's `start_dictation` / `stop_dictation` on the
> running engine. The controller drops `set_segmentation_mode`, gains
> `start_dictation` / `stop_dictation`, and its phase gains `"dictating"`. To be
> implemented by
> [plans/202609012010_dictation-sessions.md](../plans/202609012010_dictation-sessions.md).

## Purpose

A small, standalone **example** that drives the `ASREngine` from a Gradio web UI,
so a person can exercise the whole engine — pick an input device and ASR module,
start/stop always-on capture, run a single-shot `listen`, start/stop a dictation
session, and watch utterances and segments stream in — without writing code or
touching the MCP server.

It is a **direct-import consumer** of the library: it builds an `ASREngine` from
an `ASREngineConfig` in-process and calls the engine API directly. It is **not**
an MCP client and does **not** go through `AsrTools` — those exist for the MCP
server and for handing pre-built tool implementations to an agent, not for an
in-process caller (see [tools.md](tools.md) and the note there). This demo is the
worked example of the "Direct import (no MCP)" usage in [engine.md](engine.md).

It lives in `examples/`, outside the `asr_engine` package: a new top-level
directory for runnable examples that depend on the library but are not part of
it. It is intentionally kept thin, with all non-UI logic in a UI-agnostic
controller so the behavior is testable without Gradio.

## Non-goals

- **Not** an MCP client — see [demo-client.md](demo-client.md) for that.
- **No** browser-microphone capture. Audio comes from a server-side input device
  via the engine's normal `AudioCapture`; feeding Gradio's streaming-audio
  component through a custom `AudioSource` is a deferred idea (Open questions).
- **No** editing of module-specific fields (model, language, API-key env name)
  from the UI — the module *type* is chosen in the UI; its other fields come from
  the loaded config file. (Segmentation *mode* **and** params — trigger words and
  timeouts — are editable in the UI.)
- Not shipped as a core dependency: Gradio is an optional dependency group.

## Layout

```
examples/
  gradio_demo/
    __init__.py
    controller.py   # UI-agnostic DemoController: owns the engine + event state
    app.py          # thin Gradio wiring + entry point (main)
```

- `controller.py` imports `asr_engine`, never `gradio`. It is the testable unit.
- `app.py` imports both; it builds the Gradio `Blocks`, binds widgets to
  controller methods, and polls the controller for display state. It contains no
  segmentation/engine logic of its own.

## Concurrency model

The `ASREngine` is async and expects a running event loop; Gradio callbacks are
synchronous. The controller owns a **dedicated asyncio event loop running in a
background thread** and marshals every engine call onto it with
`asyncio.run_coroutine_threadsafe(...)`, blocking the calling Gradio handler
until it completes (with a timeout). Engine callbacks (`on_speech_utterance`,
`on_speech_segment`) fire on that loop thread; the controller appends to
thread-safe buffers that the UI reads. This keeps the engine on one loop
(the "asyncio throughout" invariant) while Gradio stays synchronous.

## `DemoController`

The UI-agnostic core. Holds the loop thread, the current `ASREngineConfig`, at
most one live `ASREngine`, the current segmentation mode, and rolling event
state.

```python
class DemoController:
    def __init__(self, base_config: ASREngineConfig) -> None: ...

    # --- configuration (only while stopped) ---
    def available_devices(self) -> list[str]:        # AudioCapture.list_devices()
    def available_modules(self) -> list[str]:        # sorted(REGISTRY)
    def set_device(self, device: str | None) -> None
    def set_module(self, module_type: str) -> None

    # --- lifecycle ---
    def start(self) -> None                          # build engine if needed, engine.start()
    def stop(self) -> None                           # engine.stop(), or cancel an in-progress listen
    def listen(self, mode: str | None = None) -> None  # single-shot, background + cancelable

    # --- dictation (marshaled onto the loop thread; engine must be running) ---
    def start_dictation(                             # engine.start_dictation
        self, mode: str | None = None, end_on_final_segment: bool = False,
    ) -> None
    def stop_dictation(self) -> None                 # engine.stop_dictation

    # --- segmentation params (marshaled onto the loop thread) ---
    def set_segmentation_params(                     # engine.set_segmentation_params
        self, trigger_words: str, initial_silence_timeout_s: float,
        end_of_speech_timeout_s: float,
    ) -> None

    # --- results ---
    def clear_logs(self) -> None                     # empty the utterance/segment logs

    # --- state for the UI ---
    def state(self) -> ControllerState               # snapshot: see below
```

### Rebuild-on-config-change

The engine is built from a fixed `config.module` / `config.audio.device`, with no
runtime module- or device-switch API. So:

- `set_device` and `set_module` mutate the controller's pending
  `ASREngineConfig` and **discard any existing engine instance**. They raise
  `RuntimeError` (surfaced as a UI message) if called while the engine is
  running — the UI also disables those widgets while running.
- The next `start()` (or `listen()`) constructs a fresh `ASREngine` from the
  current config. Module-specific fields (e.g. Deepgram's `api_key_env`,
  `model`, `language`) come from the loaded base config; switching `module.type`
  keeps the rest of the `module` block only if compatible, otherwise falls back
  to that module's own config defaults. Construction errors (unknown module,
  unsupported audio format, missing API key) are caught and shown in the UI, not
  raised past the handler.

### `listen`

`listen` requires the engine **not** running (engine raises `ValueError`
otherwise). It is **non-blocking**: it schedules `engine.listen(mode)` as a task
on the loop thread and returns immediately, so the UI stays responsive. The
controller enters the `listening` phase; when the capture completes, its closed
`SpeechSegment` is stored as `last_listen` and the phase returns to `stopped`.
`mode` is the value of the shared **Segmentation mode** dropdown (any of the
three modes, `utterance` included); when the UI passes `None` the engine falls
back to its `listen_default_segmentation_mode`.

A listen can be **cancelled**: `stop()` cancels the listen task, which unwinds
`engine.listen`'s own cleanup (it stops the engine and restores the prior mode);
the phase then returns to `stopped` with a "Listen cancelled." message. This is
why Stop is enabled during `listening` (see the enablement table).

### Dictation control

`start_dictation(mode, end_on_final_segment)` and `stop_dictation()` map to the
engine methods of the same names and are only valid **while the engine is
running** (phase `running` → `dictating`). `start_dictation` resolves `mode`
against the config's `dictation_default_segmentation_mode` when `None`; the demo
defaults `end_on_final_segment` to `False` so a dictation runs until Stop
dictation (the natural "watch it aggregate" demo). Their `ValueError`s (not
running, already/not dictating, unknown mode) are caught and shown as a message.
When a dictation with `end_on_final_segment=True` self-ends, the phase returns to
`running` — the controller reads `engine.dictating` in its snapshot so the UI
follows the engine.

`set_segmentation_params(trigger_words, initial_silence_timeout_s,
end_of_speech_timeout_s)` still changes the shared segmentation params live (it
maps to the engine method of the same name, safe while running). `trigger_words`
is a comma-separated string, split into a list; timeouts must be numbers greater
than 0 (rejected with a message otherwise). It persists to the pending config so
a not-yet-built engine and the state snapshot reflect it.

**These engine methods schedule work on the engine's event loop, so the
controller must invoke them on the loop thread** (via the same
`run_coroutine_threadsafe` helper), not directly from the Gradio handler thread —
calling them off-loop raises `RuntimeError: no current event loop`.

### `ControllerState`

A plain snapshot the UI renders. Fields:

| Field | Source |
|---|---|
| `phase: str` | `"stopped"` / `"running"` / `"dictating"` / `"listening"` — the primary status |
| `connected: bool` | `engine.status()["connected"]` (False if no engine) |
| `segmentation_mode: str` | `engine.segmentation_mode` (read-only property; see [engine.md](engine.md)) — the dictation/listen mode while active, else `utterance` |
| `trigger_words: str` | pending config's segmentation trigger words, comma-joined (for the textbox) |
| `initial_silence_timeout_s`, `end_of_speech_timeout_s: float` | pending config's segmentation timeouts |
| `device: str \| None`, `module_type: str` | current pending config |
| `utterance_log: list[str]` | **every** utterance formatted (interim + final), most-recent last (bounded deque) |
| `segment_log: list[str]` | **every** segment formatted (interim `[open]` + closed `[end_reason]`), most-recent last (bounded deque) |
| `last_listen: str` | formatted result of the last `listen` |
| `message: str` | last action/error message for the UI |
| `can_start` / `can_stop` / `can_listen` / `config_enabled` / `can_dictate: bool` | derived enablement (see table); `can_dictate` gates the dictation controls |

The current segmentation mode is read from the engine's read-only
`segmentation_mode` property (see [engine.md](engine.md)), so the controller
doesn't shadow engine state. When there is no live engine (not yet started after a
config change), the snapshot reports `"utterance"` (the at-rest mode).

## UI (`app.py`)

A single Gradio `Blocks` page, top to bottom:

- **Config row** (disabled while running): input-device dropdown
  (`available_devices()`, defaulting to the **first** device rather than the
  system default), module dropdown (`available_modules()`).
- **Params row**: a trigger-words textbox (comma-separated), two timeout number
  inputs, and an "Apply params" button calling `set_segmentation_params`. The
  timer only toggles these inputs' *enablement* — it never rewrites their value,
  so it can't clobber what the user is typing; the current values seed them at
  build time.
- **Engine row**: Start and Stop buttons (always-on capture).
- **State row**: read-only fields for the engine **phase** (`stopped` /
  `running` / `dictating` / `listening`) and backend connection. Because the mode
  dropdown is a free input (not a mirror of engine state), the phase field shows
  the **active** mode next to the phase while `dictating`/`listening` (e.g.
  `dictating · trigger_word`), so the user still sees what is running.
- **Segmentation + actions row** (two columns): on the **left**, a **Segmentation
  mode** dropdown (`utterance` / `trigger_word` / `timeout`) beside an "end
  dictation on first final segment" checkbox (two columns on one line); on the
  **right**, Start-dictation /
  Stop-dictation buttons on the first line and the Listen (single-shot) button on
  the second. The dropdown is a free user choice that drives **both** `listen` (the
  mode passed to `controller.listen`) and `start_dictation`; it stays editable in
  every phase and the poll timer **never** overwrites its value (that would clobber
  the selection — same rule as the params inputs). Start-dictation is enabled while
  `running` and not yet dictating; Stop-dictation while `dictating`.
- **Last activity**: a read-only line carrying the controller's message (result
  of the most recent action or an error).
- **Results**: two scrolling, read-only log panels — utterances and segments —
  each showing **every** event including interims (autoscrolled to the newest, so
  the last line is the latest), a **Clear** button beneath them
  (`clear_logs`), plus the last `listen` result. There are no separate "latest"
  single-line boxes; the log's tail is the latest.

Live results refresh by polling `controller.state()` on a timer (Gradio's
`gr.Timer` / periodic tick), since engine callbacks arrive on the loop thread and
the demo needs no lossless stream — a rolling snapshot matches the engine's own
rolling-resource semantics. Interim events are frequent, so each log is a bounded
deque (latest N lines).

### Button / widget enablement

| State | Start | Stop | Listen | Device / Module | Dictation | Params |
|---|---|---|---|---|---|---|
| stopped | on | off | on | on | off | on |
| running (always-on) | off | on | off | off | **Start** on | on |
| dictating | off | on | off | off | **Stop** on | on |
| listening (single-shot) | off | **on** (cancels listen) | off | off | off | off |

`can_dictate` is true in the `running` and `dictating` phases; within the
dictation controls, Start-dictation is enabled while `running` and
Stop-dictation while `dictating`. The **Segmentation mode** dropdown is **always
editable** (it is a free input for Listen and Start-dictation, not gated by
phase), and the params inputs are editable in every phase except `listening`.

Enablement is derived from `ControllerState`; the controller is the single
source of truth.

## Configuration

The app takes a `--config` path to the same JSON schema the MCP server uses (see
[configuration.md](configuration.md)); only the `engine` block is used (the
`server` block is ignored). It builds the base `ASREngineConfig` from that block.
With no `--config`, it falls back to a minimal built-in default (a `deepgram_v1`
module reading `DEEPGRAM_API_KEY` via `api_key_env`, system default device).

## Dependencies and running

Gradio lives in the `demo` dependency group, kept out of `[project.dependencies]`
so `import asr_engine` and the server stay lean and library consumers never pull
it. Because pyright type-checks `examples/`, `demo` is a **default sync group**
(`[tool.uv] default-groups = ["dev", "demo"]`), so the dev venv always has gradio
and `uv run pyright` / Pylance resolve it. Run the demo with:

```bash
uv run python -m examples.gradio_demo.app --config config.json
```

The examples directory is not built into the wheel (`[tool.uv_build].include`
covers only the package + sounds).

## Testing

This is an **example**, not library code: it has **no automated tests** and is
not collected by the fast tier (nothing under `examples/` is on `testpaths`).
Verification is manual — run the command above with a real input device and a
provider API key, then exercise start/stop/listen and switch segmentation mode.

The `controller.py` / `app.py` split still stands on its own merit — it keeps all
engine logic out of the Gradio callbacks and makes the demo readable — but the
controller is not pinned by tests. The engine behavior the demo relies on (the
`segmentation_mode` / `dictating` getters, `start_dictation` / `stop_dictation`,
`listen`) is covered by the engine's own tests in `tests/test_engine.py`.

## Open questions (deferred)

- **Browser-mic capture**: feed Gradio's streaming-audio component into the
  engine via a custom `AudioSource` adapter (16 kHz / s16 chunking), instead of a
  server-side device. Bigger lift; deferred.
- **Per-module config editing**: edit module-specific fields (model, language,
  API-key env name) in the UI rather than only the module `type`.
