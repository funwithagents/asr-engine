# Implementation Details — Plan 05: Deepgram Modules

## What was implemented

Two ASR modules in `src/asr_mcp/modules/`:
- `deepgram_v1.py` → `DeepgramV1Module` (registered as `"deepgram_v1"`)
- `deepgram_v2.py` → `DeepgramV2Module` (registered as `"deepgram_v2"`)

Both use `deepgram-sdk==6.0.1` (Fern-generated), with the `AsyncDeepgramClient`.

## Why two modules

The v1 and v2 Deepgram APIs have fundamentally different message models:

| | deepgram_v1 | deepgram_v2 |
|---|---|---|
| Endpoint | `/v1/listen` | `/v2/listen` |
| Models | nova-3, nova-2, enhanced, base, … | flux-* only |
| Languages | all BCP-47 | English only |
| Utterance signal | `speech_final` on `Results` messages | `EndOfTurn` event on `TurnInfo` messages |
| KeepAlive | `send_keep_alive()` (public) | `_send({"type": "KeepAlive"})` (private) |
| Config extras | `language`, `punctuate`, `interim_results` | `eot_threshold`, `eot_timeout_ms` |

## SDK version surprise: v6 is Fern-generated

`deepgram-sdk==6.0.1` is completely different from the v3/v4 SDK documented in most online examples. Key differences from what the spec assumed:

- `AsyncDeepgramClient` requires `api_key=` keyword arg (not positional)
- All connect params are passed as `str`, not typed objects (e.g. `sample_rate="16000"`)
- `send_media()` and `start_listening()` are **async** in both v1 and v2
- Async event handlers work natively — `_emit_async` awaits coroutines
- v2 has no `send_keep_alive()` — uses private `_send()`
- v1 has `send_keep_alive()`, `send_finalize()`, `send_close_stream()` as public methods

## Message model differences

**v1 `ListenV1Results`:**
- `channel.alternatives[0].transcript` — the text
- `channel.alternatives[0].confidence` — transcript confidence
- `speech_final` — True = end of utterance (maps to `ASRResult.is_final`)
- `is_final` — True = segment won't change (not used for our `is_final`)

**v2 `ListenV2TurnInfo`:**
- `transcript` — full accumulated turn text (not per-segment)
- `end_of_turn_confidence` — confidence that turn is over (used as proxy for `confidence`)
- `event` — `"EndOfTurn"` maps to `ASRResult.is_final=True`; all others are `False`

## Concurrency model (identical for both)

`start_listening()` and `_audio_loop()` run as concurrent `asyncio.Task`s under `asyncio.wait(FIRST_COMPLETED)`. Either completing (or raising) cancels the other, then triggers reconnect if `_stop_event` is not set.

## KeepAlive quirk in v2

The v2 SDK `AsyncV2SocketClient` exposes no public keepalive method. The internal `_send(dict)` accepts a dict and serialises it to JSON. This is used as `conn._send({"type": "KeepAlive"})`. Noted as a known SDK gap — if the SDK exposes a public method in a future version, this should be updated.

## Config: dropped fields vs original spec

The original spec (before splitting into v1/v2) included `language`, `punctuate`, `interim_results` for the single Deepgram module. These are now:
- **v1 only** — all three are accepted by the v1 `connect()` method
- **not in v2** — the v2 `connect()` method doesn't accept them; Flux is English-only and has no `interim_results`/`punctuate` params

## Known limitations

- `EagerEndOfTurn` events in v2 are treated as interim (`is_final=False`). Could be surfaced separately if the engine needs speculative processing.
- Manual test against a real API key was not performed during automated implementation.
