# Plan 06 — ASR Engine: Implementation Details

## What was implemented

- `ASREngine` class in `src/asr_mcp/engine.py` wiring `AudioCapture` to an `ASRModule`
- `pause()` and `resume()` methods added to `AudioCapture` in `audio.py`
- 15 unit tests in `tests/test_engine.py` covering all public methods and state transitions

## Deviations from spec

- **`AudioCapture.pause()/resume()` added**: The plan said "stop audio capture stream" for pause and "restart audio capture stream" for resume. `AudioCapture.stop()` closes the stream (destroying the queue reference), which would break the ASR module task still reading from the old queue. Instead, `pause()` calls `self._stream.stop()` (pauses capture without closing) and `resume()` calls `self._stream.start()`. The queue reference is preserved across the pause/resume cycle.

- **`stop()` cancels the asyncio task**: The plan listed `asr_module.stop()` and `audio_capture.stop()` but did not mention cancelling the background task. `stop()` also cancels and awaits `self._task` to ensure clean shutdown.

## Non-obvious decisions

- **Pause operates on the sounddevice stream, not the queue**: By stopping the stream rather than closing it, the same `asyncio.Queue` object stays valid. The ASR module task keeps running but simply receives no new chunks (it will hit its internal timeout and send KeepAlive messages, which is harmless).

- **`_handle_result` is async**: `ResultCallback` is typed as `Callable[[ASRResult], Awaitable[None]]`, so `_handle_result` must be `async` to match and be passable as the callback.

- **`set_connected` is intentionally sync**: Connection state changes originate from the ASR module (network callbacks); making it sync avoids scheduling issues and is safe since `_connected` is a plain bool.

## Known limitations

- No thread-safety: `pause()`/`resume()`/`set_connected()` are sync and called from async context; concurrent calls from multiple coroutines could race. Acceptable for the current single-event-loop design.
- `stop()` calls `asr_module.stop()` before cancelling the task. If the module's `stop()` hangs, the task cancel never happens. This is acceptable since well-behaved modules are expected to return promptly from `stop()`.
