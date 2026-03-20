# Plan 13 — EndOfUtteranceDetector & AsrToTerminal Mode Support

## What was implemented

- **Renamed** `listen_session.py` → `end_of_utterance_detector.py`: `ListenResult` → `UtteranceResult`, `ListenSession` → `EndOfUtteranceDetector`. Logic is unchanged.
- **Updated** `server.py` to import `EndOfUtteranceDetector` from the new module.
- **Renamed** test file `test_listen_session.py` → `test_end_of_utterance_detector.py` with updated imports and references.
- **Refactored** `AsrToTerminal`:
  - `submit_words` / `_submit_words` → `trigger_words` / `_trigger_words`
  - Added constructor params `mode`, `end_of_speech_timeout_s`, `initial_silence_timeout_s`
  - Added `_current_session: EndOfUtteranceDetector | None` and `_session_loop_task`
  - Added `_session_loop()`: background loop that creates one `EndOfUtteranceDetector` per utterance, waits for it, then sends backspaces + Enter under the lock, then loops
  - Removed `contains_trigger_word` check from `_handle_event`; all finals now commit normally
  - `_on_event` feeds every event (interim + final) to `_current_session.on_result()` after the typing logic, outside the lock (so the session can signal done without deadlocking the lock)
  - `start()` launches `_session_loop_task`; `stop()` cancels and awaits it
- **Updated** `asr-to-terminal` CLI: `--submit-words` → `--trigger-words`, added `--mode`, `--end-of-speech-timeout`, `--initial-silence-timeout`
- **Added** `test_e2e_asr_to_terminal_timeout` in `tests-e2e/test_asr_to_terminal.py`

## Deviations from spec

- **Trigger word visual behavior change**: In the old implementation, when a trigger word final arrived, `_handle_event` would erase the pending interim and send Enter (no text committed to terminal). In the new design, `_handle_event` treats all finals the same (commit the text, reset `_pending`), and the session loop sends `len(_pending)` backspaces + Enter. Since `_pending` is always `""` after a final commit, the session loop sends 0 backspaces + Enter — meaning the trigger word utterance text is visible in the terminal before Enter is sent. This is an intentional simplification: the session loop is designed primarily to erase pending *interim* text when EOS fires; for trigger_word mode the text was already committed.

- **`_on_event` feeds session outside the lock**: The plan says "if `self._current_session` is set: ... `await self._current_session.on_result(result)`". This is done *after* releasing `self._lock` (not inside `async with self._lock`). Reason: `EndOfUtteranceDetector.on_result` may set `_done` which unblocks `_session_loop.wait()`, which then tries to acquire `self._lock`. Feeding the session inside the lock would deadlock.

## Non-obvious decisions

- **Lock discipline**: `_handle_event` runs under `self._lock`; `_session_loop` acquires `self._lock` to send backspaces/Enter. `_current_session.on_result()` is called *outside* the lock to avoid deadlock (see above).

- **`_session_loop` CancelledError handling**: The loop catches `CancelledError` to set `_current_session = None` before re-raising, ensuring no dangling session reference after `stop()`.

- **Why interim events feed the detector**: In `timeout` mode, the EOS timer resets on every event including interims. Without feeding interims to the detector, a pause mid-word would not reset the timer and Enter would fire too early.

- **Test strategy for session loop**: The session loop tests use `asyncio.Event` to block the second iteration (preventing infinite tight loop), and `AsyncMock(side_effect=...)` to track `send_enter` calls while still setting the event. A fresh `_mock_client()` helper with `AsyncMock` start/stop is created per test since `AsrToTerminal.start()` awaits `_client.start()`.

## Known limitations

- In trigger_word mode, the trigger word utterance text is briefly visible in the terminal before Enter fires (0 backspaces are sent since `_pending` is cleared by `_handle_event`). A future plan could choose to type trigger_word finals into `_pending` without committing them, so the session loop can erase them.
- The e2e timeout test requires `trailing_silence_s=3.0` on the server plus a 3.5s sleep to ensure the EOS timer fires and the keystroke is injected before the assertion. This is inherently timing-sensitive.
