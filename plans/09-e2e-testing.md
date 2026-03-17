# Plan 09 — E2E File-Based Testing

Implement automated end-to-end tests that feed a WAV fixture through the full
pipeline and assert the expected transcript is received by an in-process MCP client.

Spec: [specs/e2e-testing.md](../specs/e2e-testing.md)

## Tasks

### 1. Add `AudioSource` protocol to `audio.py`

- [x] Define a `typing.Protocol` named `AudioSource` with:
  - `start() -> asyncio.Queue[bytes]`
  - `stop() -> None`
  - `pause() -> None`
  - `resume() -> None`
- [x] Annotate existing `AudioCapture` as satisfying `AudioSource` (no structural
  changes needed — it already matches)

### 2. Add `FileAudioSource` to `audio.py`

- [x] Constructor: `__init__(self, path: Path | str, chunk_samples: int = CHUNK_SAMPLES, trailing_silence_s: float = 0.0)`
- [x] `start()`:
  - Open the WAV file with `wave.open`
  - Assert sample rate == `SAMPLE_RATE`, channels == `CHANNELS`, sample width == 2
  - Create an `asyncio.Queue[bytes]`
  - Launch `_feed()` as a background `asyncio.Task`
  - Return the queue
- [x] `_feed()` coroutine:
  - Loop: read `chunk_samples` frames, put bytes on queue, `await asyncio.sleep(chunk_duration)`
  - Stop when all frames are exhausted
  - Optionally pad with silence chunks for `trailing_silence_s` seconds (needed for v2 EndOfTurn detection)
- [x] `stop()`: cancel the feed task if running
- [x] `pause()` / `resume()`: no-ops (file playback is not interruptible)

### 3. Inject `audio_source` into `ASREngine`

- [x] Add optional parameter `audio_source: AudioSource | None = None` to
  `ASREngine.__init__`
- [x] In `ASREngine.start()`:
  - If `audio_source` is not `None`, call `audio_source.start()` instead of
    constructing `AudioCapture`
  - Store the source in `self._audio_capture` so `stop()`, `pause()`, `resume()`
    work transparently

### 4. Create `e2e/`

- [x] Create `e2e/` directory with `__init__.py`
- [x] Add `e2e/fixtures/` sub-directory
- [x] Add `e2e/fixtures/README.md` documenting the expected WAV format:
  16 kHz, mono, signed 16-bit PCM, content = "the sky is blue"

### 5. Write `e2e/test_file_asr.py`

- [x] Helper `_normalize(text: str) -> str`: lowercase + strip non-`[a-z0-9 ]`
- [x] Helper `_load_api_key() -> str`: parse `config.json` at repo root
- [x] Shared coroutine `_run_e2e(module_type, module_config, port, trailing_silence_s)`:
  - Construct `FileAudioSource("e2e/fixtures/sample.wav")`
  - Construct `ASREngine` with the file source
  - Call `create_mcp_server(engine)` → get `FastMCP`
  - Start uvicorn in a background task; wait for `server.started`
  - Start the engine
  - Run inline MCP client:
    - Connect with `streamable_http_client`
    - Subscribe to `asr://result`
    - On each `ResourceUpdatedNotification`: fetch resource, collect payload,
      set `final_event` if `is_final=True`
    - Await `final_event`
  - Wrap with `asyncio.wait_for(timeout=30.0)`
  - Teardown: stop engine, set `server.should_exit = True`, await server task
  - Assert at least one final result received
  - Assert `_normalize(last_final_transcript) == _normalize("the sky is blue")`
- [x] `test_e2e_deepgram_v1`: calls `_run_e2e("deepgram_v1", {...nova-3...}, port=18001)`
- [x] `test_e2e_deepgram_v2`: calls `_run_e2e("deepgram_v2", {...flux-general-en...}, port=18002, trailing_silence_s=6.0)`
