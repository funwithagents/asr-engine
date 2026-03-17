# Plan 05 — Deepgram Module

Implement `DeepgramModule` using the Deepgram Python SDK Listen v2 streaming API (Flux model).

## Tasks

- [ ] Implement `DeepgramModule` in `modules/deepgram.py` extending `ASRModule`:
  - Validate required config field `api_key` in `__init__`, raise `ValueError` if missing
  - Store optional fields with defaults: `language="en-US"`, `model="flux-general-en"`, `punctuate=True`, `interim_results=True`, `eot_threshold=0.7`, `eot_timeout_ms=5000`

- [ ] Implement WebSocket connection setup in `start()`:
  - Capture the running event loop with `asyncio.get_running_loop()` (needed for async callback bridging)
  - Build connection options from config (`encoding="linear16"`, `sample_rate=16000`, `channels=1`, `language`, `model`, `punctuate`, `interim_results`, `eot_threshold`, `eot_timeout_ms`)
  - Open a Deepgram v2 live session via `async with client.listen.v2.connect(...) as conn:`
  - Register event handlers: `on_message` (for Results), `on_end_of_turn` (for EndOfTurn), `on_error`, `on_close`

- [ ] Implement audio sending loop:
  - Read chunks from `audio_queue` using `asyncio.wait_for(audio_queue.get(), timeout=5.0)`
  - On chunk received: send via `conn.send_media(chunk)`
  - On `asyncio.TimeoutError`: send KeepAlive as raw JSON text frame via `conn.send(json.dumps({"type": "KeepAlive"}))`
  - Check `_stop_event` each iteration to exit cleanly

- [ ] Implement `on_message` handler (Results events):
  - Extract `channel.alternatives[0].transcript` and `confidence`
  - Discard results with empty transcript
  - If `is_final=False`: call `loop.create_task(on_result(ASRResult(transcript, is_final=False, confidence)))`
  - If `is_final=True`: store transcript+confidence as `_last_segment`; call `loop.create_task(on_result(ASRResult(transcript, is_final=False, confidence)))`

- [ ] Implement `on_end_of_turn` handler (EndOfTurn events):
  - If `_last_segment` is set: call `loop.create_task(on_result(ASRResult(transcript, is_final=True, confidence)))` using stored values
  - Reset `_last_segment` to `None`

- [ ] Implement `stop()`:
  - Set `_stop_event` to signal the audio loop to exit
  - The `async with` context manager handles connection closure automatically on exit

- [ ] Implement reconnection logic in `start()`:
  - Wrap the `async with` connection block in a retry loop
  - On connection error or unexpected close: log the error, wait with exponential backoff (1s → 2s → 4s → 8s max), reconnect
  - While reconnecting (during backoff sleep): drain `audio_queue` to prevent unbounded growth
  - Stop retrying only when `_stop_event` is set

- [ ] Register `DeepgramModule` in `modules/__init__.py` registry under key `"deepgram"`

- [ ] Manual test: run with a real Deepgram API key and microphone, verify interim and final results are logged
