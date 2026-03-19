# Plan 11 — Listen Tool & auto_start

## What was implemented

- **`speech_utils.py`**: `contains_trigger_word(transcript, words)` — case-insensitive substring match. Shared between `AsrToTerminal` and `ListenSession`.
- **`listen_session.py`**: `ListenSession` + `ListenResult`. Two modes: `trigger_word` (waits for a final result containing a trigger word) and `timeout` (two-timer approach: initial-silence and end-of-speech).
- **`asr_resource_client.py`**: `_format_result`, `_run_client`, `main()` moved out of `client.py` into this dedicated demo CLI file.
- **`resource_client.py`**: `AsrResourceClient` — resource subscription wrapper around `ResourceSubscriber` with `asr://result` hardcoded.
- **`tool_client.py`**: `McpToolClient` — single-call tool invocation. `McpToolClient.call_tool` opens a `streamable_http_client` connection per call, initialises a `ClientSession`, calls the tool, parses the JSON result, and closes the connection.
- **`config.py`** (updated): added `EngineConfig` (`auto_start: bool = True`) and `ListenConfig` (`end_of_utterance_mode`, `trigger_words`, `initial_silence_timeout_s`, `end_of_speech_timeout_s`). Both are parsed from optional JSON blocks.
- **`server.py`** (updated): `create_mcp_server` now accepts `listen_config`; added `listen` tool with `asyncio.Lock` concurrency guard; `run_server` conditionally calls `engine.start()` based on `config.engine.auto_start`.
- **`asr_to_terminal.py`** (updated): removed `_contains_submit_word` private method; replaced with `speech_utils.contains_trigger_word(transcript, self._submit_words)`.

## Deviations from spec

- The `listen` tool raises `ValueError` (which FastMCP wraps as a `ToolError`) rather than returning an MCP error response directly. This is idiomatic FastMCP usage — the error message still matches the spec.
- The `_listen_lock.locked()` check happens before acquiring the lock; this is safe because the `async with _listen_lock` immediately follows. A tiny race window exists between the check and the acquire, but the lock still guards against actual concurrent execution.

## Non-obvious decisions

- **Timer cancellation strategy**: `ListenSession` stores `asyncio.Task` references for both timers. On `wait()` return, both are explicitly cancelled. Tasks use `try/except asyncio.CancelledError: pass` internally so they don't raise when cancelled.
- **Callback swap mechanism**: The `listen` tool saves `engine._on_result` before replacing it with `session.on_result`, then restores it in a `finally` block. This allows the MCP resource callback to resume after the `listen` session ends.
- **Lock approach**: `asyncio.Lock` is used (not `threading.Lock`) since the entire server runs on one event loop. The lock is created per `create_mcp_server` call (closure variable), so each server instance has its own lock.
- **`ListenResult.transcript` in `wait()`**: The transcript field is populated from `_committed` at the time `wait()` returns. For the `trigger_word` mode, the result is built before returning from `wait()` rather than in `on_result`, to handle the case where `_committed` could still be appended to before `wait()` processes the event.

## Known limitations

- `trigger_word` mode has no timeout guard: if no trigger word is ever spoken, `listen` blocks indefinitely. Callers must implement their own outer timeout (e.g. `asyncio.wait_for`).
- The `McpToolClient` opens a new HTTP connection per `call_tool` invocation — fine for one-shot calls but not suitable for high-frequency polling.
