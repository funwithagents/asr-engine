# 08 — Demo Client

## What was implemented

### `src/asr_mcp/resource_subscriber.py` — `ResourceSubscriber`

Generic MCP resource watcher. Accepts any `server_url`, `resource_uri`, and an
async `on_event(payload: dict)` callback. Key design:

- `start()` launches a background `asyncio.Task`
- `stop()` cancels it and waits for teardown
- Reconnects automatically on connection errors when `reconnect=True` (default)
- Single connection attempt lives in `_connect()`; reconnection loop in `_loop()`
- `_on_message` handler uses `asyncio.create_task` to avoid deadlocking
  `BaseSession._receive_loop` (see note below)

### `src/asr_mcp/asr_resource_client.py` — demo CLI (moved from `client.py` in Plan 11)

- `AsrResourceClient(server_url, on_event)` (`resource_client.py`): thin wrapper around `ResourceSubscriber`
  with `asr://result` hardcoded as the resource URI; exposes `start()` / `stop()`
- `_format_result(payload)`, `_run_client(server_url)`, `main()`: live in `asr_resource_client.py` (demo CLI).

**Plan 11 note:** `McpToolClient` (single-call tool invocation via `streamable_http_client`) lives in `tool_client.py`. Entry point `asr-mcp-client` points to `asr_mcp.asr_resource_client:main`.

### `tests/test_subscriber.py` — 6 unit tests covering `ResourceSubscriber`

- `start()` creates a background task
- `stop()` cancels it
- `on_event` is called with the correct payload on `ResourceUpdatedNotification`
- Non-notification messages are ignored
- `_loop` retries after a connection error when `reconnect=True`
- `_loop` propagates errors when `reconnect=False`

### `tests/test_client.py` — unit tests covering `resource_client.py`, `tool_client.py`, and `asr_resource_client.py`

- `_format_result` exhaustively (interim/final, with/without confidence, empty
  transcripts, unicode)
- Parametrized structural tests over `(transcript, is_final, confidence)`
- `main()` default and custom `--server` argument forwarding
- `main()` Ctrl+C printing `[INFO] Disconnected`

## Deviations from spec

The original spec described a single `client.py` with inline connection logic and
a `_run_with_reconnect` function. The implementation was later refactored into:

1. `resource_subscriber.py` — generic, reusable subscriber class
2. `resource_client.py` — ASR-specific `AsrResourceClient` wrapper
3. `asr_resource_client.py` — demo CLI (`_format_result`, `_run_client`, `main`)

This gives a cleaner separation: `ResourceSubscriber` can be reused (e.g. in e2e
tests) without coupling to the ASR-specific resource URI or print formatting.

## Non-obvious decisions

- **`asyncio.create_task` in `_on_message`**: `_on_message` is called from
  `BaseSession._receive_loop`. Awaiting `read_resource` inside it deadlocks the
  loop (response arrives as a new message but the loop is frozen). Fix: call
  `asyncio.create_task(_fetch(session))` and return immediately.
- **`start`/`stop` instead of a `run()` coroutine**: callers (demo client, e2e
  tests) can independently control the subscriber lifecycle without managing a
  raw task themselves.
- **`reconnect=True` default**: matches production use (demo client should always
  reconnect). E2e tests that call `stop()` explicitly are unaffected by this
  default.

## Known limitations

- No `[INFO] Reconnected` log line: on reconnect the full connect/subscribe
  sequence runs again, which prints equivalent information.
