# Plan 14 — Sound Feedback for `listen` Tool

## Goal

Play `feedback_asr_start.wav` when the `listen` tool starts and
`feedback_asr_stop.wav` when it ends. See [specs/sound-feedback.md](../specs/sound-feedback.md).

---

## Tasks

### 1. Package data — declare WAV files in `pyproject.toml`

- [x] Add an `[tool.uv_build]` section (or equivalent) to `pyproject.toml`
  declaring `src/asr_mcp/sounds/*.wav` as package data so the files are
  included in a built distribution.
- [x] Verify the files are already present at
  `src/asr_mcp/sounds/feedback_asr_start.wav` and
  `src/asr_mcp/sounds/feedback_asr_stop.wav`.

---

### 2. Config — `audio.output_device` and `listen.sound_feedback`

- [x] Add `output_device: str | None = None` to `AudioConfig` in `config.py`.
- [x] Add `sound_feedback: bool = True` to `ListenConfig` in `config.py`.
- [x] Parse `audio.output_device` in `load_config()` (same pattern as
  `audio.device`).
- [x] Parse `listen.sound_feedback` in `load_config()`.
- [x] Update `specs/configuration.md` to document both new fields and add
  them to the config example.

---

### 3. `sound_feedback.py` — new module

Create `src/asr_mcp/sound_feedback.py`:

- [x] Module-level `_SOUNDS_DIR` using
  `importlib.resources.files("asr_mcp") / "sounds"`.
- [x] `_play_wav(path, device)` — synchronous helper: opens WAV with `wave`,
  decodes frames to `numpy` array respecting `sampwidth` (1 → `int8`,
  2 → `int16`, 4 → `int32`), calls `sd.play(data, rate, device=device)` then
  `sd.wait()`.
- [x] `SoundFeedback` class:
  - `__init__(self, output_device)` — stores device.
  - `async play_start(self)` — runs `_play_wav(start_path, device)` in
    executor; catches all exceptions, logs at ERROR, does not raise.
  - `async play_stop(self)` — same for stop path.
- [x] `NoOpSoundFeedback` class (or a factory function) — `play_start` and
  `play_stop` are async no-ops; used when `sound_feedback=False`.

---

### 4. Wire into `server.py`

- [x] In `create_mcp_server()`, instantiate `SoundFeedback` or
  `NoOpSoundFeedback` based on `listen_config.sound_feedback`, passing
  `audio_config.output_device` (add `audio_config` parameter to
  `create_mcp_server` or thread the device through `listen_config`).
- [x] In the `listen` tool handler:
  - Call `await sound_feedback.play_start()` before `engine.start()`.
  - Call `await sound_feedback.play_stop()` in the `finally` block, after
    `engine.stop()`.
- [x] Update `run_server()` to pass `config.audio` (or just
  `config.audio.output_device`) to `create_mcp_server()`.

---

### 5. Update `AGENTS.md`

- [x] Add `sound_feedback.py` to the repository layout section.
- [x] Add `sounds/` directory entry under `src/asr_mcp/`.

---

### 6. Unit tests — `tests/test_sound_feedback.py`

- [x] `test_play_start_calls_sounddevice` — mock `sd.play`, `sd.wait`, and
  `wave.open`; assert both are called with the correct arguments.
- [x] `test_play_stop_calls_sounddevice` — same for stop sound.
- [x] `test_output_device_passed_to_sd_play` — assert `device=` kwarg is
  forwarded correctly.
- [x] `test_error_is_logged_not_raised` — make `sd.play` raise; assert no
  exception propagates and the error is logged.
- [x] `test_noop_does_not_call_sounddevice` — `NoOpSoundFeedback.play_start()`
  and `play_stop()` do not call `sd.play`.

---

### 7. Update existing tests

- [x] `tests/test_server.py` — update any `create_mcp_server()` call sites
  that need the new `audio_config` parameter (pass a default `AudioConfig()`).
- [x] `tests/test_config.py` — add assertions for `audio.output_device` and
  `listen.sound_feedback` parsing (defaults and explicit values).

---

### 8. E2E test — update existing listen tests

- [x] In `tests-e2e/test_mcp_tool_client.py`, add `"sound_feedback": true` to
  the `listen_config` dict of all existing listen tests so sound feedback is
  exercised explicitly.
