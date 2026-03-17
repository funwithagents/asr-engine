# 08 — Demo Client

## What was implemented

`src/asr_mcp/client.py` — a standalone async demo client that:

- Parses `--server` (default `http://127.0.0.1:8080/mcp`) via `argparse`
- Connects using `mcp.client.streamable_http.streamable_http_client`
- Creates a `ClientSession` with a `message_handler` callback
- Subscribes to `asr://result` on startup
- On each `ResourceUpdatedNotification`, reads the resource and prints a formatted line
- Reconnects automatically (`_run_with_reconnect`) on any non-cancel exception, printing `[WARN] Connection lost, retrying...`
- On Ctrl+C: unsubscribes, lets context managers close the session, prints `[INFO] Disconnected`

`tests/test_client.py` — 23 unit tests covering:

- `_format_result` exhaustively (interim/final, with/without confidence, empty transcripts, unicode)
- Parametrized structural tests over `(transcript, is_final, confidence)` combinations
- `main()` default and custom `--server` argument forwarding
- `main()` Ctrl+C printing `[INFO] Disconnected`
- `_on_message` handler dispatching a `ResourceUpdatedNotification` → read → print
- `_on_message` handler ignoring non-notification messages

## Deviations from spec

- `[INFO] Reconnected` log line is not emitted. After reconnection `_run` reprints `[INFO] Connected to MCP server at <url>` and `[INFO] Subscribed to asr://result` instead. This is equivalent information and avoids a separate reconnect state flag.

## Non-obvious decisions

- **`message_handler` + `read_resource` pattern**: `ResourceUpdatedNotification` carries only the URI, not the payload. The handler must call `session.read_resource(uri)` to retrieve the updated JSON. The session reference is captured via a single-element list (`session_holder`) to avoid the chicken-and-egg problem of needing the session before it is created.
- **`asyncio.sleep(float("inf"))` as idle wait**: Keeps the coroutine alive without polling. `CancelledError` from Ctrl+C flows naturally through `asyncio.run` and the `finally` block unsubscribes cleanly.
- **`_run_with_reconnect` re-raises `CancelledError`**: The reconnect loop only catches generic `Exception`, so cancellation (from Ctrl+C via `asyncio.run`) propagates immediately without triggering a retry.

## Known limitations

- `[INFO] Reconnected` is not printed separately — on reconnect the full connect/subscribe banner is printed instead.
- End-to-end test (microphone + live server) is a manual step; it is not automated.
