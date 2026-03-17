# Plan 06 — ASR Engine

Implement the engine that wires audio capture to the ASR module and manages pause/resume state.

## Tasks

- [ ] Implement `ASREngine` class in `engine.py`:
  - Constructor accepts `audio_config: AudioConfig`, `asr_module: ASRModule`, `on_result: ResultCallback`
  - Internal state: `_paused: bool`, `_running: bool`, `_connected: bool`

- [ ] Implement `ASREngine.start()`:
  - Instantiate `AudioCapture` with `audio_config.device`
  - Start audio capture, get the audio queue
  - Launch `asr_module.start(audio_queue, self._handle_result)` as an asyncio task
  - Set `_running = True`

- [ ] Implement `ASREngine.stop()`:
  - Call `asr_module.stop()`
  - Call `audio_capture.stop()`
  - Set `_running = False`

- [ ] Implement `ASREngine.pause()`:
  - If already paused, raise `RuntimeError("already paused")`
  - Stop audio capture stream (stop feeding the queue)
  - Set `_paused = True`

- [ ] Implement `ASREngine.resume()`:
  - If not paused, raise `RuntimeError("not paused")`
  - Restart audio capture stream
  - Set `_paused = False`

- [ ] Implement `ASREngine.status() -> dict`:
  - Returns `{"running": bool, "paused": bool, "connected": bool}`

- [ ] Implement `ASREngine._handle_result(result: ASRResult)`:
  - If paused, discard the result silently
  - Otherwise, forward to the `on_result` callback provided at construction

- [ ] Implement connection state tracking:
  - Expose a method `set_connected(state: bool)` that the ASR module calls on connect/disconnect
  - Update `_connected` accordingly

- [ ] Manual test: instantiate the engine, start it, verify results flow through the callback; test pause/resume cycle
