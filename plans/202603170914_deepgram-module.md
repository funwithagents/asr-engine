# Deepgram Modules

**Status:** Done

Implement two ASR modules using the Deepgram Python SDK:
- `deepgram_v1` — Listen v1 (Nova-3, multi-language, `speech_final` utterance detection)
- `deepgram_v2` — Listen v2 / Flux (English, built-in `EndOfTurn` detection)

## Tasks

### deepgram_v2 (Listen v2 / Flux)

- [x] Implement `DeepgramV2Module` in `modules/deepgram_v2.py` extending `ASRModule`:
  - Validate required `api_key`, raise `ValueError` if missing
  - Defaults: `model="flux-general-en"`, `eot_threshold=0.7`, `eot_timeout_ms=5000`

- [x] Implement WebSocket connection in `start()`:
  - `async with client.listen.v2.connect(model, encoding, sample_rate, eot_threshold, eot_timeout_ms)`
  - Register `on_message` (TurnInfo), `on_error` handlers

- [x] Implement audio loop:
  - `await conn.send_media(chunk)` for each chunk
  - On 5s timeout: `await conn._send({"type": "KeepAlive"})` (no public method in v2)

- [x] Implement `on_message`:
  - Filter `ListenV2TurnInfo`; discard empty transcripts
  - `event == "EndOfTurn"` → `ASRResult(is_final=True)`
  - All others → `ASRResult(is_final=False)`
  - `end_of_turn_confidence` → `confidence`

- [x] Implement `stop()`: set `_stop_event`

- [x] Implement reconnection: exponential backoff + queue draining

### deepgram_v1 (Listen v1 / Nova-3)

- [x] Implement `DeepgramV1Module` in `modules/deepgram_v1.py` extending `ASRModule`:
  - Validate required `api_key`, raise `ValueError` if missing
  - Defaults: `model="nova-3"`, `language="en-US"`, `punctuate=True`, `interim_results=True`

- [x] Implement WebSocket connection in `start()`:
  - `async with client.listen.v1.connect(model, encoding, sample_rate, channels, language, punctuate, interim_results)`
  - Register `on_message` (Results), `on_error` handlers

- [x] Implement audio loop:
  - `await conn.send_media(chunk)` for each chunk
  - On 5s timeout: `await conn.send_keep_alive()` (public method in v1)

- [x] Implement `on_message`:
  - Filter `ListenV1Results`; discard empty transcripts
  - `speech_final=True` → `ASRResult(is_final=True)`
  - `speech_final=False` → `ASRResult(is_final=False)`
  - `channel.alternatives[0].confidence` → `confidence`

- [x] Implement `stop()`: set `_stop_event`

- [x] Implement reconnection: exponential backoff + queue draining

### Registry & Tests

- [x] Register both in `modules/__init__.py`: `"deepgram_v1"` and `"deepgram_v2"`
- [x] Write `tests/modules/test_deepgram_v1.py` (16 tests)
- [x] Write `tests/modules/test_deepgram_v2.py` (16 tests)

### Manual Test

- [ ] Manual test: run with a real Deepgram API key and microphone for both modules
