---
code:
  - examples/asr_to_terminal/asr_to_terminal.py
  - examples/asr_to_terminal/terminal_typer.py
tests:
  - tests/examples/test_asr_to_terminal.py
---

# ASR to Terminal Specification

**Status:** Implemented

## Purpose

`AsrToTerminal` bridges the ASR MCP server with the active terminal window: it types the current speech *segment* progressively, overwriting text as the segment grows, and sends Enter when the segment closes.

Segmentation is **not done by the client** — it is owned by the server's `ASREngine` and exposed as the `asr://segment` resource (see [engine.md](engine.md) and [mcp-server.md](mcp-server.md)). `AsrToTerminal` simply subscribes to `asr://segment`, types each `transcript` as it changes, and on a closed segment (`is_final=true`) sends Enter and starts fresh.

When to close a segment (i.e. when Enter fires) is decided **on the server**, not here. Because the always-on stream is `utterance` mode by default, the useful "dictate freely, submit on a trigger word / after silence" behaviour comes from starting the server in a persistent dictation: set `engine.auto_start_dictation=true` and `engine.dictation_default_segmentation_mode` (`trigger_word` / `timeout`) plus the shared `engine.segmentation` trigger words / timeouts. Without that, a default (`utterance`) server makes `asr-to-terminal` press Enter after every final utterance. See [configuration.md](configuration.md).

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

All operations are async (they run `asyncio.create_subprocess_exec` to invoke the external tool and await completion).

**Display server resolution** (in priority order):

1. Explicit `display_server` constructor argument (`"x11"` or `"wayland"`)
2. `$XDG_SESSION_TYPE` environment variable
3. Raise `RuntimeError` if neither is available or the value is unrecognised

---

### `AsrToTerminal`

Owns an `AsrResourceClient` (subscribed to `asr://segment`) and a `TerminalTyper`. No session loop, no detector — the server closes segments.

**Constructor parameters:**

| Parameter        | Type        | Default                          |
|------------------|-------------|----------------------------------|
| `server_url`     | `str`       | `"http://127.0.0.1:8000/mcp"`    |
| `display_server` | `str\|None` | `None` (auto-detect)             |

---

## State Machine

One piece of mutable state:

- `_pending: str` — the segment text currently displayed in the terminal. Reset to `""` after Enter is sent (segment closed).

### On each `asr://segment` update

Each update carries `{transcript, is_final, end_reason, ...}`.

1. **Type the diff:** send `len(_pending) - common_prefix_len` Backspaces, type the differing suffix, then set `_pending = transcript`. (This handles both the growing open segment and the final text in one path.)
2. **If `is_final` is true (segment closed):** send Enter, then set `_pending = ""` so the next segment starts fresh.

In `trigger_word` mode the server already excludes the trigger-word utterance from the closed segment's `transcript`, so the trigger word is never typed. In `timeout` mode Enter fires when the server closes the segment after silence.

> **Note on character counting:** backspace count is based on `len(str)` (Unicode code points). Display-width issues (CJK wide chars, combining chars) are out of scope for v1.

---

## CLI

```
python -m examples.asr_to_terminal.asr_to_terminal [--server URL] [--display-server x11|wayland]
```

| Flag                | Default                              |
|---------------------|--------------------------------------|
| `--server`          | `http://127.0.0.1:8000/mcp`          |
| `--display-server`  | auto-detect via `$XDG_SESSION_TYPE`  |

Dictation mode (via `auto_start_dictation` / `dictation_default_segmentation_mode`), trigger words and timeouts are set on the **server** (`engine` config block), not here — every client of the server shares the same segmentation.

---

## Stderr logging

All diagnostic output goes to stderr so it does not pollute the active terminal session or interfere with injected keystrokes.

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
examples/
    asr_to_terminal/
        __init__.py
        terminal_typer.py    # TerminalTyper
        asr_to_terminal.py   # AsrToTerminal + main()
```

`AsrToTerminal` does no segmentation of its own — it consumes the server's `asr://segment` resource, which the engine's `Segmenter` produces (see [engine.md](engine.md)).

No `pyproject.toml` entry point: this is an example, not built into the wheel, so it is run with `python -m examples.asr_to_terminal.asr_to_terminal` (same pattern as the Gradio demo). It imports `AsrResourceClient` from `examples/mcp_client/`.

---

## Injectable keystroke sink

`AsrToTerminal(__init__)` accepts an optional `typer: KeystrokeSink` (a Protocol in `terminal_typer.py` with `type_text` / `backspace` / `send_enter`). When omitted it builds the real `TerminalTyper`; when supplied it uses that sink instead. This is how the e2e tests drive the full pipeline without real OS keystroke injection.

## E2E tests

Three real end-to-end tests in `tests-e2e/test_asr_to_terminal.py`, following the same `FileAudioSource → ASREngine → MCP server → AsrToTerminal` chain as the other ASR e2e tests. They inject an in-memory `RecordingTyper` (a `KeystrokeSink` that models a terminal input line: `type_text` appends, `backspace` trims, `send_enter` commits the line), so they run on any OS — **no `xterm`, `xdotool`, or X11 display required**. They still need a live Deepgram API key.

Each server is configured with `auto_start_dictation=true` and the `dictation_default_segmentation_mode` in the table (plus the matching trigger words / timeouts), so the always-on `asr://segment` stream aggregates for the test.

| Test | Fixture | dictation mode | Assertion |
|---|---|---|---|
| Text injection | `sample.wav` | `trigger_word` (impossible word) | `typer.line` contains `"the sky is blue"`; `typer.committed == []` (Enter never fired) |
| Submit word | `sample_submit.wav` | `trigger_word` (`validate`) | `typer.committed` non-empty (Enter fired); trigger word not in the committed text |
| Timeout | `sample.wav` | `timeout` | `typer.committed[-1]` contains `"the sky is blue"` (Enter fired on EOS) |

`sample_submit.wav` is a second audio fixture (same format as `sample.wav`) containing a submit word, e.g. `"the sky is blue validate"`.

---

## External dependencies

`xdotool` (X11) and `ydotool` (Wayland) are system packages the **runtime CLI** needs, not Python dependencies. On Wayland, `ydotoold` must also be running with access to `/dev/uinput`. The CLI should print a clear error if the required tool or daemon is unavailable. The e2e tests do not depend on them (they inject a `RecordingTyper`).

No new Python packages are required.
