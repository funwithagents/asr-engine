# Plan 10 — ASR to Terminal

Implement `AsrToTerminal`: progressive speech-to-terminal injection with submit
word detection, plus a real e2e test driven by audio fixtures.

---

## Tasks

### 1. `TerminalTyper` (`terminal_typer.py`)

- [x] Implement `TerminalTyper.__init__(display_server: str | None = None)`:
  - Resolve display server: explicit arg → `$XDG_SESSION_TYPE` → `RuntimeError`
  - Validate value is `"x11"` or `"wayland"`; raise `RuntimeError` otherwise
- [x] Implement `async type_text(text: str)`:
  - X11: `xdotool type --clearmodifiers -- <text>`
  - Wayland: `ydotool type --key-delay 0 -- <text>`
- [x] Implement `async backspace(n: int)`:
  - No-op if `n == 0`
  - X11: `xdotool key --clearmodifiers --repeat <n> BackSpace`
  - Wayland: `ydotool key --repeat <n> BackSpace`
- [x] Implement `async send_enter()`:
  - X11: `xdotool key --clearmodifiers Return`
  - Wayland: `ydotool key Return`
- [x] On subprocess non-zero exit: log warning to stderr, do not raise

---

### 2. `AsrToTerminal` + `main()` (`asr_to_terminal.py`)

- [x] Implement `AsrToTerminal.__init__`:
  - Accept `server_url`, `submit_words`, `display_server`
  - Instantiate `TerminalTyper` and `AsrMcpClient`
  - Initialise `_pending: str = ""`
- [x] Implement `_contains_submit_word(transcript: str) -> bool`:
  - Case-insensitive substring match against each word in `submit_words`
- [x] Implement `async _on_event(payload: dict)`:
  - Extract `transcript` and `is_final`
  - **Interim:** backspace `len(_pending)`, type new transcript, update `_pending`
  - **Final — submit word:** backspace `len(_pending)`, send Enter, reset `_pending = ""`
  - **Final — no submit word:** backspace `len(_pending)`, type transcript, reset `_pending = ""`
  - Log each case to stderr
- [x] Implement `async start()` / `async stop()` delegating to `AsrMcpClient`
- [x] Implement `main()`:
  - Parse `--server`, `--submit-words` (nargs=`+`), `--display-server`
  - When `--submit-words` absent use the built-in default list
  - Instantiate `AsrToTerminal`, `start()`, sleep forever, `stop()` on `KeyboardInterrupt`
- [x] Register entry point in `pyproject.toml`: `asr-to-terminal = "asr_mcp.asr_to_terminal:main"`

---

### 3. Unit tests (`tests/test_asr_to_terminal.py`)

All `TerminalTyper` calls mocked; suite must complete in < 5 s.

- [x] `TerminalTyper` resolution: auto-detect from `$XDG_SESSION_TYPE`, explicit arg overrides, unknown value raises `RuntimeError`
- [x] `_contains_submit_word`: case-insensitive match, substring match, no false positives
- [x] State machine — first interim (no prior pending): no backspace sent, text typed, `_pending` set
- [x] State machine — second interim: backspace previous, type new, `_pending` updated
- [x] State machine — final, no submit word: backspace pending, type text, `_pending` reset to `""`
- [x] State machine — final, submit word: backspace pending, Enter sent, nothing typed, `_pending` reset to `""`

---

### 4. E2E fixture

- [x] Add `tests-e2e/fixtures/sample_submit.wav`: spoken utterance that ends with a
  submit word (e.g. `"the sky is blue validate"`). Same format as `sample.wav`
  (16 kHz, mono, 16-bit PCM). Committed to the repo.
- [x] Document expected transcript in `tests-e2e/fixtures/README.md`

---

### 5. E2E tests (`tests-e2e/test_asr_to_terminal.py`)

Follows the same `FileAudioSource → ASREngine → MCP server → AsrToTerminal`
chain as `test_file_asr.py`. Requires `xdotool` and a live X11 display.

**Text injection test** (`test_e2e_terminal_typing`):

1. Launch `xterm -e 'cat > /tmp/asr_e2e_typing.txt'` as a background subprocess
2. Wait for the window with `xdotool search --sync --pid <pid>`
3. Focus it with `xdotool windowfocus`
4. Start `AsrToTerminal` pointing at the test MCP server
5. Feed `sample.wav` through the engine; wait for the final event
6. Send Ctrl-D via `xdotool key ctrl+d` to flush `cat`
7. Assert `/tmp/asr_e2e_typing.txt` normalises to `"the sky is blue"`

**Submit word test** (`test_e2e_terminal_submit`):

1. Launch `xterm -e "bash -c 'read line; echo GOT:$line > /tmp/asr_e2e_submit.txt'"` as a background subprocess
2. Focus the window (same as above)
3. Start `AsrToTerminal` with `submit_words=["validate"]`
4. Feed `sample_submit.wav` through the engine; wait for the final event
5. Allow short settling time for xdotool to complete
6. Assert `/tmp/asr_e2e_submit.txt` contains `"GOT:"` (Enter fired, no committed text — interim was erased before submit)

Both tests: clean up subprocess in `finally`, timeout 30 s.
