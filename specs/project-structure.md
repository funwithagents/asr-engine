# Project Structure

## Repository Layout

```
asr-mcp/
├── pyproject.toml               # uv project definition, dependencies, entry points
├── config.json                  # Example config (not committed with secrets)
├── config.example.json          # Template config checked into version control
├── specs/                       # This specification folder
│   ├── overview.md
│   ├── architecture.md
│   ├── configuration.md
│   ├── mcp-server.md
│   ├── asr-module-interface.md
│   ├── deepgram-module.md
│   ├── demo-client.md
│   └── project-structure.md
└── src/
    └── asr_mcp/
        ├── __init__.py
        ├── server.py                # MCP server setup, resource + tool registration (incl. listen tool)
        ├── engine.py                # ASR engine: orchestrates audio capture + module
        ├── audio.py                 # Audio capture (sounddevice, thread + asyncio.Queue)
        ├── config.py                # Config loading and validation (dataclasses)
        ├── cli.py                   # Entry point for the server (argparse)
        ├── client.py                # Library: AsrMcpClient (resource subscription) + McpToolClient (tool calls)
        ├── asr_resource_client.py   # Demo CLI: subscribe to asr://result, log results
        ├── speech_utils.py          # contains_trigger_word() — shared trigger word detection
        ├── listen_session.py        # ListenSession + ListenResult — end-of-utterance state machine
        ├── terminal_typer.py        # TerminalTyper — keystroke injection (xdotool/ydotool)
        ├── asr_to_terminal.py       # AsrToTerminal state machine + CLI entry point
        └── modules/
            ├── __init__.py          # REGISTRY dict
            ├── base.py              # ASRModule ABC + ASRResult dataclass
            ├── deepgram_v1.py       # Deepgram Listen v1 (Nova models)
            └── deepgram_v2.py       # Deepgram Listen v2 (Flux models)
```

## Entry Points (pyproject.toml)

```toml
[project.scripts]
asr-mcp-server = "asr_mcp.cli:main"
asr-mcp-client = "asr_mcp.asr_resource_client:main"
asr-to-terminal = "asr_mcp.asr_to_terminal:main"
```

## Key Dependencies

| Package | Purpose |
|---|---|
| `mcp[cli]` | Official MCP Python SDK (server + client) |
| `sounddevice` | Cross-platform audio capture |
| `numpy` | PCM buffer handling |
| `deepgram-sdk` | Deepgram streaming API |
| `httpx` | HTTP client (used internally by MCP SDK) |

## Python Version

- Minimum: Python 3.11 (for `asyncio` improvements and `tomllib`)
- Recommended: Python 3.12+
