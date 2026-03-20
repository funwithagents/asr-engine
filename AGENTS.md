# AGENTS.md — ASR MCP Project

## What this project is

A real-time Automatic Speech Recognition (ASR) MCP server written in Python.
- Captures audio continuously from a system input device
- Streams audio to a pluggable ASR backend (first: Deepgram)
- Exposes transcription results as a live MCP resource (`asr://result`) over StreamableHTTP
- Exposes tools to start, stop, and query ASR state
- Includes a demo client that subscribes to the resource and logs results

## Key design decisions

- **Always-on by default**: `engine.auto_start=true` (default) starts ASR at server startup. Set `auto_start=false` for on-demand use via the `start` or `listen` tool.
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
│   ├── project-structure.md
│   ├── e2e-testing.md
│   └── asr-to-terminal.md
├── plans/                       # Phased implementation plans with checkboxes
│   ├── plans.md                 # Index
│   ├── 01-project-setup.md
│   ├── 02-config.md
│   ├── 03-audio-capture.md
│   ├── 04-asr-module-interface.md
│   ├── 05-deepgram-module.md
│   ├── 06-asr-engine.md
│   ├── 07-mcp-server.md
│   ├── 08-demo-client.md
│   ├── 09-e2e-testing.md
│   └── 10-asr-to-terminal.md
├── implementation-details/      # Post-implementation notes (written after each plan is done)
│   ├── implem.md                # Index
│   ├── 01-project-setup.md
│   ├── 02-config.md
│   └── ...                      # One file per plan, added as implementation progresses
├── src/
│   └── asr_mcp/
│       ├── cli.py                    # Server entry point (argparse → wires everything)
│       ├── config.py                 # Config dataclasses + load/validate
│       ├── audio.py                  # AudioCapture, AudioSource protocol, FileAudioSource
│       ├── engine.py                 # ASREngine: wires audio + module, start/stop
│       ├── server.py                 # MCP server: resource, tools, StreamableHTTP, listen tool
│       ├── resource_subscriber.py    # ResourceSubscriber: generic MCP resource watcher
│       ├── resource_client.py        # AsrResourceClient: subscribe to asr://result
│       ├── tool_client.py            # McpToolClient: single-call MCP tool invocation
│       ├── asr_resource_client.py    # Demo CLI: subscribe to asr://result, log results
│       ├── speech_utils.py           # contains_trigger_word() — shared trigger word detection
│       ├── end_of_utterance_detector.py  # EndOfUtteranceDetector + UtteranceResult: end-of-utterance logic
│       ├── terminal_typer.py         # TerminalTyper: xdotool/ydotool keystroke injection
│       ├── asr_to_terminal.py        # AsrToTerminal state machine + asr-to-terminal CLI
│       └── modules/
│           ├── __init__.py      # REGISTRY + load_module()
│           ├── base.py          # ASRModule ABC, ASRResult dataclass
│           ├── deepgram_v1.py   # Deepgram Listen v1 (Nova models, is_final-based)
│           └── deepgram_v2.py   # Deepgram Listen v2 (Flux models, EndOfTurn-based)
├── tests/                       # Unit tests (fast, no external services)
│   ├── conftest.py              # Shared fixtures
│   ├── test_config.py
│   ├── test_audio.py
│   ├── test_engine.py
│   ├── test_server.py
│   ├── test_subscriber.py
│   ├── test_client.py
│   ├── test_cli.py
│   ├── test_asr_to_terminal.py
│   └── modules/
│       ├── test_deepgram_v1.py
│       └── test_deepgram_v2.py
└── tests-e2e/                   # End-to-end tests (hit real Deepgram API, require config.json)
    ├── fixtures/
    │   ├── sample.wav                # WAV fixture: 16kHz mono s16 PCM, content = "the sky is blue"
    │   ├── sample_submit.wav         # WAV fixture: same format, content = "the sky is blue validate"
    │   └── README.md                 # Fixture format documentation
    ├── test_asr_resource_client.py   # FileAudioSource → ASREngine → MCP server → AsrResourceClient
    ├── test_mcp_tool_client.py       # FileAudioSource → ASREngine → MCP server → McpToolClient (listen tool)
    └── test_asr_to_terminal.py       # ASREngine → MCP server → AsrToTerminal → xterm (requires xdotool + X11)
```

## Entry points

```bash
uv run asr-mcp-server --config config.json            # Start the MCP server
uv run asr-mcp-client                                 # Run the demo resource client (default: http://127.0.0.1:8000/mcp)
uv run asr-to-terminal [--server URL] [--submit-words WORD ...] [--display-server x11|wayland]
uv run pytest                                         # Run all tests (unit + e2e)
uv run pytest tests/                                  # Unit tests only (no API key needed)
uv run pytest tests-e2e/                              # E2E tests only (requires config.json with valid API key)
```

## Tests

| Suite | Location | What it covers | External deps |
|-------|----------|----------------|---------------|
| Unit tests | `tests/` | Config, audio capture, engine, MCP server, client, ASR modules, AsrToTerminal — all in-process, no network | None |
| E2E ASR tests | `tests-e2e/test_asr_resource_client.py` | Full pipeline: `FileAudioSource` → `ASREngine` → in-process uvicorn MCP server → `AsrResourceClient` → transcript assertion | Real Deepgram API (API key in `config.json`) |
| E2E listen tool tests | `tests-e2e/test_mcp_tool_client.py` | Same pipeline → `McpToolClient` `listen` tool (trigger_word and timeout modes) | Real Deepgram API (API key in `config.json`) |
| E2E terminal tests | `tests-e2e/test_asr_to_terminal.py` | Same pipeline → `AsrToTerminal` → `xdotool` → `xterm` → file assertion | Real Deepgram API + `xdotool` + `xterm` + live X11 display |

E2E ASR tests feed `tests-e2e/fixtures/sample.wav` (*"the sky is blue"*) through the pipeline. E2E listen tool tests cover trigger_word and timeout end-of-utterance modes. E2E terminal tests additionally drive keystroke injection into an xterm window and verify the typed output.

## System dependencies for e2e terminal tests

The `tests-e2e/test_asr_to_terminal.py` tests require two system packages that are **not** installed by `uv`:

```bash
# Debian / Ubuntu
sudo apt-get install xdotool xterm
```

| Package  | Purpose |
|----------|---------|
| `xdotool` | Keystroke injection on X11 (used by `TerminalTyper`) |
| `xterm`   | Minimal terminal emulator used as the injection target in e2e tests |

A live X11 display (`$DISPLAY`) is also required. On a headless server, use Xvfb:

```bash
Xvfb :99 -screen 0 1024x768x24 &
export DISPLAY=:99
```

## Unit testing strategy

Write tests that verify **observable behavior**, not implementation details.

**Rules:**

1. **Test scenarios, not fields.** When verifying a constructed/parsed object, one test asserts all relevant fields together. Don't write one test per field.

2. **Test observable behavior only.** Assert return values, raised exceptions, calls to collaborators, and changes to public state. Never assert on private attributes (`_foo`).

3. **One test per distinct code path.** Two tests that exercise the same branch with slightly different data should be one test. Keep variants only when they trigger genuinely different logic (e.g. `is_final=True` vs `is_final=False`).

4. **Delete trivial structural tests.** `isinstance(x, SomeClass)` or `x.name == "literal"` are not worth a dedicated test — they only break if you intentionally change the type or name.

5. **Error paths deserve individual tests.** `missing_key`, `empty_key`, `unknown_type` are distinct scenarios because they exercise different validation branches and may produce different error messages.

6. **Merge lifecycle tests.** start/stop, connect/disconnect sequences belong in one test that exercises the full cycle, not split across two.

**Speed rule** — the full unit test suite (`pytest tests/`) must complete in under 5 seconds. If it doesn't, treat it as a bug: find the slow tests with `pytest --durations=10` and fix the root cause (usually a real timer firing in production code that needs to be made patchable, or a missing stop signal).

**Smell checklist** — delete or merge a test if it:
- Asserts a private attribute (e.g. `assert obj._foo == ...`)
- Is fully subsumed by another test in the same file
- Checks something that cannot break independently (structural/isinstance)
- Is one of N identical tests differing only in which field they check

## Logging conventions

- Every module that logs uses `log = logging.getLogger(__name__)` (variable name: `log`, not `logger`).
- **Library modules** (`src/asr_mcp/`) never call `basicConfig` or configure handlers — they only get a logger and use it.
- **Entry points and scripts** (`scripts/`) call `setup_logging()` from `asr_mcp._logging` at startup to configure the root logger with common format.
- The MCP server (`server.py`) additionally passes a uvicorn-specific `log_config` dict to `uvicorn.Config` to control uvicorn's own loggers — this is separate from `setup_logging()` and should not be changed without good reason.

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
