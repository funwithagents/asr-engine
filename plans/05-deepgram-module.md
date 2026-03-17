# Plan 05 — Deepgram Module

Implement `DeepgramModule` using the Deepgram Python SDK Listen v2 streaming API (Flux model).

## Tasks

- [x] Implement `DeepgramModule` in `modules/deepgram.py` extending `ASRModule`:
  - Validate required config field `api_key` in `__init__`, raise `ValueError` if missing
  - Store optional fields with defaults: `model="flux-general-en"`, `eot_threshold=0.7`, `eot_timeout_ms=5000`

- [x] Implement WebSocket connection setup in `start()`:
  - Build connection options from config (`encoding="linear16"`, `sample_rate="16000"`, `model`, `eot_threshold`, `eot_timeout_ms`)
  - Open a Deepgram v2 live session via `async with client.listen.v2.connect(...) as conn:`
  - Register event handlers: `on_message` (for TurnInfo), `on_error`

- [x] Implement audio sending loop:
  - Read chunks from `audio_queue` using `asyncio.wait_for(audio_queue.get(), timeout=5.0)`
  - On chunk received: send via `await conn.send_media(chunk)`
  - On `asyncio.TimeoutError`: send KeepAlive via `await conn._send({"type": "KeepAlive"})`
  - Check `_stop_event` each iteration to exit cleanly

- [x] Implement `on_message` handler (TurnInfo events):
  - Filter for `ListenV2TurnInfo` instances only; ignore `ListenV2Connected` and others
  - Discard results with empty transcript
  - If `msg.event == "EndOfTurn"`: emit `ASRResult(is_final=True)`
  - Otherwise: emit `ASRResult(is_final=False)`
  - Use `msg.end_of_turn_confidence` as `confidence`

- [x] Implement `stop()`:
  - Set `_stop_event` to signal the audio loop to exit
  - The `async with` context manager handles connection closure automatically on exit

- [x] Implement reconnection logic in `start()`:
  - Run `listen_task` and `audio_task` concurrently; use `asyncio.wait(FIRST_COMPLETED)`
  - On connection error or unexpected close: log, wait with exponential backoff (1s → 2s → 4s → 8s max), reconnect
  - While reconnecting (during backoff): drain `audio_queue` via `_drain_queue_for()`
  - Stop retrying only when `_stop_event` is set

- [x] Register `DeepgramModule` in `modules/__init__.py` registry under key `"deepgram"`

- [ ] Manual test: run with a real Deepgram API key and microphone, verify interim and final results are logged
