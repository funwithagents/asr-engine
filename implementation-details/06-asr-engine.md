# Plan 06 — ASR Engine: Implementation Details

## What was implemented

- `ASREngine` class in `src/asr_mcp/engine.py` — validates ASR type, instantiates the module from `REGISTRY`, wires `AudioCapture` to it
- Unit tests in `tests/test_engine.py` covering all public methods, state transitions, and constructor validation

## Deviations from spec

- **`stop()` cancels the asyncio task**: The plan listed `asr_module.stop()` and `audio_capture.stop()` but did not mention cancelling the background task. `stop()` also cancels and awaits `self._task` to ensure clean shutdown.

## Deviations added post-plan-06

- **Constructor takes `ASRConfig`, not `ASRModule`**: The original plan passed a pre-built `ASRModule` instance. The constructor now takes `ASRConfig` (containing `type` and `extra`) and owns validation + instantiation itself. This keeps all ASR lifecycle logic inside the engine and removes that responsibility from callers (`run_server`, `cli`).

## Non-obvious decisions

- **`_handle_result` is async**: `ResultCallback` is typed as `Callable[[ASRResult], Awaitable[None]]`, so `_handle_result` must be `async` to match and be passable as the callback.

- **`set_connected` is intentionally sync**: Connection state changes originate from the ASR module (network callbacks); making it sync avoids scheduling issues and is safe since `_connected` is a plain bool.

## Known limitations

- `stop()` calls `asr_module.stop()` before cancelling the task. If the module's `stop()` hangs, the task cancel never happens. This is acceptable since well-behaved modules are expected to return promptly from `stop()`.
