# Implementation Details — Plan 05: Deepgram Module

## What was implemented

`DeepgramModule` in `src/asr_mcp/modules/deepgram.py`, registered as `"deepgram"` in `modules/__init__.py`. Uses the Deepgram Python SDK v6 (Fern-generated) with the Listen v2 (Flux) WebSocket API.

## SDK version surprise: v6 is a completely different API

The installed `deepgram-sdk==6.0.1` is a Fern-generated SDK with a completely different interface from the v3/v4 SDK documented in most online examples and tutorials. Key differences from the old SDK:

- No `AsyncDeepgramClient("api_key")` positional arg — must use `AsyncDeepgramClient(api_key="...")` keyword
- No `LiveOptions` object — options are kwargs to `connect()`
- `send_media()` and `start_listening()` are **async** (`await` required)
- No `send_keep_alive()` method in v2 (exists in v1 only)
- Async event handlers work natively — `_emit_async` awaits coroutines

## v2 vs spec: config fields dropped

The v2 SDK `connect()` method does **not** accept `language`, `punctuate`, or `interim_results`. These are v1-only query parameters. The spec's config table was updated accordingly. The v2 config is:

| Field | Default |
|---|---|
| `model` | `"flux-general-en"` |
| `eot_threshold` | `0.7` |
| `eot_timeout_ms` | `5000` |

## Message model: TurnInfo replaces Results + speech_final

In v2 there are no `Results` messages. All transcription comes via `ListenV2TurnInfo` (type `"TurnInfo"`). This object carries:
- `transcript` — the full accumulated transcript for the current turn
- `event` — one of `"Update"`, `"StartOfTurn"`, `"EagerEndOfTurn"`, `"TurnResumed"`, `"EndOfTurn"`
- `end_of_turn_confidence` — confidence that no more speech is coming

Mapping to `ASRResult`:
- Any `TurnInfo` with `event != "EndOfTurn"` → `ASRResult(is_final=False)`
- `event == "EndOfTurn"` → `ASRResult(is_final=True)`
- `end_of_turn_confidence` used as `confidence` (not transcript confidence — closest available)

This is simpler and more reliable than the v1 `speech_final` heuristic.

## KeepAlive: uses private `_send()`

The v2 `AsyncV2SocketClient` has no public `send_keep_alive()`. KeepAlive is sent via `conn._send({"type": "KeepAlive"})`. The `_send()` method accepts a dict and serialises it to JSON before sending as a text frame. This is a documented SDK gap; noted here as a known SDK quirk.

## Concurrency model: two tasks + asyncio.wait

Inside each connection attempt, `start_listening()` and `_audio_loop()` run as two concurrent `asyncio.Task`s. `asyncio.wait(FIRST_COMPLETED)` is used so that either task completing (normally or via exception) triggers cleanup of the other. This cleanly handles both:
- Graceful stop: `_stop_event` breaks `_audio_loop` → cancels `listen_task`
- Unexpected disconnect: `start_listening()` exits → cancels `audio_task` → triggers reconnect

## Async handler bridge

The SDK's `_emit_async` (used by `start_listening()`) already checks `if inspect.isawaitable(res): await res`. So `async def on_message(msg)` works directly without `loop.create_task()`. The spec's mention of an async bridge via `loop.create_task()` was not needed.

## Known limitations

- `EagerEndOfTurn` events are not given special treatment — they emit `is_final=False` like `Update`. Could be exposed later if the engine needs speculative processing.
- `language` support is not configurable. Flux is English-focused; non-English use would require the v1 API.
- Manual test against a real API key was not performed during automated implementation.
