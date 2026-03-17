# Implementation Details — Plan 07: MCP Server

## What was implemented

- `src/asr_mcp/server.py` — `create_mcp_server(engine)` and `run_server(config, engine)`
- `src/asr_mcp/cli.py` — updated `main()` to instantiate the module + engine and call `run_server`
- `tests/test_server.py` — 17 unit tests covering the resource, tools, and result callback

## Deviations from spec

### FastMCP instead of lowlevel `mcp.Server`

The plan mentioned `mcp.Server` (lowlevel API). The implementation uses `FastMCP` (high-level API) because it provides decorator-based resource and tool registration, built-in StreamableHTTP transport via `streamable_http_app()`, and integrates directly with uvicorn via `run_streamable_http_async`.

### All runtime logic lives in `run_server`; `cli.py` is a thin entry point

`run_server(config)` owns the full lifecycle: validates the ASR type, prints the startup banner, instantiates the ASR module from `REGISTRY`, creates `ASREngine` with a no-op callback, calls `create_mcp_server` (which wires the real callback), starts the engine, runs uvicorn, and stops the engine in a `finally` block.

`cli.py` is reduced to: parse args → `load_config` (the only thing that requires file I/O) → `asyncio.run(run_server(config))`, with two separate `try/except` blocks so config file errors and runtime errors (unknown ASR type, etc.) both produce clean `stderr` messages and `sys.exit(1)`.

### `create_mcp_server` replaces `engine._on_result`

Rather than requiring the caller to pass the right callback at `ASREngine` construction time, `create_mcp_server` assigns `engine._on_result = _on_asr_result` directly after constructing the closure state. The no-op passed at engine construction is immediately overwritten before the engine starts.

## Non-obvious decisions

### Subscribe / unsubscribe via the lowlevel `_mcp_server`

FastMCP has no public subscribe/unsubscribe API. The subscribe handler is registered on `mcp._mcp_server` (the lowlevel `Server`), which is the only way to intercept MCP resource subscription requests. Sessions are stored in a closure-local list and pruned on send failure.

### `call_tool` returns `list[TextContent]`, not a raw dict

FastMCP's `call_tool()` returns a list of `TextContent` objects. The tests use a `tool_result_json()` helper that parses `result[0].text` as JSON. This is different from accessing the dict directly.

### MCP tool errors propagate automatically

When a tool function raises `RuntimeError` (e.g. `engine.pause()` when already paused), FastMCP catches it and returns a `ToolError` response to the client. No explicit error wrapping is needed.

### `AnyUrl("asr://result")` works in pydantic v2

The custom `asr://` scheme is accepted by pydantic v2's `AnyUrl`. Used both for the `send_resource_updated` call and in the subscribe/unsubscribe handlers.

## Known limitations

- No session cleanup when a client disconnects cleanly (unsubscribe handler must be called by the client). Dead sessions are only pruned after a failed send.
- The `run_server` function does not expose a way to shut the HTTP server down programmatically (other than SIGINT/SIGTERM).
- Manual test (MCP Inspector / curl) has not been performed yet.
