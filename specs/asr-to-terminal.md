# ASR to Terminal Specification

## Purpose

`AsrToTerminal` bridges the ASR MCP client with the active terminal window: it
types interim and final transcripts progressively, overwriting interim text as
new results arrive, and sends Enter when the end of an utterance is detected.
End-of-utterance detection is delegated to `EndOfUtteranceDetector`, which
supports two modes: `trigger_word` (Enter fires when a submit word is spoken)
and `timeout` (Enter fires after a configurable silence period).

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

Owns an `AsrResourceClient`, a `TerminalTyper`, and a background session loop
that drives `EndOfUtteranceDetector` instances (one per utterance).

**Constructor parameters:**

| Parameter                   | Type        | Default                              |
|-----------------------------|-------------|--------------------------------------|
| `server_url`                | `str`       | `"http://127.0.0.1:8000/mcp"`        |
| `display_server`            | `str\|None` | `None` (auto-detect)                 |
| `mode`                      | `str`       | `"trigger_word"`                     |
| `trigger_words`             | `list[str]` | see *Default trigger words* below    |
| `end_of_speech_timeout_s`   | `float`     | `5.0`                                |
| `initial_silence_timeout_s` | `float`     | `10.0`                               |

**Default trigger words** (case-insensitive, only used in `trigger_word` mode):

```python
["submit", "enter", "validate", "send", "confirm", "go",
 "envoyer", "valider", "confirmer", "soumettre", "entree", "entrée"]
```

---

## State Machine

One piece of mutable state:

- `_pending: str` — the interim text currently displayed in the terminal.
  Reset to `""` after every final result or Enter.

### On any ASR event

Every event (interim and final) is forwarded to the current
`EndOfUtteranceDetector` session via `session.on_result(result)` so that timers
stay synchronised with the actual speech stream.

### On interim result

1. Send `len(_pending)` Backspace keystrokes (erase previous interim).
2. Type the new interim transcript.
3. Set `_pending = new_transcript`.

### On final result (no trigger word / no timeout yet)

1. Send `len(_pending) - common_prefix_len` Backspace keystrokes.
2. Type the suffix that differs.
3. Set `_pending = ""` (text committed; next interim starts fresh).

### Session loop (background task)

Runs continuously while `AsrToTerminal` is active:

1. Create a new `EndOfUtteranceDetector` for the current utterance.
2. `await session.wait()` — blocks until end-of-utterance is signalled.
3. Erase `_pending` (send `len(_pending)` Backspaces).
4. Send Enter.
5. Set `_pending = ""`.
6. Go to step 1 (start the next utterance).

#### `trigger_word` mode

The session ends when a final result contains a submit word. The submit word
utterance is not typed (the trigger detection fires before the text would be
committed). All preceding finals have already been typed progressively.

#### `timeout` mode

The session ends after `end_of_speech_timeout_s` seconds of silence following
the last ASR event (or `initial_silence_timeout_s` if nothing was heard at all).
Enter fires automatically after the silence elapses.

> **Note on character counting:** backspace count is based on `len(str)` (Unicode
> code points). Display-width issues (CJK wide chars, combining chars) are out of
> scope for v1.

---

## CLI

```
asr-to-terminal [--server URL] [--display-server x11|wayland]
                [--mode trigger_word|timeout] [--trigger-words WORD ...]
                [--end-of-speech-timeout SECONDS] [--initial-silence-timeout SECONDS]
```

| Flag                        | Default                              |
|-----------------------------|--------------------------------------|
| `--server`                  | `http://127.0.0.1:8000/mcp`          |
| `--display-server`          | auto-detect via `$XDG_SESSION_TYPE`  |
| `--mode`                    | `trigger_word`                       |
| `--trigger-words`           | (built-in defaults, see above)       |
| `--end-of-speech-timeout`   | `5.0`                                |
| `--initial-silence-timeout` | `10.0`                               |

When `--trigger-words` is provided it **replaces** the default list entirely.
`--trigger-words`, `--end-of-speech-timeout`, and `--initial-silence-timeout`
are only active in `trigger_word` and `timeout` modes respectively.

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
    speech_utils.py                  # contains_trigger_word() — shared with listen tool
    terminal_typer.py                # TerminalTyper
    end_of_utterance_detector.py     # EndOfUtteranceDetector + UtteranceResult
    asr_to_terminal.py               # AsrToTerminal + main()
```

`AsrToTerminal` delegates end-of-utterance logic to `EndOfUtteranceDetector`
(see [end-of-utterance-detector.md](end-of-utterance-detector.md)). The submit
word list passed to `EndOfUtteranceDetector` remains independent from the
`listen` tool's trigger word list.

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
