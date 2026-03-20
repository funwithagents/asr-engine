# Sound Feedback Specification

## Purpose

Play short audio cues when the `listen` tool starts and stops, so the user
gets an immediate physical signal that speech recognition is active or has
ended — without having to look at a screen.

## Scope

Sound feedback applies **only to the `listen` tool**. The `start` and `stop`
tools are not affected.

## Audio files

Two WAV files are bundled inside the package at:

```
src/asr_mcp/sounds/feedback_asr_start.wav   # played when listen begins
src/asr_mcp/sounds/feedback_asr_stop.wav    # played when listen ends
```

They are resolved at runtime via `importlib.resources.files("asr_mcp") /
"sounds" / filename`, so they work correctly whether the package is installed
or run in-place with `uv run`.

The files must be declared as package data in `pyproject.toml` so they are
included in a built distribution.

## Playback

- Played using `sounddevice` (already a project dependency).
- WAV data is read with Python's standard-library `wave` module, decoded to a
  `numpy` array, and passed to `sd.play()`.
- `sd.wait()` is called after `sd.play()` to block until playback finishes.
  Because this is synchronous, playback runs in a thread pool via
  `asyncio.get_event_loop().run_in_executor(None, ...)`.
- The output device is taken from `audio.output_device` in config (`None` =
  system default, passed directly to `sd.play(device=...)`).

## Timing within `listen`

```
listen called
  └─ play start sound     ← feedback_asr_start.wav (awaited)
  └─ engine.start()
  └─ collect until end condition
  └─ engine.stop()
  └─ play stop sound      ← feedback_asr_stop.wav (awaited)
  └─ return {transcript, end_reason}
```

The start sound plays **before** `engine.start()` so the user hears it before
any processing delay. The stop sound plays **after** `engine.stop()` so it
signals that the session is fully closed.

Both sounds are played even if the session ends with an error (try/finally).

## Error handling

If playback fails for any reason (device unavailable, file missing, etc.), the
error is logged at `ERROR` level and the `listen` session continues or returns
normally. Sound failures must never abort a `listen` call.

## Configuration

### `listen` block — new field

| Field | Type | Default | Description |
|---|---|---|---|
| `sound_feedback` | boolean | `true` | Play start/stop audio cues during `listen`. Set to `false` to disable. |

### `audio` block — new field

| Field | Type | Default | Description |
|---|---|---|---|
| `output_device` | string or null | `null` | Audio output device name or index for sound feedback playback. `null` = system default. Same semantics as `audio.device` for input. |

### Updated config example

```json
{
  "audio": {
    "device": null,
    "output_device": null
  },
  "listen": {
    "end_of_utterance_mode": "trigger_word",
    "sound_feedback": true
  }
}
```

## New module: `sound_feedback.py`

```python
class SoundFeedback:
    def __init__(self, output_device: str | int | None = None) -> None: ...

    async def play_start(self) -> None:
        """Play feedback_asr_start.wav. Log and continue on error."""

    async def play_stop(self) -> None:
        """Play feedback_asr_stop.wav. Log and continue on error."""
```

A module-level `_SOUNDS_DIR` constant resolves the bundled sounds directory
once at import time.

`SoundFeedback` is instantiated in `create_mcp_server()` when
`listen_config.sound_feedback` is `True`; a no-op stub is used otherwise so
the `listen` tool calls `play_start` / `play_stop` unconditionally.

## Unit testing

- `play_start` / `play_stop` call `sd.play` and `sd.wait` with the correct
  file data and output device.
- When `sd.play` raises, the error is logged and no exception propagates.
- The no-op stub variant makes no calls to `sounddevice`.
- Tests mock `sounddevice.play`, `sounddevice.wait`, and the `wave` module;
  no real audio device is required.
