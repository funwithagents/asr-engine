# Agent instructions

Start at [specs/_index.md](specs/_index.md) for an overview of the specs and their status before making design decisions or writing code — it lists each spec and whether it's still open ("Draft"/"Not started"), design-validated ("Stable"), or built ("Implemented"). For what's been (or is being) built, see [plans/_index.md](plans/_index.md), which lists each implementation plan and its status ("Todo"/"In progress"/"Done").

## What this project is

A real-time Automatic Speech Recognition (ASR) MCP server written in Python.

- Captures audio continuously from a system input device.
- Streams audio to a pluggable ASR backend (first: Deepgram).
- Exposes transcription results as live MCP resources (`asr://utterance`, `asr://segment`) over StreamableHTTP.
- Exposes tools to start, stop, query ASR state, and `listen` for a single utterance.
- Includes a demo client that subscribes to the resource and logs results, and an `asr-to-terminal` bridge that types transcripts into the focused window.

### Key design decisions

- **Always-on by default:** `engine.auto_start=true` (default) starts ASR at server startup. Set `auto_start=false` for on-demand use via the `start` or `listen` tool.
- **Rolling resources:** `asr://utterance` holds only the latest utterance (interim or final); `asr://segment` holds the latest aggregated segment. Neither is a full transcript history.
- **Engine-owned segmentation:** the `ASREngine`'s `Segmenter` aggregates utterances into segments (utterance/trigger_word/timeout modes); clients consume segments rather than re-implementing end-of-utterance logic.
- **Pluggable modules:** one ASR module active at a time, selected via `engine.module.type` in config.
- **StreamableHTTP transport:** enables remote access on a local network.
- **asyncio throughout:** audio capture runs in a thread, everything else is async on one event loop.

## Project map

Where things live. This is a coarse, module-level map — for the full file inventory use `git ls-files`; for design detail follow the spec links.

### Top-level layout

| Path | What's there |
|---|---|
| `src/asr_engine/` | The library itself — one module per core concept (see below); pluggable backends in `modules/`, bundled cues in `sounds/` |
| `specs/` | Pre-implementation design docs, one per concept, each with a `**Status:**` — indexed by [specs/_index.md](specs/_index.md) |
| `plans/` | Implementation plans turning settled specs into buildable steps — indexed by [plans/_index.md](plans/_index.md) |
| `tests/` | Fast, deterministic, no-network tests; mirrors the `src/asr_engine/` module structure |
| `tests-e2e/` | Opt-in live tests that call the real Deepgram API (not collected by the fast dev loop) |
| `scripts/` | Standalone debug/utility scripts (not part of the package) |

### `src/asr_engine/` modules

<!-- One row per concept module. Keep this in sync with the code (a test enforces it). The modules/ subpackage (base.py, deepgram_v1.py, deepgram_v2.py) and sounds/ assets are not top-level modules. -->

| Module | Role | Spec |
|---|---|---|
| [mcp_server_cli.py](src/asr_engine/mcp_server_cli.py) | MCP server entry point (argparse → wires everything) | [mcp-server.md](specs/mcp-server.md) |
| [config.py](src/asr_engine/config.py) | Config dataclasses + load/validate | [configuration.md](specs/configuration.md) |
| [audio.py](src/asr_engine/audio.py) | `AudioCapture`, `AudioSource` protocol, `FileAudioSource` | [architecture.md](specs/architecture.md) |
| [engine.py](src/asr_engine/engine.py) | `ASREngine`: built from `ASREngineConfig`; wires audio + module, start/stop, segmentation, sound feedback, logging level, `listen` | [engine.md](specs/engine.md) |
| [tools.py](src/asr_engine/tools.py) | `AsrTools`: transport-agnostic `start`/`stop`/`is_running`/`listen` over an `ASREngine` | [tools.md](specs/tools.md) |
| [server.py](src/asr_engine/server.py) | MCP server: resources + StreamableHTTP, thin MCP adapter over `AsrTools` | [mcp-server.md](specs/mcp-server.md) |
| [resource_subscriber.py](src/asr_engine/resource_subscriber.py) | `ResourceSubscriber`: generic MCP resource watcher | [demo-client.md](specs/demo-client.md) |
| [resource_client.py](src/asr_engine/resource_client.py) | `AsrResourceClient`: subscribe to `asr://utterance` / `asr://segment` | [demo-client.md](specs/demo-client.md) |
| [asr_resource_client.py](src/asr_engine/asr_resource_client.py) | Demo CLI: subscribe to `asr://utterance`, log results | [demo-client.md](specs/demo-client.md) |
| [speech_utils.py](src/asr_engine/speech_utils.py) | `contains_trigger_word()` — shared trigger-word detection | [engine.md](specs/engine.md) |
| [segmenter.py](src/asr_engine/segmenter.py) | `Segmenter` + `SpeechSegment`: utterance→segment aggregation (utterance/trigger_word/timeout) | [engine.md](specs/engine.md) |
| [sound_feedback.py](src/asr_engine/sound_feedback.py) | `SoundFeedback` + `NoOpSoundFeedback`: WAV cue playback | [sound-feedback.md](specs/sound-feedback.md) |
| [terminal_typer.py](src/asr_engine/terminal_typer.py) | `TerminalTyper`: xdotool/ydotool keystroke injection | [asr-to-terminal.md](specs/asr-to-terminal.md) |
| [asr_to_terminal.py](src/asr_engine/asr_to_terminal.py) | `AsrToTerminal` state machine + `asr-to-terminal` CLI | [asr-to-terminal.md](specs/asr-to-terminal.md) |
| [_logging.py](src/asr_engine/_logging.py) | `setup_logging()` for entry points and scripts | [project.md](specs/project.md) |
| [__init__.py](src/asr_engine/__init__.py) | Package glue (no owning spec) | — |

**Keep this map current:** when you add, rename, or remove a top-level `src/asr_engine/` module or a root directory, update the map in the same change — same discipline as keeping spec/plan statuses honest (below). A test (`tests/test_project_map.py`) enforces that every `src/asr_engine/*.py` module appears here and vice-versa — and that the spec frontmatter (see below) stays honest too.

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
  - src/asr_engine/<module>.py
tests:
  - tests/test_<module>.py
---
```

This is the **spec → code/tests** mapping — the inverse of the module → spec column in the Project map above. Its job is to give the **spec-drift checks** an explicit, version-controlled scope: the exact files to diff a spec against. `code:` names the implementation the spec specifies; `tests:` names the tests that pin its behavior (may be empty).

The mapping is **many-to-many**: a file can be governed by several specs, so the same path legitimately appears in more than one spec's frontmatter.

**Keep it current** (same discipline as statuses): when you move, rename, or delete a file a spec governs — or add a new `src/asr_engine/` module — update the affected spec's `code:`/`tests:` in the same change. `tests/test_project_map.py` enforces three invariants: every listed path exists, every spec declares a non-empty `code:` list, and every concept module in `src/asr_engine/` is named by at least one spec (`__init__.py` is exempt as package glue).

## Testing

- Write functional tests: exercise what a feature/function actually does (inputs → outputs, state changes, side effects), not just that it runs or matches its signature.
- Avoid trivial/tautological tests — e.g. asserting a constant, asserting an object is not `None`, asserting a mock was called. If a test would pass for a broken implementation, it's not worth writing.
- Prefer driving the public API the way a real caller would over asserting on internals. The full strategy (two-tier split, scenario-not-field rules, speed rule) is specced in [specs/testing.md](specs/testing.md).

### Live/e2e tests

Some tests call the real Deepgram API over the network. They live in `tests-e2e/`, a directory separate from `tests/`, so the fast dev loop (`uv run pytest tests/`) never needs network access or credentials. Run them explicitly (`uv run pytest tests-e2e`), and only when you actually want to verify against the live service. The file-based e2e pipeline design is specced in [specs/e2e-testing.md](specs/e2e-testing.md).

**Per-module vs single-provider.** The e2e suite splits along what varies per ASR module. `test_engine_modules.py` is **parametrized over every module** (the `MODULES` table in `helpers.py`) and verifies only the `ASRModule` contract through the engine — interim/final utterances, interim/final segments, `listen` in both modes. Everything downstream (`test_mcp_resource.py`, `test_mcp_tools.py`, `test_asr_to_terminal.py`) is module-agnostic and runs on a **single provider** chosen once in `helpers.default_provider()`. Tests are named for the behavior under test, not the module. To run just one backend, filter on its param id: `zsh -ic 'uv run pytest tests-e2e -k deepgram_v1'` (runs only that module's conformance tests, since the single-provider tests aren't module-named).

**Credentials.** Tests never read the literal key. Each module config carries `api_key_env` — the *name* of the env var it authenticates with (Deepgram's is `DEEPGRAM_API_KEY`, the `DEEPGRAM_API_KEY_ENV` constant in `helpers.py`; other modules name their own, and a module needing no key names none) — and the module's own `resolve_api_key` reads it, so no secret lives in the repo. `helpers.require_api_key(module_config)` **skips** a test when the env var that config names is unset (without it, `resolve_api_key` would raise and fail the test rather than skip it); a config without `api_key_env` is never skipped.

**The keys live in `~/.zshrc`**, but the shell tool runs a non-interactive `bash`/`zsh` that doesn't source it — a plain `uv run pytest tests-e2e` in that shell sees no keys and every case skips. Source it explicitly in an interactive `zsh` invocation:

```bash
zsh -ic 'uv run pytest tests-e2e'
```

**e2e terminal tests are self-contained:** `tests-e2e/test_asr_to_terminal.py` injects an in-memory `RecordingTyper` (a `KeystrokeSink`) into `AsrToTerminal`, so it drives the full live pipeline without `xterm`, `xdotool`, or an X11 display — it runs anywhere with a Deepgram API key. Only the runtime `asr-to-terminal` CLI needs `xdotool` (X11), or `ydotool` plus a running `ydotoold` with access to `/dev/uinput` (Wayland).

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
uv run pytest tests-e2e/     # opt-in live tier (needs the provider API key; configs are generated by the tests)
```

### Entry points

```bash
uv run asr-engine-mcp --config config.json   # Start the MCP server
uv run asr-mcp-client                        # Demo resource client (default: http://127.0.0.1:8000/mcp)
uv run asr-to-terminal [--server URL] [--display-server x11|wayland]
```

## Conventions

### Audio format contract

All ASR modules receive audio as **16 kHz, 16-bit signed PCM, mono, ~100 ms chunks (3200 bytes)**. The audio capture layer owns resampling; modules must not worry about format conversion.

### Logging

- Every module that logs uses `log = logging.getLogger(__name__)` (variable name `log`, not `logger`).
- **Library modules** (`src/asr_engine/`) configure nothing — no `basicConfig`, no handlers, no levels. They only acquire a logger and use it. This includes `ASREngine`, which never touches global logging state. `asr_engine/__init__.py` attaches a `NullHandler` to the `asr_engine` logger so a bare `import asr_engine` that configures nothing drops records silently instead of hitting the last-resort handler.
- **Entry points and scripts** (the application layer) own all configuration: they call `setup_logging()` from `asr_engine._logging` (which owns `basicConfig`) at startup. The `asr-engine-mcp` server takes a `--log-level` CLI flag (default `INFO`) as the sole level control — there is no config-file logging block.
- The MCP server (`server.py`) additionally passes a uvicorn-specific `log_config` dict to `uvicorn.Config` to control uvicorn's own loggers — separate from `setup_logging()`, and given the resolved `--log-level`; don't change without good reason.

### Adding a new ASR module

1. Create `src/asr_engine/modules/<name>.py` implementing `ASRModule` from `modules/base.py`.
2. Register it in `modules/__init__.py`: `REGISTRY["<name>"] = <ClassName>`.
3. Document its config fields (the `engine.module` block accepts any fields beyond `type`).
4. Update [specs/deepgram-module.md](specs/deepgram-module.md) or add a new spec, and its frontmatter `code:` list.
5. Add a row to the `MODULES` table in `tests-e2e/helpers.py` (module type, model, and a `silence_s` matching how long the backend needs to finalize an utterance) so the new module gets e2e conformance coverage in `test_engine_modules.py`. Verify with `zsh -ic 'uv run pytest tests-e2e -k <name>'`.
