---
code:
  - src/asr_mcp/asr_resource_client.py
  - src/asr_mcp/resource_client.py
  - src/asr_mcp/resource_subscriber.py
  - src/asr_mcp/tool_client.py
tests:
  - tests/test_client.py
  - tests/test_subscriber.py
---

# Demo Client Specification

**Status:** Implemented

## Purpose

A standalone Python script that connects to the MCP server via StreamableHTTP, subscribes to the `asr://result` resource, and logs each update to stdout. Used to validate the server end-to-end.

## Usage

```bash
uv run asr-mcp-client --server http://<host>:<port>/mcp
```

Default server URL: `http://127.0.0.1:8080/mcp`

## Behavior

1. Connect to the MCP server
2. Subscribe to the `asr://result` resource
3. For each resource update received, log a formatted line to stdout:
   - Interim: `[INTERIM] hello how are`
   - Final:   `[FINAL  ] Hello, how are you? (confidence: 0.98)`
4. Also log connection events:
   - `[INFO] Connected to MCP server at http://...`
   - `[INFO] Subscribed to asr://result`
   - `[WARN] Connection lost, retrying...`
   - `[INFO] Reconnected`
5. Run until interrupted with Ctrl+C, then disconnect cleanly

## Output Format

```
[INFO] Connected to MCP server at http://127.0.0.1:8080/mcp
[INFO] Subscribed to asr://result
[INTERIM] hello how
[INTERIM] hello how are you
[FINAL  ] Hello, how are you? (confidence: 0.98)
[INTERIM] what time
[FINAL  ] What time is it? (confidence: 0.95)
```

## Implementation Notes

- Implemented in `src/asr_mcp/asr_resource_client.py` (CLI entry point)
- `src/asr_mcp/resource_client.py` — `AsrResourceClient`: resource subscription library class
- `src/asr_mcp/tool_client.py` — `McpToolClient`: single-call tool invocation library class
- Uses the official MCP Python SDK client
- No config file needed — server URL passed as CLI argument (with a sensible default)
- Minimal dependencies: only `mcp` SDK + standard library
