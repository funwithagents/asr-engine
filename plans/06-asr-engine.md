# Plan 06 — ASR Engine

Implement the engine that wires audio capture to the ASR module and manages start/stop state.

## Tasks

- [x] Implement `ASREngine` class in `engine.py`:
  - Constructor accepts `audio_config: AudioConfig`, `asr_config: ASRConfig`, `on_result: ResultCallback`
  - Validates `asr_config.type` against `REGISTRY`; raises `ValueError` if unknown
  - Instantiates the ASR module from `REGISTRY` using `asr_config.extra` as config
  - Internal state: `_running: bool`, `_connected: bool`

- [x] Implement `ASREngine.start()`:
  - Instantiate `AudioCapture` with `audio_config.device`
  - Start audio capture, get the audio queue
  - Launch `asr_module.start(audio_queue, self._handle_result)` as an asyncio task
  - Set `_running = True`

- [x] Implement `ASREngine.stop()`:
  - Call `asr_module.stop()`
  - Call `audio_capture.stop()`
  - Set `_running = False`

- [x] Implement `ASREngine.status() -> dict`:
  - Returns `{"running": bool, "connected": bool}`

- [x] Implement `ASREngine._handle_result(result: ASRResult)`:
  - Forward to the `on_result` callback provided at construction

- [x] Implement connection state tracking:
  - Expose a method `set_connected(state: bool)` that the ASR module calls on connect/disconnect
  - Update `_connected` accordingly

- [x] Manual test: instantiate the engine, start it, verify results flow through the callback; test start/stop cycle
