# Plan 05 — Deepgram Module

Implement `DeepgramModule` using the Deepgram Python SDK streaming API.

## Tasks

- [ ] Implement `DeepgramModule` in `modules/deepgram.py` extending `ASRModule`:
  - Validate required config field `api_key` in `__init__`, raise `ValueError` if missing
  - Store optional fields with defaults: `language="en-US"`, `model="nova-2"`, `punctuate=True`, `interim_results=True`

- [ ] Implement WebSocket connection setup in `start()`:
  - Build connection options from config (encoding, sample_rate, channels, language, model, punctuate, interim_results)
  - Open a Deepgram live transcription session via the SDK
  - Register event handlers: `on_message`, `on_error`, `on_close`

- [ ] Implement audio sending loop:
  - Read chunks from `audio_queue` in a loop
  - Send each chunk as binary data to the Deepgram session
  - Send a KeepAlive message every 5 seconds when no audio chunk arrives (use `asyncio.wait_for` with timeout)

- [ ] Implement `on_message` handler:
  - Parse result events of type `"Results"`
  - Extract `channel.alternatives[0].transcript` and `confidence`
  - Use `speech_final` as `is_final`
  - Discard results with empty transcript
  - Call `on_result(ASRResult(...))` for valid results

- [ ] Implement `stop()`:
  - Send a CloseStream message to Deepgram
  - Close the WebSocket session cleanly
  - Set an internal stop flag to exit the audio loop

- [ ] Implement reconnection logic in `start()`:
  - Wrap the connection + audio loop in a retry loop
  - On connection error or unexpected close: log the error, wait with exponential backoff (1s → 2s → 4s → 8s max), reconnect
  - While reconnecting: drain `audio_queue` to prevent unbounded growth
  - Stop retrying only when `stop()` has been called

- [ ] Register `DeepgramModule` in `modules/__init__.py` registry under key `"deepgram"`

- [ ] Manual test: run with a real Deepgram API key and microphone, verify interim and final results are logged
