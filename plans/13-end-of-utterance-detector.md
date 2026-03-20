# Plan 13 — EndOfUtteranceDetector & AsrToTerminal Mode Support

Rename `ListenSession` → `EndOfUtteranceDetector` (and `ListenResult` →
`UtteranceResult`), move it to a dedicated file, and wire it into `AsrToTerminal`
so that `asr-to-terminal` supports both `trigger_word` and `timeout`
end-of-utterance modes.

Specs: [end-of-utterance-detector.md](../specs/end-of-utterance-detector.md),
[asr-to-terminal.md](../specs/asr-to-terminal.md)

---

## Tasks

### 1. Rename and relocate `ListenSession` → `EndOfUtteranceDetector`

- [ ] Create `src/asr_mcp/end_of_utterance_detector.py`:
  - Move `ListenResult` → `UtteranceResult` (same fields: `transcript: str`,
    `end_reason: str`)
  - Move `ListenSession` → `EndOfUtteranceDetector` (same logic, new name)
  - Update all internal references (`ListenResult` → `UtteranceResult`)
- [ ] Delete `src/asr_mcp/listen_session.py`
- [ ] Update `server.py`:
  - Replace `from asr_mcp.listen_session import ListenSession` with
    `from asr_mcp.end_of_utterance_detector import EndOfUtteranceDetector, UtteranceResult`
  - Replace all `ListenSession(...)` calls with `EndOfUtteranceDetector(...)`
- [ ] Update `tests/test_listen_session.py` → rename to
  `tests/test_end_of_utterance_detector.py`; update all imports and class
  references inside
- [ ] Update `AGENTS.md`:
  - Repository layout: rename `listen_session.py` → `end_of_utterance_detector.py`,
    update description (`ListenSession` / `ListenResult` → `EndOfUtteranceDetector`
    / `UtteranceResult`)

---

### 2. Add `EndOfUtteranceDetector` session loop to `AsrToTerminal`

- [ ] Rename `submit_words` → `trigger_words` throughout `asr_to_terminal.py`
  (constructor parameter, instance attribute `_submit_words` → `_trigger_words`,
  internal usages)
- [ ] Update `AsrToTerminal.__init__` to accept new parameters:
  - `mode: str = "trigger_word"`
  - `end_of_speech_timeout_s: float = 5.0`
  - `initial_silence_timeout_s: float = 10.0`
  - Store them as instance attributes alongside existing ones
  - Add `self._current_session: EndOfUtteranceDetector | None = None`
- [ ] Add `async _session_loop(self) -> None`:
  - Loop indefinitely:
    1. Create a new `EndOfUtteranceDetector` with the configured mode and timeouts;
       assign to `self._current_session`
    2. `await self._current_session.wait()`
    3. Under `self._lock`: send `len(self._pending)` Backspaces, send Enter,
       set `self._pending = ""`
    4. Set `self._current_session = None`, then loop back
- [ ] Update `AsrToTerminal._on_event`:
  - After the existing interim/final typing logic, if `self._current_session`
    is set: construct `ASRResult(transcript=transcript, is_final=is_final)` and
    `await self._current_session.on_result(result)` — feeds all events (interim
    + final) to the detector so timers stay in sync
  - Remove the existing `contains_trigger_word` call and associated Enter logic
    from `_handle_event` (the session loop now owns the Enter decision)
- [ ] Update `AsrToTerminal.start`:
  - Launch `_session_loop` as a background asyncio task; store as
    `self._session_loop_task`
- [ ] Update `AsrToTerminal.stop`:
  - Cancel `self._session_loop_task` and await it (suppress `CancelledError`)

---

### 3. Update the `asr-to-terminal` CLI

- [ ] Rename `--submit-words` → `--trigger-words` in `argparse`
- [ ] Add arguments to `argparse` in `asr_to_terminal.main()`:
  - `--mode {trigger_word,timeout}` (default: `trigger_word`)
  - `--end-of-speech-timeout SECONDS` (default: `5.0`, type `float`)
  - `--initial-silence-timeout SECONDS` (default: `10.0`, type `float`)
- [ ] Pass new args through `_run()` → `AsrToTerminal()`

---

### 4. Update unit tests (`tests/test_asr_to_terminal.py`)

- [ ] Update existing tests to use new `AsrToTerminal` constructor signature
  where needed (new params have defaults, so most tests need no change)
- [ ] Add test: `trigger_word` mode — submit word in final → session loop fires
  Enter (mock `EndOfUtteranceDetector.wait` to return immediately with
  `end_reason="trigger_word"`)
- [ ] Add test: `timeout` mode — session loop fires Enter on EOS timeout (mock
  `EndOfUtteranceDetector.wait` to return with `end_reason="end_of_speech_timeout"`)
- [ ] Add test: `start()` launches `_session_loop_task`; `stop()` cancels it
- [ ] Verify all existing tests still pass (no behaviour change for the typing
  logic itself)

---

### 5. Update e2e tests (`tests-e2e/test_asr_to_terminal.py`)

Existing e2e tests cover `trigger_word` mode implicitly (submit word detection).
Add a `timeout` mode e2e test.

- [ ] Add `test_e2e_asr_to_terminal_timeout`:
  - Instantiate `AsrToTerminal` with `mode="timeout"`,
    `end_of_speech_timeout_s=2.0` (short for test speed)
  - Audio fixture: `sample.wav` (*"the sky is blue"*, no submit word)
  - Capture target: `xterm -e 'bash -c "read line; echo GOT:$line > /tmp/..."'`
  - Assert file content == `"GOT:the sky is blue"` (text typed then Enter sent
    by timeout)

---

### 6. Update specs

- [ ] Update `specs/specs.md`:
  - Mark spec 11 (`end-of-utterance-detector.md`) as `yes` (implemented)
- [ ] Update `specs/project-structure.md`:
  - Replace `listen_session.py` with `end_of_utterance_detector.py`; update
    description (`EndOfUtteranceDetector` + `UtteranceResult`)

---

### 7. Update documentation indexes

- [ ] Add Plan 13 row to `plans/plans.md` (mark as done when complete)
- [ ] Add Plan 13 row to `implementation-details/implem.md` (mark as not yet
  written; write the file after implementation)
- [ ] Write `implementation-details/13-end-of-utterance-detector.md` after all
  code is done:
  - What was implemented: rename, session loop, new CLI args
  - Deviations from spec (if any)
  - Non-obvious decisions: why `_on_event` feeds both interim and final to the
    detector; lock discipline between session loop and event handler
  - Known limitations
