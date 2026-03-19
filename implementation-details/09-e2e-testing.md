# Plan 09 — E2E Testing: Implementation Notes

## What was implemented

- `AudioSource` protocol (`typing.Protocol`) in `audio.py` formalising the
  `start / stop / pause / resume` interface shared by `AudioCapture` and the new
  `FileAudioSource`.
- `FileAudioSource` in `audio.py`: reads a WAV file at real-time rate (one
  `chunk_samples` frame every `chunk_duration` seconds) and optionally appends
  silence frames (`trailing_silence_s` parameter).
- `ASREngine` now accepts an optional `audio_source: AudioSource | None = None`
  constructor parameter. When provided it is used in `start()` instead of
  constructing `AudioCapture`.
- `tests-e2e/__init__.py` and `tests-e2e/fixtures/README.md` documenting the WAV
  fixture requirements.
- `tests-e2e/test_asr_resource_client.py` with `test_e2e_deepgram_v1` and
  `test_e2e_deepgram_v2`, each wiring a `FileAudioSource` → `ASREngine` →
  in-process uvicorn MCP server → `AsrResourceClient` → transcript assertion.

## Deviations from spec

- `FileAudioSource` gained a `trailing_silence_s: float = 0.0` parameter not
  present in the original spec. This was required for `test_e2e_deepgram_v2`
  (see below).
- `_run_e2e` gained a matching `trailing_silence_s` argument.

## Non-obvious decisions

### `trailing_silence_s` in `FileAudioSource`

Deepgram v2 (Flux) fires `EndOfTurn` only after detecting silence in the audio
stream. When the WAV file finishes, `FileAudioSource` stops enqueuing chunks and
`_audio_loop` (in `DeepgramV2Module`) falls back to sending JSON `KeepAlive`
messages every 5 seconds. KeepAlive resets the *connection* timeout but does not
provide silence audio data; Deepgram v2 never sees silence and therefore never
fires `EndOfTurn`, causing the test to time out.

The fix pads the queue with `trailing_silence_s / chunk_duration` zero-byte
chunks after the real audio. `test_e2e_deepgram_v2` uses `trailing_silence_s=6.0`
which gives Deepgram well above the 5-second `eot_timeout_ms` threshold.

### In-process server/client pattern

Both the uvicorn server and the MCP client run as `asyncio.Task`s in the same
event loop as the test. The test waits for `server.started` before connecting
the client, ensuring no race condition on port availability.

### `asyncio.create_task` in `_on_message`

The message handler passed to `ClientSession` must return promptly (awaiting
inside it deadlocks `_receive_loop` — same pattern as `client.py`). Resource
reads are therefore offloaded to a separate task via `asyncio.create_task`. This was the original pattern; `AsrResourceClient` / `ResourceSubscriber` now owns this detail.

## Known limitations

- The tests require a valid Deepgram API key in `config.json` and hit the real
  Deepgram API. There is no CI integration at this stage.
- `trailing_silence_s` is a workaround; a cleaner long-term approach would be to
  send a `CloseStream` signal to the Deepgram v2 WebSocket once the file source
  is exhausted.
