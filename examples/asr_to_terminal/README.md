# ASR to terminal

`asr_to_terminal` subscribes to the MCP server's `asr://segment` resource and types the segment into the currently focused terminal window.

As the segment changes, the bridge backspaces the changed suffix and types its replacement. When the server closes the segment (`is_final=true`), it sends Enter and begins a fresh line. All segmentation remains in the server's `ASREngine`; this client only renders the latest segment.

> The example injects real keystrokes into the focused window. Verify the target window before speaking, especially when a final segment will send Enter.

## System requirements

- X11: `xdotool`
- Wayland: `ydotool`, a running `ydotoold`, and access to `/dev/uinput`

These are external system programs, not Python dependencies.

## Configure the server

For continuous aggregated segments, enable a persistent dictation in the server configuration:

```json
{
  "engine": {
    "auto_start": true,
    "auto_start_dictation": true,
    "dictation_default_segmentation_mode": "trigger_word",
    "segmentation": {
      "trigger_words": ["submit", "validate", "send"]
    },
    "module": {
      "type": "deepgram_v1",
      "api_key_env": "DEEPGRAM_API_KEY"
    }
  }
}
```

Use `timeout` instead of `trigger_word` when silence should submit the line. If `auto_start_dictation` is false, the normal always-on stream uses `utterance` mode and each final utterance sends Enter separately.

Start the configured server before the bridge:

```bash
export DEEPGRAM_API_KEY="..."
uv run asr-engine-mcp --config config.json
```

## Run

From the repository root:

```bash
uv run python -m examples.asr_to_terminal.asr_to_terminal
```

Options:

```text
--server URL                   MCP endpoint (default: http://127.0.0.1:8000/mcp)
--display-server x11|wayland   Override $XDG_SESSION_TYPE auto-detection
```

For example:

```bash
uv run python -m examples.asr_to_terminal.asr_to_terminal \
  --server http://192.168.1.10:8000/mcp \
  --display-server wayland
```

Diagnostics are written to stderr so they do not mix with the injected terminal input.
