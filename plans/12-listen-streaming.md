# Plan 12 — Listen Tool Streaming via Progress Notifications

Stream committed final results to the caller during a `listen` session, using MCP
progress notifications. Each time a final ASR result is committed to the transcript,
the client receives a `notifications/progress` message with the accumulated text so
far. The final return value of `listen` is unchanged.

Specs: [mcp-server.md](../specs/mcp-server.md)

---

## Background

The MCP Python SDK supports progress notifications natively:

- **Server side**: `Context.report_progress(progress, total, message)` in
  `mcp/server/fastmcp/server.py`. FastMCP injects `Context` into any tool
  that declares a `ctx: Context` parameter. If the client did not supply a
  `progressToken`, the call is a silent no-op — backwards compatible.
- **Client side**: `ClientSession.call_tool(..., progress_callback=fn)` in
  `mcp/client/session.py`. The SDK automatically injects the `progressToken`
  into the request metadata and routes incoming `notifications/progress`
  messages to `fn`. Callback signature:
  `async def fn(progress: float, total: float | None, message: str | None) -> None`.

No streaming/generator tool output mechanism exists in the SDK. Progress
notifications are the correct approach.

---

## Tasks

### 1. `ListenSession` — add `on_final_committed` callback (`listen_session.py`)

- [ ] Add `on_final_committed: Callable[[str], Awaitable[None]] | None = None`
  parameter to `ListenSession.__init__`; store as `self._on_final_committed`
- [ ] In `on_result`, after `self._committed.append(result.transcript)`, if
  `self._on_final_committed` is set, call
  `await self._on_final_committed(" ".join(self._committed))`
- [ ] Unit tests (`tests/test_listen_session.py`):
  - `on_final_committed` is called once per committed final, with the
    correctly joined transcript so far
  - `on_final_committed` is **not** called for interim results
  - `on_final_committed` is **not** called when the final contains a trigger
    word (trigger word ends the session, not committed)
  - `on_final_committed=None` (default) still works without error

---

### 2. `listen` tool — inject `Context` and wire callback (`server.py`)

- [ ] Add `ctx: Context` parameter to the `listen` tool function
  (FastMCP injects it automatically; import `Context` from
  `mcp.server.fastmcp`)
- [ ] Define an `async def _on_final_committed(transcript: str) -> None`
  callback inside the `listen` handler that calls
  `await ctx.report_progress(progress=len(session._committed), total=None, message=transcript)`

  > Note: accessing `session._committed` for the `progress` count is
  > acceptable here because the callback is defined as a closure in the
  > same function body. If preferred, the committed count can be passed as
  > a parameter from `ListenSession` instead (see alternative below).

- [ ] Pass `on_final_committed=_on_final_committed` when constructing
  `ListenSession`
- [ ] Unit tests (`tests/test_server.py`):
  - `ctx.report_progress` is called once per committed final, with correct
    `message` (accumulated transcript so far)
  - `ctx.report_progress` is **not** called for the trigger word final
  - `ctx.report_progress` is **not** called when `on_final_committed` fires
    with an interim (sanity check — already covered by `ListenSession` tests)
  - Existing `listen` tests still pass (no regression)

---

### 3. `McpToolClient` — expose `progress_callback` (`tool_client.py`)

- [ ] Add `progress_callback: ProgressFnT | None = None` parameter to
  `call_tool` (import `ProgressFnT` from `mcp.shared.session`)
- [ ] Forward it to `session.call_tool(..., progress_callback=progress_callback)`
- [ ] Unit tests (`tests/test_client.py`):
  - `progress_callback=None` (default): no regression, existing test passes
  - `progress_callback=fn`: verify the kwarg is forwarded to the underlying
    `ClientSession.call_tool`

---

### 4. E2E test — verify streaming during `listen` (`tests-e2e/test_mcp_tool_client.py`)

- [ ] Add `test_e2e_listen_streaming`:
  - Server config: `auto_start=false`, `end_of_utterance_mode="timeout"`,
    `end_of_speech_timeout_s=2.0`
  - Audio fixture: `sample.wav` (*"the sky is blue"*) — no new fixture
    needed; this file already produces at least one final result (proven by
    the existing timeout e2e test). The goal here is to verify the
    notification plumbing works end-to-end, not to test multi-final
    accumulation (that is covered by unit tests).
  - Pass a `progress_callback` to `McpToolClient.call_tool` that appends
    each `message` to a list
  - Assert at least one progress notification was received before the tool
    returned
  - Assert the final notification `message` matches the final transcript

---

### 5. Update specs (`specs/mcp-server.md`)

- [ ] Add a **Streaming** subsection under the `listen` tool:
  - Describe the progress notification mechanism (one notification per
    committed final, message = accumulated transcript)
  - State the `progress` field convention (count of committed finals so far)
  - Note that streaming is opt-in: clients that omit `progressToken` receive
    no notifications and the final result is unchanged
  - Show the `ProgressFnT` callback signature for SDK users

---

### 6. Update implementation details

After all code changes are done and working:

- [ ] Create `implementation-details/12-listen-streaming.md`:
  - What was implemented: `on_final_committed` callback in `ListenSession`,
    `ctx: Context` in `listen` tool, `progress_callback` in `McpToolClient`
  - Deviations from spec (if any)
  - Non-obvious decisions: why `progress` = committed count (monotonically
    increasing, meaningful for clients that display a progress bar);
    why `message` = full accumulated transcript (not just the new fragment —
    simpler for clients to display); silent no-op when no `progressToken`
  - Known limitations: trigger word mode has no streaming (only one final
    is ever committed before the session ends anyway); `total` is always
    `None` since utterance length is unknown in advance

---

### 7. Update plans and index files

- [ ] Mark Plan 12 as done in `plans/plans.md`
- [ ] Add Plan 12 row to `implementation-details/implem.md`
- [ ] Update `AGENTS.md` if any notable new design decisions emerge
