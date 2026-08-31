# Agent instructions

Start at [specs/_index.md](specs/_index.md) for an overview of the specs and their status before making design decisions or writing code — it lists each spec and whether it's still open ("Draft"/"Not started"), design-validated ("Stable"), or built ("Implemented"). For what's been (or is being) built, see [plans/_index.md](plans/_index.md), which lists each implementation plan and its status ("Todo"/"In progress"/"Done").

## What this project is

A real-time Automatic Speech Recognition (ASR) MCP server written in Python.

- Captures audio continuously from a system input device.
- Streams audio to a pluggable ASR backend (first: Deepgram).
- Exposes transcription results as a live MCP resource (`asr://result`) over StreamableHTTP.
- Exposes tools to start, stop, query ASR state, and `listen` for a single utterance.
- Includes a demo client that subscribes to the resource and logs results, and an `asr-to-terminal` bridge that types transcripts into the focused window.

### Key design decisions

- **Always-on by default:** `engine.auto_start=true` (default) starts ASR at server startup. Set `auto_start=false` for on-demand use via the `start` or `listen` tool.
- **Single rolling resource:** `asr://result` holds only the latest utterance (interim or final), not a full transcript history.
- **Pluggable modules:** one ASR module active at a time, selected via `asr.type` in config.
- **StreamableHTTP transport:** enables remote access on a local network.
- **asyncio throughout:** audio capture runs in a thread, everything else is async on one event loop.

## Project map

Where things live. This is a coarse, module-level map — for the full file inventory use `git ls-files`; for design detail follow the spec links.

### Top-level layout

| Path | What's there |
|---|---|
| `src/asr_mcp/` | The library itself — one module per core concept (see below); pluggable backends in `modules/`, bundled cues in `sounds/` |
| `specs/` | Pre-implementation design docs, one per concept, each with a `**Status:**` — indexed by [specs/_index.md](specs/_index.md) |
| `plans/` | Implementation plans turning settled specs into buildable steps — indexed by [plans/_index.md](plans/_index.md) |
| `tests/` | Fast, deterministic, no-network tests; mirrors the `src/asr_mcp/` module structure |
| `tests-e2e/` | Opt-in live tests that call the real Deepgram API (not collected by the fast dev loop) |
| `scripts/` | Standalone debug/utility scripts (not part of the package) |

### `src/asr_mcp/` modules

<!-- One row per concept module. Keep this in sync with the code (a test enforces it). The modules/ subpackage (base.py, deepgram_v1.py, deepgram_v2.py) and sounds/ assets are not top-level modules. -->

| Module | Role | Spec |
|---|---|---|
| [cli.py](src/asr_mcp/cli.py) | Server entry point (argparse → wires everything) | [mcp-server.md](specs/mcp-server.md) |
| [config.py](src/asr_mcp/config.py) | Config dataclasses + load/validate | [configuration.md](specs/configuration.md) |
| [audio.py](src/asr_mcp/audio.py) | `AudioCapture`, `AudioSource` protocol, `FileAudioSource` | [architecture.md](specs/architecture.md) |
| [engine.py](src/asr_mcp/engine.py) | `ASREngine`: wires audio + module, start/stop | [architecture.md](specs/architecture.md) |
| [server.py](src/asr_mcp/server.py) | MCP server: resource, tools, StreamableHTTP, `listen` tool | [mcp-server.md](specs/mcp-server.md) |
| [resource_subscriber.py](src/asr_mcp/resource_subscriber.py) | `ResourceSubscriber`: generic MCP resource watcher | [demo-client.md](specs/demo-client.md) |
| [resource_client.py](src/asr_mcp/resource_client.py) | `AsrResourceClient`: subscribe to `asr://result` | [demo-client.md](specs/demo-client.md) |
| [tool_client.py](src/asr_mcp/tool_client.py) | `McpToolClient`: single-call MCP tool invocation | [demo-client.md](specs/demo-client.md) |
| [asr_resource_client.py](src/asr_mcp/asr_resource_client.py) | Demo CLI: subscribe to `asr://result`, log results | [demo-client.md](specs/demo-client.md) |
| [speech_utils.py](src/asr_mcp/speech_utils.py) | `contains_trigger_word()` — shared trigger-word detection | [end-of-utterance-detector.md](specs/end-of-utterance-detector.md) |
| [end_of_utterance_detector.py](src/asr_mcp/end_of_utterance_detector.py) | `EndOfUtteranceDetector` + `UtteranceResult`: end-of-utterance logic | [end-of-utterance-detector.md](specs/end-of-utterance-detector.md) |
| [sound_feedback.py](src/asr_mcp/sound_feedback.py) | `SoundFeedback` + `NoOpSoundFeedback`: WAV cue playback | [sound-feedback.md](specs/sound-feedback.md) |
| [terminal_typer.py](src/asr_mcp/terminal_typer.py) | `TerminalTyper`: xdotool/ydotool keystroke injection | [asr-to-terminal.md](specs/asr-to-terminal.md) |
| [asr_to_terminal.py](src/asr_mcp/asr_to_terminal.py) | `AsrToTerminal` state machine + `asr-to-terminal` CLI | [asr-to-terminal.md](specs/asr-to-terminal.md) |
| [_logging.py](src/asr_mcp/_logging.py) | `setup_logging()` for entry points and scripts | [project.md](specs/project.md) |
| [__init__.py](src/asr_mcp/__init__.py) | Package glue (no owning spec) | — |

**Keep this map current:** when you add, rename, or remove a top-level `src/asr_mcp/` module or a root directory, update the map in the same change — same discipline as keeping spec/plan statuses honest (below). A test (`tests/test_project_map.py`) enforces that every `src/asr_mcp/*.py` module appears here and vice-versa — and that the spec frontmatter (see below) stays honest too.

## Keeping statuses current

Specs and plans both carry a status, and you are responsible for keeping it honest as work progresses — update it in the same change that does the work, not as an afterthought:

- **Spec status** (`**Status:**` line near the top of each spec, and the Status column in [specs/_index.md](specs/_index.md)) tracks *design maturity* and *whether the code reflects the spec*, as a lifecycle: `Not started` → `Draft` (open questions remain) → `Stable` (design settled, reviewed and validated — open questions are deferrals only — but **not necessarily implemented yet**) → `Implemented` (a `Done` plan has built it and the code matches the spec).
  - **`Stable` is the design-review gate, not an implementation claim.** Promote `Draft` → `Stable` once the core design is settled and its remaining open questions are genuine deferrals — this is where the design is validated *before* code is written.
  - **`Implemented` means code matches.** Promote `Stable` → `Implemented` only once a plan implementing it is `Done` (lint, type check, tests all pass — see Verification).
  - **When you edit an `Implemented` spec in a way that requires new code, set its status to `Updated` in the same change**, write a new plan for the gap, and flip it back to `Implemented` once that plan is `Done` — the `Implemented → Updated → Implemented` loop. A purely editorial edit keeps the status.
- **Plan status** (`**Status:**` line near the top of each plan, and the Status column in [plans/_index.md](plans/_index.md)) tracks *implementation progress*: `Todo` → `In progress` → `Done`. Mark a plan `Done` only once it's implemented and verified (lint, type check, tests all pass — see Verification).
- Whenever you add a spec or plan, add its row to the relevant `_index.md`; whenever you change a status, change it in both the file and the index.

## Spec frontmatter

Every spec opens with a YAML frontmatter block naming the code and tests it governs:

```
---
code:
  - src/asr_mcp/<module>.py
tests:
  - tests/test_<module>.py
---
```

This is the **spec → code/tests** mapping — the inverse of the module → spec column in the Project map above. Its job is to give the **spec-drift checks** an explicit, version-controlled scope: the exact files to diff a spec against. `code:` names the implementation the spec specifies; `tests:` names the tests that pin its behavior (may be empty).

The mapping is **many-to-many**: a file can be governed by several specs, so the same path legitimately appears in more than one spec's frontmatter.

**Keep it current** (same discipline as statuses): when you move, rename, or delete a file a spec governs — or add a new `src/asr_mcp/` module — update the affected spec's `code:`/`tests:` in the same change. `tests/test_project_map.py` enforces three invariants: every listed path exists, every spec declares a non-empty `code:` list, and every concept module in `src/asr_mcp/` is named by at least one spec (`__init__.py` is exempt as package glue).

## Testing

- Write functional tests: exercise what a feature/function actually does (inputs → outputs, state changes, side effects), not just that it runs or matches its signature.
- Avoid trivial/tautological tests — e.g. asserting a constant, asserting an object is not `None`, asserting a mock was called. If a test would pass for a broken implementation, it's not worth writing.
- Prefer driving the public API the way a real caller would over asserting on internals. The full strategy (two-tier split, scenario-not-field rules, speed rule) is specced in [specs/testing.md](specs/testing.md).

### Live/e2e tests

Some tests call the real Deepgram API over the network. They live in `tests-e2e/`, a directory separate from `tests/`, so the fast dev loop (`uv run pytest tests/`) never needs network access or credentials. Run them explicitly (`uv run pytest tests-e2e`), and only when you actually want to verify against the live service. Credentials come from `config.json` (never committed) via `tests-e2e/helpers.load_api_key()`. The file-based e2e pipeline design is specced in [specs/e2e-testing.md](specs/e2e-testing.md).

**System dependencies for e2e terminal tests:** `tests-e2e/test_asr_to_terminal.py` needs two system packages **not** installed by `uv` — `xdotool` (keystroke injection on X11) and `xterm` (the injection target) — plus a live X11 display (`$DISPLAY`). On Debian/Ubuntu: `sudo apt-get install xdotool xterm`. On a headless server, use Xvfb (`Xvfb :99 -screen 0 1024x768x24 &` then `export DISPLAY=:99`).

## Implementation plans

- Write implementation plans as files in the [plans](plans/) folder.
- Name each file `YYYYMMDDHHmm_plan-title.md`: a compact date-time prefix, then an underscore, then a kebab-case title. Example: `202603201617_sound-feedback.md`. Plans sort chronologically by this prefix.
- Give each plan a `**Status:**` line just under its title (`Todo`/`In progress`/`Done`) and add a row for it to [plans/_index.md](plans/_index.md). Keep both current as work progresses (see "Keeping statuses current" above).
- Start from [plans/_plan-template.md](plans/_plan-template.md).

## Verification

After any code change, run linting, type checking, and tests, and fix any failures before considering the work done. The fast tier plus lint + type-check is the gate; `tests-e2e/` is opt-in and only when verifying against the live service.

## Commands

```bash
uv sync --dev
uv run ruff check .          # lint
uv run ruff format .         # format
uv run pyright               # type-check (src, tests, tests-e2e)
uv run pytest tests/         # fast, deterministic tier (no credentials)
uv run pytest tests-e2e/     # opt-in live tier (needs config.json + API key)
```

### Entry points

```bash
uv run asr-mcp-server --config config.json   # Start the MCP server
uv run asr-mcp-client                        # Demo resource client (default: http://127.0.0.1:8000/mcp)
uv run asr-to-terminal [--server URL] [--submit-words WORD ...] [--display-server x11|wayland]
```

## Conventions

### Audio format contract

All ASR modules receive audio as **16 kHz, 16-bit signed PCM, mono, ~100 ms chunks (3200 bytes)**. The audio capture layer owns resampling; modules must not worry about format conversion.

### Logging

- Every module that logs uses `log = logging.getLogger(__name__)` (variable name `log`, not `logger`).
- **Library modules** (`src/asr_mcp/`) never call `basicConfig` or configure handlers — they only get a logger and use it.
- **Entry points and scripts** call `setup_logging()` from `asr_mcp._logging` at startup to configure the root logger.
- The MCP server (`server.py`) additionally passes a uvicorn-specific `log_config` dict to `uvicorn.Config` to control uvicorn's own loggers — separate from `setup_logging()`, don't change without good reason.

### Adding a new ASR module

1. Create `src/asr_mcp/modules/<name>.py` implementing `ASRModule` from `modules/base.py`.
2. Register it in `modules/__init__.py`: `REGISTRY["<name>"] = <ClassName>`.
3. Document its config fields (the `asr` block accepts any fields beyond `type`).
4. Update [specs/deepgram-module.md](specs/deepgram-module.md) or add a new spec, and its frontmatter `code:` list.
