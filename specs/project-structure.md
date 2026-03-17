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
        ├── server.py            # MCP server setup, resource + tool registration
        ├── engine.py            # ASR engine: orchestrates audio capture + module
        ├── audio.py             # Audio capture (sounddevice, thread + asyncio.Queue)
        ├── config.py            # Config loading and validation (dataclasses)
        ├── cli.py               # Entry point for the server (argparse)
        ├── client.py            # Entry point for the demo client
        └── modules/
            ├── __init__.py      # REGISTRY dict
            ├── base.py          # ASRModule ABC + ASRResult dataclass
            └── deepgram.py      # DeepgramModule implementation
```

## Entry Points (pyproject.toml)

```toml
[project.scripts]
asr-mcp-server = "asr_mcp.cli:main"
asr-mcp-client = "asr_mcp.client:main"
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
