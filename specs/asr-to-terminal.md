---
code:
  - src/asr_mcp/asr_to_terminal.py
  - src/asr_mcp/terminal_typer.py
tests:
  - tests/test_asr_to_terminal.py
---

# ASR to Terminal Specification

**Status:** Implemented

## Purpose

`AsrToTerminal` bridges the ASR MCP server with the active terminal window: it
types the current speech *segment* progressively, overwriting text as the
segment grows, and sends Enter when the segment closes.

Segmentation is **no longer done by the client** — it is owned by the server's
`ASREngine` and exposed as the `asr://segment` resource (see
[engine.md](engine.md) and [mcp-server.md](mcp-server.md)). `AsrToTerminal`
simply subscribes to `asr://segment`, types each `transcript` as it changes, and
on a closed segment (`is_final=true`) sends Enter and starts fresh. The segment
mode (`trigger_word` / `timeout` / `utterance`) and its trigger words / timeouts
are configured **on the server** via the `engine` config block — not on the
`asr-to-terminal` CLI.

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

Owns an `AsrResourceClient` (subscribed to `asr://segment`) and a `TerminalTyper`.
No session loop, no detector — the server closes segments.

**Constructor parameters:**

| Parameter        | Type        | Default                          |
|------------------|-------------|----------------------------------|
| `server_url`     | `str`       | `"http://127.0.0.1:8000/mcp"`    |
| `display_server` | `str\|None` | `None` (auto-detect)             |

---

## State Machine

One piece of mutable state:

- `_pending: str` — the segment text currently displayed in the terminal.
  Reset to `""` after Enter is sent (segment closed).

### On each `asr://segment` update

Each update carries `{transcript, is_final, end_reason, ...}`.

1. **Type the diff:** send `len(_pending) - common_prefix_len` Backspaces, type
   the differing suffix, then set `_pending = transcript`. (This handles both the
   growing open segment and the final text in one path.)
2. **If `is_final` is true (segment closed):** send Enter, then set
   `_pending = ""` so the next segment starts fresh.

In `trigger_word` mode the server already excludes the trigger-word utterance
from the closed segment's `transcript`, so the trigger word is never typed. In
`timeout` mode Enter fires when the server closes the segment after silence.

> **Note on character counting:** backspace count is based on `len(str)` (Unicode
> code points). Display-width issues (CJK wide chars, combining chars) are out of
> scope for v1.

---

## CLI

```
asr-to-terminal [--server URL] [--display-server x11|wayland]
```

| Flag                | Default                              |
|---------------------|--------------------------------------|
| `--server`          | `http://127.0.0.1:8000/mcp`          |
| `--display-server`  | auto-detect via `$XDG_SESSION_TYPE`  |

Segment mode, trigger words and timeouts are set on the **server** (`engine`
config block), not here — every client of the server shares the same
segmentation.

---

## Stderr logging

All diagnostic output goes to stderr so it does not pollute the active terminal
session or interfere with injected keystrokes.

| Event                        | Log line                                    |
|------------------------------|---------------------------------------------|
| Connected to MCP server      | `[INFO] Connected to <url>`                 |
| Segment update (open)        | `[SEGMENT] <transcript>`                    |
| Segment closed → Enter       | `[ENTER] <transcript> (<end_reason>)`       |
| Connection lost / retrying   | `[WARN] Connection lost, retrying…`         |
| Disconnected (Ctrl-C)        | `[INFO] Disconnected`                       |

---

## File layout

```
src/asr_mcp/
    terminal_typer.py    # TerminalTyper
    asr_to_terminal.py   # AsrToTerminal + main()
```

`AsrToTerminal` does no segmentation of its own — it consumes the server's
`asr://segment` resource, which the engine's `Segmenter` produces (see
[engine.md](engine.md)).

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
