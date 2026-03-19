# Plan 11 — Listen Tool & auto_start

Implement `engine.auto_start` config, the `listen` MCP tool, the `ListenSession`
class, the `McpToolClient`, and the shared `speech_utils` module. Refactor
`AsrToTerminal` to use the shared trigger word detection.

Specs: [mcp-server.md](../specs/mcp-server.md), [configuration.md](../specs/configuration.md), [asr-to-terminal.md](../specs/asr-to-terminal.md)

---

## Tasks

### 1. Config — new `engine` and `listen` blocks (`config.py`)

- [ ] Add `EngineConfig` dataclass:
  - `auto_start: bool = True`
- [ ] Add `ListenConfig` dataclass:
  - `end_of_utterance_mode: str = "trigger_word"`
  - `trigger_words: list[str]` — default list:
    `["submit", "enter", "validate", "send", "confirm", "go", "envoyer", "valider", "confirmer", "soumettre", "entree", "entrée"]`
  - `initial_silence_timeout_s: float = 10.0`
  - `end_of_speech_timeout_s: float = 5.0`
- [ ] Add `engine: EngineConfig` and `listen: ListenConfig` fields to `AppConfig`
- [ ] Parse `engine` block in `load_config`: read `auto_start`, default `True`
- [ ] Parse `listen` block in `load_config`:
  - Read `end_of_utterance_mode`; validate it is `"trigger_word"` or `"timeout"`, raise `ValueError` otherwise
  - Read `trigger_words`; if absent use the default list
  - Read `initial_silence_timeout_s` and `end_of_speech_timeout_s`
- [ ] Unit tests (`tests/test_config.py`):
  - Omitted `engine` and `listen` blocks → defaults used
  - `auto_start: false` → parsed correctly
  - `end_of_utterance_mode: "timeout"` → parsed correctly
  - Custom `trigger_words` list → replaces defaults
  - Invalid `end_of_utterance_mode` → `ValueError`

---

### 2. Shared trigger word detection (`speech_utils.py`)

- [ ] Create `src/asr_mcp/speech_utils.py` with:
  ```python
  def contains_trigger_word(transcript: str, words: list[str]) -> bool:
      """Case-insensitive substring match of any word in words against transcript."""
  ```
- [ ] Unit tests (`tests/test_speech_utils.py`):
  - Exact match (case-insensitive)
  - Substring match within a longer sentence
  - No match returns `False`
  - Empty word list returns `False`

---

### 3. Refactor `AsrToTerminal` to use `speech_utils`

- [ ] Replace `AsrToTerminal._contains_submit_word` with a call to
  `speech_utils.contains_trigger_word(transcript, self._submit_words)`
- [ ] Delete the private method
- [ ] Verify existing unit tests in `tests/test_asr_to_terminal.py` still pass
  (no new tests needed — behaviour is unchanged)

---

### 4. `ListenSession` (`listen_session.py`)

- [ ] Create `src/asr_mcp/listen_session.py`
- [ ] Implement `ListenResult` dataclass:
  - `transcript: str`
  - `end_reason: str`  — `"trigger_word"` | `"end_of_speech_timeout"` | `"initial_silence_timeout"`
- [ ] Implement `ListenSession.__init__`:
  - Accept `mode`, `trigger_words`, `initial_silence_timeout_s`, `end_of_speech_timeout_s`
  - Initialise `_committed: list[str] = []`
  - Initialise `asyncio.Event _done`
  - Initialise `_result: ListenResult | None = None`
- [ ] Implement `async on_result(result: ASRResult) -> None`:
  - **Both modes:** reset the end-of-speech timer (cancel + reschedule) on every event
  - **`trigger_word` mode — final with trigger word:** set `_result` with `end_reason="trigger_word"`,
    set `_done`; do not append to `_committed`
  - **Any mode — final without trigger word:** append `result.transcript` to `_committed`
  - **Interim:** no accumulation; timer reset only
- [ ] Implement `async wait() -> ListenResult`:
  - **`timeout` mode:** manage both timers concurrently:
    - Initial-silence timer: fires after `initial_silence_timeout_s` if no event has been
      received since session start — set `_result` with `end_reason="initial_silence_timeout"`
    - End-of-speech timer: started after the first ASR event, reset on each subsequent
      event; fires after `end_of_speech_timeout_s` of silence — set `_result` with
      `end_reason="end_of_speech_timeout"`
    - Whichever fires first sets `_done`
  - **`trigger_word` mode:** simply `await _done`
  - Once done, cancel any running timer tasks
  - Return `_result` with `transcript = " ".join(_committed)`
- [ ] Unit tests (`tests/test_listen_session.py`) — all timers must be injectable/patchable so the
  suite completes in < 5 s:
  - `trigger_word` mode: trigger word in final → session ends, utterance not in transcript
  - `trigger_word` mode: multiple finals without trigger word → all accumulated
  - `trigger_word` mode: interim events → not accumulated, session continues
  - `timeout` mode: no events → initial-silence timeout fires, empty transcript
  - `timeout` mode: events then silence → end-of-speech timeout fires, accumulated finals returned
  - `timeout` mode: interim resets the end-of-speech timer (silence measured from last interim)
  - Timer tasks are cancelled after session ends (no dangling tasks)

---

### 5. Split `client.py`: extract demo CLI into `asr_resource_client.py`

`client.py` currently acts as both a library module and a demo CLI entry point.
Adding `McpToolClient` alongside `AsrMcpClient` makes this a clean library; the
resource-subscribing CLI should live in its own explicitly named file.

- [ ] Create `src/asr_mcp/asr_resource_client.py`:
  - Move `_format_result`, `_run_client`, and `main()` from `client.py` into this file
  - `main()` is unchanged in behaviour
- [ ] Remove the moved code from `client.py`
- [ ] Update the `asr-mcp-client` entry point in `pyproject.toml` to point to
  `asr_mcp.asr_resource_client:main`
- [ ] Verify the existing demo client unit tests in `tests/test_client.py` still pass;
  update imports if needed

---

### 6. `McpToolClient` (`client.py`)

The existing `AsrMcpClient` covers long-lived resource subscriptions. A separate
`McpToolClient` handles the complementary pattern: connect, call a tool once,
return the parsed result, disconnect. This is the right client for `listen` (and
for any programmatic caller that wants to invoke `start`, `stop`, or `is_running`
without subscribing to events).

- [ ] Add `McpToolClient` to `src/asr_mcp/client.py`:
  - `__init__(self, server_url: str)`
  - `async call_tool(self, name: str, arguments: dict | None = None) -> dict`:
    - Open a `streamable_http_client` connection
    - Create and initialise a `ClientSession`
    - Call `session.call_tool(name, arguments or {})`
    - Parse the returned `CallToolResult`: if `isError`, raise `RuntimeError` with the
      error text; otherwise parse `result.content[0].text` as JSON and return the dict
    - Connection is opened and closed per call (no persistent state)
- [ ] Unit tests (`tests/test_client.py`):
  - Successful tool call → returns parsed dict
  - Tool error response → raises `RuntimeError`

---

### 7. `auto_start` wiring (`server.py`)

- [ ] In `run_server`: make `await engine.start()` conditional on `config.engine.auto_start`
- [ ] Pass `config.listen` into `create_mcp_server`

---

### 8. `listen` tool (`server.py`)

- [ ] Add a `_listen_lock: asyncio.Lock` in `create_mcp_server` to prevent concurrent `listen` calls
- [ ] Register the `listen` tool:
  - If `_listen_lock` already acquired → raise tool error `"A listen session is already in progress."`
  - If `engine.status()["running"]` → raise tool error `"ASR is already running. Stop it before calling listen."`
  - Create a `ListenSession` from `config.listen`
  - Wire `session.on_result` as the temporary engine result callback
  - `await engine.start()`
  - `result = await session.wait()`
  - In `finally`: `await engine.stop()` and restore the original engine result callback
  - Return `{"transcript": result.transcript, "end_reason": result.end_reason}`
- [ ] Unit tests (`tests/test_server.py`):
  - `listen` when engine already running → error response
  - `listen` when another `listen` in progress → error response
  - Successful `listen` with `trigger_word` mode → starts engine, returns transcript, stops engine
  - Successful `listen` with `timeout` mode → same lifecycle check
  - Engine is always stopped even if `ListenSession.wait()` raises

---

### 9. E2E tests for `listen` (`tests-e2e/test_file_asr.py`)

Uses `McpToolClient` to call the `listen` tool. The server is started with
`auto_start=false`; the tool manages the engine lifecycle internally, so no
resource subscription is needed on the test side.

- [ ] Update `helpers.py` (`start_mcp_server`) to accept and forward `engine` and `listen`
  config overrides when writing the temp config file
- [ ] Add `test_e2e_listen_trigger_word`:
  - Server config: `auto_start=false`, `end_of_utterance_mode="trigger_word"`,
    `trigger_words=["validate"]`
  - Audio fixture: `sample_submit.wav` (*"the sky is blue validate"*)
  - Call `listen` tool via `McpToolClient`
  - Assert `transcript == "the sky is blue"` and `end_reason == "trigger_word"`
- [ ] Add `test_e2e_listen_timeout`:
  - Server config: `auto_start=false`, `end_of_utterance_mode="timeout"`,
    `end_of_speech_timeout_s=2.0` (low value for test speed)
  - Audio fixture: `sample.wav` (*"the sky is blue"*)
  - Call `listen` tool via `McpToolClient`
  - Assert `transcript == "the sky is blue"` and `end_reason == "end_of_speech_timeout"`

---

### 10. Update specs

Specs were written before implementation; verify they match what was actually
built and patch any gaps.

- [ ] Update `specs/configuration.md`:
  - Verify `engine` and `listen` block tables match the implemented dataclasses
    and defaults exactly
- [ ] Update `specs/mcp-server.md`:
  - Verify `listen` tool output schema, error messages, and lifecycle diagrams
    match the implementation
- [ ] Update `specs/asr-to-terminal.md`:
  - Verify the `speech_utils` reference and file layout section are accurate
- [ ] Update `specs/project-structure.md`:
  - Add `asr_resource_client.py` — resource-subscribing CLI (replaces `client.py` CLI)
  - Add `speech_utils.py` — shared trigger word detection
  - Add `listen_session.py` — `ListenSession` and `ListenResult`
  - Update `client.py` description: now a pure library (`AsrMcpClient` + `McpToolClient`)
  - Update entry point: `asr-mcp-client = "asr_mcp.asr_resource_client:main"`
- [ ] Update `specs/demo-client.md`:
  - Update file name reference from `client.py` to `asr_resource_client.py`
  - Add note that `client.py` is now the shared library module
- [ ] Update `specs/specs.md`:
  - Mark spec entries as implemented where applicable

---

### 11. Update implementation details

After all code changes are done and working:

- [ ] Create `implementation-details/11-listen-tool.md`:
  - What was implemented: `speech_utils.py`, `listen_session.py`, `asr_resource_client.py`,
    `McpToolClient`, `auto_start` wiring, `listen` tool
  - Deviations from spec (if any)
  - Non-obvious decisions: timer cancellation strategy, callback swap mechanism,
    lock approach for concurrency guard
  - Known limitations
- [ ] Update `implementation-details/07-mcp-server.md`:
  - Document `auto_start` conditional wiring in `run_server`
  - Document `listen` tool: lock mechanism, engine lifecycle, callback swap
  - Document `config.listen` threading through `create_mcp_server`
- [ ] Update `implementation-details/08-demo-client.md`:
  - Document the move of `main()` / `_format_result` / `_run_client` from `client.py`
    to `asr_resource_client.py` and the updated entry point
- [ ] Update `implementation-details/10-asr-to-terminal.md`:
  - Document the removal of `_contains_submit_word` and delegation to `speech_utils`
- [ ] Update `implementation-details/implem.md`:
  - Add Plan 11 row to the index table

---

### 12. Update `plans.md` and `AGENTS.md`

- [ ] Mark Plan 11 as done in `plans/plans.md`
- [ ] Update `AGENTS.md`:
  - Repository layout: add `asr_resource_client.py`, `speech_utils.py`, `listen_session.py`;
    update `client.py` description to "pure library: AsrMcpClient + McpToolClient"
  - Entry points: update `asr-mcp-client` to point to `asr_resource_client:main`
  - Key design decisions: update the "Always-on" bullet to reflect `engine.auto_start`
    (default `true` preserves existing behaviour; `false` enables on-demand use via
    `start` tool or `listen` tool)
