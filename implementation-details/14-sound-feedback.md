# Implementation Details — Plan 14: Sound Feedback

## What was implemented

- `src/asr_mcp/sound_feedback.py`: `SoundFeedback` and `NoOpSoundFeedback` classes.
- `AudioConfig.output_device` and `ListenConfig.sound_feedback` config fields.
- `create_mcp_server()` gains an optional `audio_config` parameter; instantiates `SoundFeedback` or `NoOpSoundFeedback` based on `listen_config.sound_feedback`.
- `listen` tool calls `play_start()` before `engine.start()` and `play_stop()` in the `finally` block after `engine.stop()`.
- `pyproject.toml` declares `src/asr_mcp/sounds/*.wav` as package data under `[tool.uv_build]`.
- Unit tests in `tests/test_sound_feedback.py`; `listen` tests in `test_server.py` use `sound_feedback=False` to stay fast.
- All three e2e listen tests in `test_mcp_tool_client.py` now set `"sound_feedback": true` explicitly.

## Deviations from spec

None.

## Non-obvious decisions

- **`sound_feedback=False` in unit test helper**: `_listen_config()` in `test_server.py` defaults to `sound_feedback=False` so existing listen tests don't attempt real audio playback. The `SoundFeedback` class is covered by its own dedicated test file with mocked `sd` and `wave`.

- **`audio_config` optional in `create_mcp_server`**: kept optional (defaults to `AudioConfig()`) to avoid breaking existing call sites and tests that only pass `engine` and `listen_config`.

- **`_play_wav` uses `str(path)`**: `wave.open()` receives `str(path)` where `path` is a `Traversable` from `importlib.resources`. This works correctly for both in-place (`uv run`) and installed package layouts where the sounds directory is on the real filesystem.

## Known limitations

- Audio playback on headless/CI runners will fail silently (the exception is caught and logged at ERROR). This is intentional per spec — sound failures must never abort a `listen` call.
