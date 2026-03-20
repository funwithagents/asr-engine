# Plan 12 — Listen Tool Streaming via Progress Notifications

## What was implemented

- **`ListenSession.on_final_committed`** — new optional `Callable[[str], Awaitable[None]]`
  parameter. After each final result is committed to `self._committed`, the callback
  is called with `" ".join(self._committed)` (the full accumulated transcript so far).
  Not called for interim results or for the trigger-word final (which ends the session
  without committing).

- **`listen` tool — `ctx: Context`** — FastMCP injects `Context` automatically when
  the tool declares a `ctx: Context` parameter. A closure `_on_final_committed` is
  defined inside the handler and calls `ctx.report_progress(progress, total, message)`
  before passing it to `ListenSession`. This keeps the progress plumbing entirely in
  the server layer; `ListenSession` is not coupled to MCP.

- **`McpToolClient.call_tool` — `progress_callback`** — new optional `ProgressFnT | None`
  parameter, forwarded verbatim to `ClientSession.call_tool`. The SDK handles
  `progressToken` injection and notification routing automatically. Callers that omit
  the argument get the same behaviour as before (no regression).

## Deviations from spec

None. The implementation follows the plan exactly.

## Non-obvious decisions

- **`progress` = count of committed finals** — monotonically increasing integer.
  Chosen because it is the natural "how far along are we" signal clients can use
  for a progress bar. Using the character length of the transcript was rejected as
  it is less predictable.

- **`message` = full accumulated transcript** — not just the new fragment. Simpler
  for display clients: they can always replace their current text with the latest
  message and get the correct cumulative view without needing to join fragments
  themselves.

- **`total = None`** — the number of utterances in a session is unknown in advance,
  so no meaningful total can be reported.

- **Closure over `session`** — `_on_final_committed` reads `len(session._committed)`
  directly. The closure is defined before `session` is assigned but is only called
  after `session` exists (from within `session.on_result`), so this is safe.
  Accessing the private attribute is acceptable because both are in the same function
  body.

- **Patching `Context.report_progress` in unit tests** — FastMCP constructs the
  `Context` object internally when calling tools via `mcp.call_tool`. There is no
  public injection point, so tests patch the method on the class itself (saving and
  restoring the original in `finally`).

## Known limitations

- **Trigger-word mode has no streaming** — only one final is ever committed before
  the session ends anyway (the trigger utterance itself is not committed). In practice,
  multiple non-trigger finals can precede the trigger word, so notifications are
  emitted for those. But the session ends after the trigger, so there is at most one
  "useful" notification window.

- **`total` is always `None`** — clients cannot display a deterministic progress bar.

- **Single-connection requirement** — `McpToolClient` opens a new connection per
  `call_tool`. Progress notifications travel over the same connection; this works
  correctly with `streamable_http_client` and `ClientSession`.
