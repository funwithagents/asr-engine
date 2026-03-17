# Plan 07 — MCP Server

Implement the MCP server exposing the `asr://result` resource and control tools over StreamableHTTP.

## Tasks

- [x] Implement `create_mcp_server(engine: ASREngine) -> mcp.Server` in `server.py`:
  - Instantiate an MCP `Server` with name `"asr-mcp"`

- [x] Register the `asr://result` resource:
  - Implement the `list_resources` handler returning a single `Resource` descriptor for `asr://result`
  - Implement the `read_resource` handler returning the current JSON payload (or an empty-transcript placeholder if no result yet)
  - Maintain an in-memory `_current_result: dict` updated by the engine callback

- [x] Implement the engine result callback `_on_asr_result(result: ASRResult)`:
  - Build the JSON payload: `transcript`, `is_final`, `confidence`, `timestamp` (UTC ISO 8601)
  - Store it as `_current_result`
  - Call `server.request_context.session.send_resource_updated("asr://result")` to notify subscribers

- [x] Register the `pause` tool:
  - Call `engine.pause()`
  - Return `{"status": "paused"}`
  - Return MCP error if `engine.pause()` raises `RuntimeError`

- [x] Register the `resume` tool:
  - Call `engine.resume()`
  - Return `{"status": "running"}`
  - Return MCP error if `engine.resume()` raises `RuntimeError`

- [x] Register the `is_running` tool:
  - Return `engine.status()`

- [x] Implement `run_server(config: AppConfig, engine: ASREngine)` in `server.py`:
  - Create the MCP server
  - Create a Starlette app with `StreamableHTTPServerTransport` mounted at `/mcp`
  - Run with `uvicorn` on `config.server.host` and `config.server.port`

- [x] Update `cli.py` `main()` to wire everything together:
  - Load config
  - Load ASR module from registry
  - Create `ASREngine`
  - Start `ASREngine` as an asyncio background task
  - Call `run_server()`
  - On shutdown (SIGINT/SIGTERM): call `engine.stop()`

- [ ] Manual test: start the server, use the MCP inspector or `curl` to read the resource and call tools
