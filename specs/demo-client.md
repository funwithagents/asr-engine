---
code:
  - examples/mcp_client/asr_resource_client.py
  - examples/mcp_client/resource_client.py
  - examples/mcp_client/resource_subscriber.py
tests:
  - tests/examples/test_client.py
  - tests/examples/test_subscriber.py
---

# Demo Client Specification

**Status:** Implemented

## Purpose

A standalone example that connects to the MCP server via StreamableHTTP, subscribes to the `asr://utterance` resource, and logs each update. Used to validate the server end-to-end. It lives in `examples/mcp_client/`, outside the `asr_engine` package.

## Usage

```bash
uv run python -m examples.mcp_client.asr_resource_client --server http://<host>:<port>/mcp
```

Default server URL: `http://127.0.0.1:8000/mcp`

## Behavior

1. Connect to the MCP server
2. Subscribe to the `asr://utterance` resource
3. For each resource update received, log a formatted line at `INFO` through the module logger (`logging.getLogger(__name__)`):
   - Interim: `[INTERIM] hello how are`
   - Final:   `[FINAL  ] Hello, how are you? (confidence: 0.98)`
4. Connection-lifecycle events are logged by the subscription library (`ResourceSubscriber`) through its own logger: connected, subscribed, reconnected (after a retry), and a `WARNING` when the connection is lost and a retry is scheduled.
5. Run until interrupted with Ctrl+C, then log `Disconnected` and disconnect cleanly

## Logging

The demo uses the standard `logging` library, not hand-formatted `print`s. As an application entry point it configures logging itself — its own `logging.basicConfig(...)` in `main()`, **not** the library's private `asr_engine._logging.setup_logging` (examples depend only on the public library). The format matches the rest of the project (`%(asctime)s %(levelname)s %(name)s: %(message)s`), so every line carries a timestamp, level, and logger name; `basicConfig` writes to stderr by default. Representative output:

```
2026-03-17 10:23:45,001 INFO examples.mcp_client.resource_subscriber: Connected to http://127.0.0.1:8000/mcp
2026-03-17 10:23:45,002 INFO examples.mcp_client.resource_subscriber: Subscribed to asr://utterance
2026-03-17 10:23:46,100 INFO examples.mcp_client.asr_resource_client: [INTERIM] hello how
2026-03-17 10:23:46,400 INFO examples.mcp_client.asr_resource_client: [INTERIM] hello how are you
2026-03-17 10:23:47,000 INFO examples.mcp_client.asr_resource_client: [FINAL  ] Hello, how are you? (confidence: 0.98)
```

## Implementation Notes

- Implemented in `examples/mcp_client/asr_resource_client.py` (CLI, run via `python -m`)
- `examples/mcp_client/resource_client.py` — `AsrResourceClient`: resource subscription library class. Takes a `resource_uri` argument (default `asr://utterance`) so the same class can subscribe to `asr://segment` — `AsrToTerminal` (in `examples/asr_to_terminal/`) imports it that way.
- `examples/mcp_client/resource_subscriber.py` — `ResourceSubscriber`: the generic MCP resource watcher `AsrResourceClient` is built on.
- A single-call MCP **tool** client (`McpToolClient`) is **not** part of the package or the examples — it has no product consumer, so it lives as a test-only helper at `tests-e2e/mcp_tool_client.py` (used by `tests-e2e/test_mcp_tools.py`).
- Uses the official MCP Python SDK client
- No config file needed — server URL passed as CLI argument (with a sensible default)
- Minimal dependencies: only `mcp` SDK + standard library
