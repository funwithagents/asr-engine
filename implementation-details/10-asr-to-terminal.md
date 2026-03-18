# Implementation Details — Plan 10: ASR to Terminal

## What was implemented

Two new source files and their tests:

- `src/asr_mcp/terminal_typer.py` — `TerminalTyper`: thin async wrapper over `xdotool` (X11) and `ydotool` (Wayland) for keystroke injection (`type_text`, `backspace`, `send_enter`).
- `src/asr_mcp/asr_to_terminal.py` — `AsrToTerminal`: progressive text-injection state machine that owns a `TerminalTyper` and an `AsrMcpClient`. Also contains `main()` CLI.
- `tests/test_asr_to_terminal.py` — 14 unit tests covering resolution logic, submit-word detection, and all state machine branches.
- `tests-e2e/test_asr_to_terminal.py` — two real e2e tests using xterm + xdotool.
- `tests-e2e/fixtures/sample_submit.wav` — pre-recorded TTS fixture (16 kHz, mono, 16-bit PCM) containing `"the sky is blue validate"`.
- Entry point `asr-to-terminal` added to `pyproject.toml`.

## Deviations from spec

**`_on_event` wiring in e2e tests:** The spec didn't specify how to intercept the final event inside `AsrToTerminal` to know when transcription is done. The e2e tests patch both `atr._on_event` and `atr._client._subscriber._on_event` to wrap the original handler with a `final_event.set()` call. This is a test-only detail — production code is untouched.

**`type_text` skips empty strings:** Added an early-return guard for empty text to avoid spawning `xdotool type -- ""` (which would be a no-op but noisy).

## Non-obvious decisions

- **`_contains_submit_word` uses substring match, not word-boundary match.** Spec says "substring match", so `"goat"` would match submit word `"go"`. This is intentional per spec (v1 scope note).
- **`backspace(0)` is a no-op** — avoids spawning a subprocess for the common first-interim case where nothing has been typed yet.
- **Stderr for all logging** — all output goes to stderr so it doesn't pollute stdin of the active terminal window being typed into.
- **Display server normalised to lowercase** — `os.environ.get("XDG_SESSION_TYPE")` may return `"X11"` on some desktops; `.lower()` before validation handles this.

## Known limitations

- Character counting uses `len(str)` (Unicode code points). Wide characters (CJK, emoji) and combining characters will cause incorrect backspace counts — deferred to v2 as per spec.
- The e2e tests require a real X11 display and `xdotool` installed; they will fail in headless CI without a virtual display (e.g. Xvfb).
- The e2e tests monkey-patch internal attributes (`_subscriber._on_event`) to observe final events — fragile if the subscriber internals change.
