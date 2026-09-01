---
code:
  - src/asr_engine/tools.py
tests:
  - tests/test_tools.py
---

# Tools Layer Specification

**Status:** Implemented

> **Dictation update (2026-09-01):** `AsrTools` gains `start_dictation` /
> `stop_dictation` and the two default-mode setters
> (`set_dictation_default_segmentation_mode`,
> `set_listen_default_segmentation_mode`), thin pass-throughs to the engine so the
> MCP server can expose them. Implemented by
> [plans/202609012010_dictation-sessions.md](../plans/202609012010_dictation-sessions.md).

> **New in the 2026-08-31 refactor**, implemented by
> [plans/202608311612_asr-engine-refactor.md](../plans/202608311612_asr-engine-refactor.md).

## Purpose

Define the ASR control operations **once**, independent of any transport, so
they can be:

- called directly by a Python program that `import asr_engine` and holds an
  `ASREngine`, and
- wrapped, unchanged, by the MCP server (see [mcp-server.md](mcp-server.md)).

Before this layer, `start` / `stop` / `is_running` / `listen` lived inline in the
FastMCP server, entangling the control logic (the listen lock, progress
reporting) with MCP's `Context`. `AsrTools` extracts that logic so the MCP tools
are thin adapters and direct importers get the same behaviour.

## `AsrTools`

```python
ProgressCallback = Callable[[float, float | None, str | None], Awaitable[None]]


class AsrTools:
    def __init__(self, engine: ASREngine) -> None: ...

    async def start(self) -> dict: ...  # {"status": "running"}
    async def stop(self) -> dict: ...  # {"status": "stopped"}
    def is_running(self) -> dict: ...  # engine.status()

    async def listen(
        self,
        mode: str | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> dict: ...  # {"transcript", "end_reason"}

    async def start_dictation(
        self,
        end_on_final_segment: bool = True,
        segmentation_mode: str | None = None,
    ) -> dict: ...  # {"status": "dictating", "mode", "end_on_final_segment"}
    async def stop_dictation(self) -> dict: ...  # {"status": "dictation_stopped"}
    def is_dictation_running(self) -> dict: ...  # {"dictating", "segmentation_mode"}
    def set_dictation_default_segmentation_mode(self, mode: str) -> dict: ...
    def set_listen_default_segmentation_mode(self, mode: str) -> dict: ...
```

`AsrTools` holds a reference to one `ASREngine`. It knows nothing about FastMCP,
`Context`, HTTP, or resources.

### `start` / `stop` / `is_running`

Thin pass-throughs to the engine:

- `start()` → `await engine.start()`, returns `{"status": "running"}`.
- `stop()` → `await engine.stop()`, returns `{"status": "stopped"}`.
- `is_running()` → returns `engine.status()` (`{"running": bool, "connected": bool}`).

### `listen`

Wraps `engine.listen` with the concurrency guard and progress translation that
used to live in the server:

1. Raise `ValueError("A listen session is already in progress.")` if a `listen`
   is already running on this `AsrTools` (an internal `asyncio.Lock`).
2. Raise `ValueError("ASR is already running. Stop it before calling listen.")`
   if `engine.status()["running"]` (the engine also enforces this; the tool
   checks first to give the clear message before acquiring anything).
3. Call `engine.listen(mode, on_update=_relay)`. The `mode` is passed straight
   through, so `None` uses the engine's `listen_default_segmentation_mode`.
   `listen` does not accept or forward segmentation params — those stay as
   configured (see [engine.md](engine.md)).
4. `_relay` reports progress only when the committed-final count grows
   (`len(segment.utterances)` increases), calling
   `on_progress(progress=committed, total=None, message=segment.transcript)`.
   Interim updates and the trigger-word utterance produce no progress call.
5. Return `{"transcript": segment.transcript, "end_reason": segment.end_reason}`.

Sound cues are played by `engine.listen` itself; `AsrTools` does not touch sound
feedback.

### `start_dictation` / `stop_dictation`

Thin pass-throughs to the engine's dictation primitives (see
[engine.md](engine.md)), which own the state and the `ValueError`s (engine not
running, already/not dictating, unknown mode):

- `start_dictation(end_on_final_segment=True, segmentation_mode=None)` →
  `await engine.start_dictation(...)`, returns
  `{"status": "dictating", "mode": <resolved mode>, "end_on_final_segment": <bool>}`.
- `stop_dictation()` → `await engine.stop_dictation()`, returns
  `{"status": "dictation_stopped"}` (the engine stays running).
- `is_dictation_running()` → `{"dictating": engine.dictating, "segmentation_mode":
  engine.segmentation_mode}` — the dictation counterpart to `is_running`,
  composing the engine's `dictating` property and `segmentation_mode` getter (the
  active dictation/`listen` mode, else `utterance`). Never raises.

Unlike `listen`, these are non-blocking: dictation runs on the always-on engine
and its segments reach consumers through the normal `on_speech_segment` callback
/ `asr://segment` resource, not through a return value.

### `set_dictation_default_segmentation_mode` / `set_listen_default_segmentation_mode`

Pass-throughs to the engine's setters of the same names; each returns the updated
value, e.g. `{"dictation_default_segmentation_mode": "timeout"}` /
`{"listen_default_segmentation_mode": "utterance"}`. They validate the mode (the
engine raises `ValueError` on an unknown one) and do not change the current mode.

## MCP adapter

The MCP server (see [mcp-server.md](mcp-server.md)) constructs one
`AsrTools(engine)` and registers all nine methods as MCP tools through thin
adapters. The `listen` tool passes an `on_progress` that forwards to
`ctx.report_progress(progress, total, message)`; when the client sends no
`progressToken`, `ctx.report_progress` is a no-op and the tool still returns
normally.

An in-process agent can instead register the bound `AsrTools` methods directly
with its own tool mechanism. Their signatures, docstrings, validation, and
return shapes provide a ready-made agent tool contract, avoiding another layer
of wrappers and descriptions derived from the lower-level engine API. The
`on_progress` parameter of `listen` is a host integration hook and must be
omitted or injected by the framework rather than exposed as a model-supplied
argument.

## Testing

Drive `AsrTools` against a fake/stub `ASREngine` (no real audio or network):

- `start` / `stop` / `is_running` return the documented dicts and call the
  matching engine methods.
- `listen` returns `{"transcript", "end_reason"}` from the segment the fake
  engine closes with, and passes `mode` through unchanged (including `None`).
- Progress: `on_progress` fires once per newly committed final with the
  space-joined transcript, and not at all for interim updates.
- The in-progress lock: a second concurrent `listen` raises
  `"A listen session is already in progress."`; a `listen` while the engine is
  already running raises the "ASR is already running" message.
