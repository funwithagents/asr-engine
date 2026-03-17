# Plan 08 — Demo Client

Implement the standalone demo client that subscribes to `asr://result` and logs results.

## Tasks

- [x] Implement `main()` in `client.py`:
  - Parse `--server` argument (default: `http://127.0.0.1:8080/mcp`)

- [x] Connect to the MCP server using the SDK's StreamableHTTP client transport

- [x] Subscribe to the `asr://result` resource

- [x] Implement the resource update handler:
  - Parse the JSON payload
  - If `is_final` is false: print `[INTERIM] <transcript>`
  - If `is_final` is true: print `[FINAL  ] <transcript> (confidence: <value>)` — omit confidence if `null`

- [x] Log connection lifecycle events:
  - `[INFO] Connected to MCP server at <url>`
  - `[INFO] Subscribed to asr://result`
  - `[WARN] Connection lost, retrying...`
  - `[INFO] Reconnected`

- [x] Handle Ctrl+C (KeyboardInterrupt / SIGINT) cleanly:
  - Unsubscribe from the resource
  - Close the MCP client session
  - Print `[INFO] Disconnected`

- [ ] End-to-end test: run server + client together, speak into microphone, verify interim and final results appear in the client log
