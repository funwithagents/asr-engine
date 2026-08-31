---
code:
  - src/asr_engine/tools.py
tests:
  - tests/test_tools.py
---

# Tools Layer Specification

**Status:** Implemented

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

## MCP adapter

The MCP server (see [mcp-server.md](mcp-server.md)) constructs one
`AsrTools(engine)` and registers four MCP tools that call the corresponding
methods. The `listen` tool passes an `on_progress` that forwards to
`ctx.report_progress(progress, total, message)`; when the client sends no
`progressToken`, `ctx.report_progress` is a no-op and the tool still returns
normally.

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
