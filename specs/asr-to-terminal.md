# ASR to Terminal Specification

## Purpose

`AsrToTerminal` bridges the ASR MCP client with the active terminal window: it
types interim and final transcripts progressively, overwriting interim text as
new results arrive, and triggers Enter (without typing the utterance) when a
configurable submit word is detected.

---

## Components

### `TerminalTyper`

Thin abstraction over the OS-level keystroke injection tool.

| Display server | Tool      |
|----------------|-----------|
| `x11`          | `xdotool` |
| `wayland`      | `ydotool` |

**Operations:**

- `type_text(text: str)` — inject characters into the active window
- `backspace(n: int)` — send *n* Backspace keystrokes
- `send_enter()` — send a single Return keystroke

All operations are async (they run `asyncio.create_subprocess_exec` to invoke
the external tool and await completion).

**Display server resolution** (in priority order):

1. Explicit `display_server` constructor argument (`"x11"` or `"wayland"`)
2. `$XDG_SESSION_TYPE` environment variable
3. Raise `RuntimeError` if neither is available or the value is unrecognised

---

### `AsrToTerminal`

Owns an `AsrResourceClient` and a `TerminalTyper`. Implements the progressive text
injection state machine.

**Constructor parameters:**

| Parameter        | Type        | Default                              |
|------------------|-------------|--------------------------------------|
| `server_url`     | `str`       | `"http://127.0.0.1:8080/mcp"`        |
| `submit_words`   | `list[str]` | see *Default submit words* below     |
| `display_server` | `str\|None` | `None` (auto-detect)                 |

**Default submit words** (case-insensitive):

```python
["submit", "enter", "validate", "send", "confirm", "go",
 "envoyer", "valider", "confirmer", "soumettre", "entree", "entrée"]
```

---

## State Machine

Two pieces of state are maintained:

- `_pending: str` — the interim text currently displayed in the terminal
  (not yet committed). Reset to `""` after every final or Enter.

### On interim result

1. Send `len(_pending)` Backspace keystrokes (erase previous interim).
2. Type the new interim transcript.
3. Set `_pending = new_transcript`.

### On final result

**Case A — transcript contains a submit word** (case-insensitive substring
match, detected via `speech_utils.contains_trigger_word`):

1. Send `len(_pending)` Backspace keystrokes (erase the interim).
2. Send Enter.
3. Set `_pending = ""`.

**Case B — no submit word:**

1. Send `len(_pending)` Backspace keystrokes.
2. Type the final transcript.
3. Set `_pending = ""` (text is now committed; next interim starts fresh).

> **Note on character counting:** backspace count is based on `len(str)` (Unicode
> code points). Display-width issues (CJK wide chars, combining chars) are out of
> scope for v1.

---

## CLI

```
asr-to-terminal [--server URL] [--submit-words WORD ...] [--display-server x11|wayland]
```

| Flag               | Default                              |
|--------------------|--------------------------------------|
| `--server`         | `http://127.0.0.1:8080/mcp`          |
| `--submit-words`   | (built-in defaults, see above)       |
| `--display-server` | auto-detect via `$XDG_SESSION_TYPE`  |

When `--submit-words` is provided it **replaces** the default list entirely.

---

## Stderr logging

All diagnostic output goes to stderr so it does not pollute the active terminal
session or interfere with injected keystrokes.

| Event                        | Log line                                    |
|------------------------------|---------------------------------------------|
| Connected to MCP server      | `[INFO] Connected to <url>`                 |
| Interim received             | `[INTERIM] <transcript>`                    |
| Final committed              | `[FINAL] <transcript>`                      |
| Submit word triggered        | `[SUBMIT] <transcript> → Enter`             |
| Connection lost / retrying   | `[WARN] Connection lost, retrying…`         |
| Disconnected (Ctrl-C)        | `[INFO] Disconnected`                       |

---

## File layout

```
src/asr_mcp/
    speech_utils.py       # contains_trigger_word() — shared with listen tool
    terminal_typer.py     # TerminalTyper
    asr_to_terminal.py    # AsrToTerminal + main()
```

`AsrToTerminal` uses `speech_utils.contains_trigger_word` for submit word
detection instead of a private method. The submit word list it passes remains
independent from the `listen` tool's trigger word list.

Entry point registered in `pyproject.toml`:

```toml
asr-to-terminal = "asr_mcp.asr_to_terminal:main"
```

---

## E2E tests

Two real end-to-end tests in `tests-e2e/test_asr_to_terminal.py`, following the
same `FileAudioSource → ASREngine → MCP server → AsrToTerminal` chain as the
existing ASR e2e tests. Require `xdotool` and a live X11 display.

| Test | Fixture | Capture method | Assertion |
|---|---|---|---|
| Text injection | `sample.wav` | `xterm -e 'cat > /tmp/...'` + Ctrl-D | file content == `"the sky is blue"` |
| Submit word | `sample_submit.wav` | `xterm -e 'bash -c "read line; echo GOT:$line > /tmp/..."'` | file content == `"GOT:"` (Enter fired, no text) |

`sample_submit.wav` is a second audio fixture (same format as `sample.wav`)
containing a submit word, e.g. `"the sky is blue validate"`.

---

## External dependencies

`xdotool` (X11) and `ydotool` (Wayland) are system packages, not Python
dependencies. The CLI should print a clear error if the required tool is
missing.

No new Python packages are required.
