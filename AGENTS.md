# AGENTS.md — ASR MCP Project

## What this project is

A real-time Automatic Speech Recognition (ASR) MCP server written in Python.
- Captures audio continuously from a system input device
- Streams audio to a pluggable ASR backend (first: Deepgram)
- Exposes transcription results as a live MCP resource (`asr://result`) over StreamableHTTP
- Exposes tools to pause, resume, and query ASR state
- Includes a demo client that subscribes to the resource and logs results

## Key design decisions

- **Always-on**: ASR runs from server startup, not triggered by client connections
- **Single rolling resource**: `asr://result` holds only the latest utterance (interim or final), not a full transcript history
- **Pluggable modules**: one ASR module active at a time, selected via `asr.type` in config
- **StreamableHTTP transport**: enables remote access on a local network
- **asyncio throughout**: audio capture runs in a thread, everything else is async on one event loop

## Repository layout

```
asr-mcp/
├── AGENTS.md                    # This file
├── pyproject.toml               # uv project: deps, entry points, pytest config
├── config.example.json          # Config template (no secrets)
├── specs/                       # Full project specifications
│   ├── specs.md                 # Index — start here
│   ├── overview.md
│   ├── architecture.md
│   ├── configuration.md
│   ├── mcp-server.md
│   ├── asr-module-interface.md
│   ├── deepgram-module.md
│   ├── demo-client.md
│   └── project-structure.md
├── plans/                       # Phased implementation plans with checkboxes
│   ├── plans.md                 # Index
│   ├── 01-project-setup.md
│   ├── 02-config.md
│   ├── 03-audio-capture.md
│   ├── 04-asr-module-interface.md
│   ├── 05-deepgram-module.md
│   ├── 06-asr-engine.md
│   ├── 07-mcp-server.md
│   └── 08-demo-client.md
├── implementation-details/      # Post-implementation notes (written after each plan is done)
│   ├── implem.md                # Index
│   ├── 01-project-setup.md
│   ├── 02-config.md
│   └── ...                      # One file per plan, added as implementation progresses
├── src/
│   └── asr_mcp/
│       ├── cli.py               # Server entry point (argparse → wires everything)
│       ├── client.py            # Demo client entry point
│       ├── config.py            # Config dataclasses + load/validate
│       ├── audio.py             # AudioCapture: sounddevice thread → asyncio.Queue
│       ├── engine.py            # ASREngine: wires audio + module, pause/resume
│       ├── server.py            # MCP server: resource, tools, StreamableHTTP
│       └── modules/
│           ├── __init__.py      # REGISTRY + load_module()
│           ├── base.py          # ASRModule ABC, ASRResult dataclass
│           └── deepgram.py      # DeepgramModule implementation
└── tests/
    ├── conftest.py              # Shared fixtures
    ├── test_config.py
    ├── test_audio.py
    ├── test_engine.py
    ├── test_server.py
    └── modules/
        └── test_deepgram.py
```

## Entry points

```bash
uv run asr-mcp-server --config config.json   # Start the MCP server
uv run asr-mcp-client --server http://host:port/mcp  # Run the demo client
uv run pytest                                # Run the test suite
```

## Audio format contract

All ASR modules receive audio as: **16kHz, 16-bit signed PCM, mono, ~100ms chunks (3200 bytes)**.
The audio capture layer owns resampling; modules must not worry about format conversion.

## Adding a new ASR module

1. Create `src/asr_mcp/modules/<name>.py` implementing `ASRModule` from `modules/base.py`
2. Register it in `modules/__init__.py`: `REGISTRY["<name>"] = <ClassName>`
3. Document its config fields (the `asr` block accepts any fields beyond `type`)

## Documentation workflow

This project follows a three-layer documentation convention:

1. **`specs/`** — Written before implementation. Describes *what* to build and *why*.
2. **`plans/`** — Written before implementation. Describes *how* to build it, step by step with checkboxes.
3. **`implementation-details/`** — Written *after* each plan is completed. One file per plan, covering deviations from spec, non-obvious decisions, SDK quirks, and known limitations. Index at [`implementation-details/implem.md`](implementation-details/implem.md).

When implementing a plan: tick off tasks in `plans/`, then write the corresponding file in `implementation-details/` and mark it as written in `implem.md`.

## Where to look first

- Understand the system: [`specs/specs.md`](specs/specs.md)
- Check implementation status: [`plans/plans.md`](plans/plans.md)
- Understand what was actually built: [`implementation-details/implem.md`](implementation-details/implem.md)
- Understand data flow: [`specs/architecture.md`](specs/architecture.md)
- Understand the module contract: [`specs/asr-module-interface.md`](specs/asr-module-interface.md)
