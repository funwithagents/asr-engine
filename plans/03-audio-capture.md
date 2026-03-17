# Plan 03 — Audio Capture

Implement the audio capture layer that reads from a system audio input device and feeds raw PCM chunks into an `asyncio.Queue`.

## Tasks

- [ ] Implement `AudioCapture` class in `audio.py`:
  - Constructor accepts `device: str | None` and `loop: asyncio.AbstractEventLoop`
  - Internally uses `sounddevice.InputStream` with:
    - `samplerate=16000`
    - `channels=1`
    - `dtype='int16'`
    - chunk size of 1600 samples (~100ms)
  - Audio callback puts chunks onto an `asyncio.Queue[bytes]` thread-safely via `loop.call_soon_threadsafe`
- [ ] Implement `AudioCapture.start() -> asyncio.Queue[bytes]`:
  - Opens the stream and starts it
  - Returns the queue for consumers to read from
- [ ] Implement `AudioCapture.stop()`:
  - Stops and closes the stream cleanly
- [ ] Implement `AudioCapture.list_devices() -> list[str]`:
  - Static/class method that returns available input device names (for debugging / config help)
- [ ] Handle invalid device name/index at `start()` time:
  - Raise `ValueError` with clear message listing available devices
- [ ] Manual test: instantiate `AudioCapture`, start it, read a few chunks from the queue and print chunk sizes to verify correct framing
