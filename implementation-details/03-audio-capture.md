# Implementation Details — 03 Audio Capture

## What was implemented

`AudioCapture` in [src/asr_mcp/audio.py](../src/asr_mcp/audio.py):

- Constructor takes `device: str | None` and `loop: asyncio.AbstractEventLoop`
- `start()` validates the device name (if given), creates an `asyncio.Queue[bytes]`, opens a `sounddevice.InputStream` with the spec parameters (16 kHz, mono, int16, 1600-sample blocks), and returns the queue
- The `sounddevice` callback converts the numpy array to `bytes` and enqueues it thread-safely with `loop.call_soon_threadsafe(queue.put_nowait, chunk)`
- `stop()` calls `.stop()` + `.close()` on the stream and clears the reference; idempotent (safe to call when already stopped)
- `list_devices()` is a `@staticmethod` that filters `sd.query_devices()` to entries with `max_input_channels > 0`

15 unit tests in [tests/test_audio.py](../tests/test_audio.py) covering: device listing, stream parameter correctness, device validation, stop idempotency, and callback byte delivery.

## Deviations from spec

None. All tasks were implemented as specified.

The manual-test checkbox (instantiate and print chunk sizes) was left unchecked — it describes an ad-hoc developer exercise and is not automatable without real hardware.

## Non-obvious decisions

**PortAudio not available in CI**: `sounddevice` tries to load the PortAudio shared library at import time and raises `OSError` if it is missing. Tests mock `sounddevice` via `sys.modules.setdefault("sounddevice", MagicMock())` in `conftest.py` before any test module imports `audio.py`. This makes the entire test suite runnable without audio hardware or OS audio libraries.

**`bytes(indata)` for conversion**: `indata` is a `(blocksize, channels)` numpy array of `int16`. Calling `bytes()` on it produces the raw little-endian PCM bytes that ASR backends expect, equivalent to `indata.tobytes()` but slightly more concise.

**No queue size cap**: The queue is unbounded. If the ASR module falls behind, audio chunks will accumulate in memory. This is acceptable for the current stage; back-pressure or dropping can be added later if needed.

## Known limitations

- Device validation only checks by name string. If the config provides a numeric index as a string (e.g. `"0"`), it will raise a `ValueError` even though sounddevice would accept it. Numeric-index support is deferred.
- No reconnection logic if the audio device is unplugged mid-session. The stream will error and the queue will stop receiving data silently.
