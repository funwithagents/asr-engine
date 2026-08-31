---
code:
  - src/asr_engine/asr_resource_client.py
  - src/asr_engine/resource_client.py
  - src/asr_engine/resource_subscriber.py
tests:
  - tests/test_client.py
  - tests/test_subscriber.py
---

# Demo Client Specification

**Status:** Implemented

## Purpose

A standalone Python script that connects to the MCP server via StreamableHTTP, subscribes to the `asr://utterance` resource, and logs each update to stdout. Used to validate the server end-to-end.

## Usage

```bash
uv run asr-mcp-client --server http://<host>:<port>/mcp
```

Default server URL: `http://127.0.0.1:8000/mcp`

## Behavior

1. Connect to the MCP server
2. Subscribe to the `asr://utterance` resource
3. For each resource update received, log a formatted line to stdout:
   - Interim: `[INTERIM] hello how are`
   - Final:   `[FINAL  ] Hello, how are you? (confidence: 0.98)`
4. Also log connection events:
   - `[INFO] Connected to MCP server at http://...`
   - `[INFO] Subscribed to asr://utterance`
   - `[WARN] Connection lost, retrying...`
   - `[INFO] Reconnected`
5. Run until interrupted with Ctrl+C, then disconnect cleanly

## Output Format

```
[INFO] Connected to MCP server at http://127.0.0.1:8000/mcp
[INFO] Subscribed to asr://utterance
[INTERIM] hello how
[INTERIM] hello how are you
[FINAL  ] Hello, how are you? (confidence: 0.98)
[INTERIM] what time
[FINAL  ] What time is it? (confidence: 0.95)
```

## Implementation Notes

- Implemented in `src/asr_engine/asr_resource_client.py` (CLI entry point)
- `src/asr_engine/resource_client.py` — `AsrResourceClient`: resource subscription library class. Takes a `resource_uri` argument (default `asr://utterance`) so the same class can subscribe to `asr://segment` — `AsrToTerminal` uses it that way.
- A single-call MCP **tool** client (`McpToolClient`) is **not** part of the package — it has no product consumer, so it lives as a test-only helper at `tests-e2e/mcp_tool_client.py` (used by `tests-e2e/test_mcp_tool_client.py`).
- Uses the official MCP Python SDK client
- No config file needed — server URL passed as CLI argument (with a sensible default)
- Minimal dependencies: only `mcp` SDK + standard library
