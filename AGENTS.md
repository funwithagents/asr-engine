# Agent instructions

Start at [specs/_index.md](specs/_index.md) for an overview of the specs and their status before making design decisions or writing code — it lists each spec and whether it's still open ("Draft"/"Not started"), design-validated ("Stable"), or built ("Implemented"). For what's been (or is being) built, see [plans/_index.md](plans/_index.md), which lists each implementation plan and its status ("Todo"/"In progress"/"Done").

The spec index also opens with what this project is; the goals, constraints and system design live in [specs/overview.md](specs/overview.md) and [specs/architecture.md](specs/architecture.md). Don't restate design here — follow the spec links.

## Project map

Where things live. This is a coarse, module-level map — for the full file inventory use `git ls-files`; for design detail follow the spec links.

### Top-level layout

| Path | What's there |
|---|---|
| `src/asr_engine/` | The library itself — one module per core concept (see below); pluggable backends in `modules/`, bundled cues in `sounds/` |
| `specs/` | Pre-implementation design docs, one per concept, each with a `**Status:**` — indexed by [specs/_index.md](specs/_index.md) |
| `plans/` | Implementation plans turning settled specs into buildable steps — indexed by [plans/_index.md](plans/_index.md) |
| `tests/` | Fast, deterministic, no-network tests; mirrors the `src/asr_engine/` module structure (`tests/examples/` covers `examples/`) |
| `tests-e2e/` | Opt-in real-time pipeline tests: per-module conformance against live provider APIs, everything else keyless on the scripted `fake` module (not collected by default `pytest`) |
| `examples/` | Runnable consumers of the library, not part of the wheel — `gradio_demo/`, `mcp_client/`, `asr_to_terminal/`, each run via `python -m examples.<pkg>.<module>`; see [examples/README.md](examples/README.md) and [specs/project.md](specs/project.md) "Repo shape" |
| `scripts/` | Standalone debug/utility scripts (not part of the package) |

### `src/asr_engine/` modules

| Module | Role | Spec |
|---|---|---|
| [mcp_server_cli.py](src/asr_engine/mcp_server_cli.py) | MCP server entry point (argparse → wires everything) | [mcp-server.md](specs/mcp-server.md) |
| [config.py](src/asr_engine/config.py) | Config dataclasses + load/validate | [configuration.md](specs/configuration.md) |
| [audio.py](src/asr_engine/audio.py) | `AudioCapture`, `AudioSource` protocol (public injection seam), `FileAudioSource` | [architecture.md](specs/architecture.md), [audio-source.md](specs/audio-source.md) |
| [engine.py](src/asr_engine/engine.py) | `ASREngine`: built from `ASREngineConfig`; wires audio + module, start/stop, segmentation, sound feedback, `listen` | [engine.md](specs/engine.md) |
| [tools.py](src/asr_engine/tools.py) | `AsrTools`: transport-agnostic lifecycle, `listen`, and dictation tools over an `ASREngine` | [tools.md](specs/tools.md) |
| [server.py](src/asr_engine/server.py) | MCP server: resources + StreamableHTTP, thin MCP adapter over `AsrTools` | [mcp-server.md](specs/mcp-server.md) |
| [speech_utils.py](src/asr_engine/speech_utils.py) | `contains_trigger_word()` — shared trigger-word detection | [engine.md](specs/engine.md) |
| [segmenter.py](src/asr_engine/segmenter.py) | `Segmenter` + `SpeechSegment`: utterance→segment aggregation (utterance/trigger_word/timeout) | [engine.md](specs/engine.md) |
| [sound_feedback.py](src/asr_engine/sound_feedback.py) | `SoundFeedback` + `NoOpSoundFeedback`: WAV cue playback | [sound-feedback.md](specs/sound-feedback.md) |
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
- Prefer driving the public API the way a real caller would over asserting on internals.
- The full strategy (two-tier split, scenario-not-field rules, the `fake` module as the downstream test double, speed rule) is specced in [specs/testing.md](specs/testing.md).

### Live/e2e tests

`tests-e2e/` holds the slow, real-time pipeline tests (subprocess MCP servers, real-time file audio), separate from `tests/` so the default `uv run pytest` never collects them. Only the per-module conformance cases (`test_engine_modules.py`) call live provider APIs; everything else runs keyless on the scripted `fake` module. What is parametrized per module, the `MODULES` table, `default_module()`, and credentials-by-name (`api_key_env`) are specced in [specs/e2e-testing.md](specs/e2e-testing.md).

A live case whose `api_key_env` variable is unset **skips**, not fails, so the tier is safe to run with only the keys you have. **The keys live in `~/.zshrc`**, which the shell tool's non-interactive shell doesn't source — a plain `uv run pytest tests-e2e` sees no keys and every live case skips (the `fake`-module scenarios still run). Source it via an interactive `zsh`:

```bash
zsh -ic 'uv run pytest tests-e2e'                  # all: live cases run where a key is set, else skip
zsh -ic 'uv run pytest tests-e2e -k deepgram_v1'   # one backend, by its parametrize id
```

Never `echo`/print a key itself; when checking whether one is set, redact the value (e.g. `env | grep DEEPGRAM | sed -E 's/=.*/=<set>/'`).

## Implementation plans

- Write implementation plans as files in the [plans](plans/) folder.
- Name each file `YYYYMMDDHHmm_plan-title.md`: a compact date-time prefix, then an underscore, then a kebab-case title. Example: `202603201617_sound-feedback.md`. Plans sort chronologically by this prefix.
- Give each plan a `**Status:**` line just under its title (`Todo`/`In progress`/`Done`) and add a row for it to [plans/_index.md](plans/_index.md). Keep both current as work progresses (see "Keeping statuses current" above).
- Start from [plans/_plan-template.md](plans/_plan-template.md).

## Verification

After any code change, run linting, type checking, and tests, and fix any failures before considering the work done. The fast tier plus lint + type-check is the gate; `tests-e2e/` is opt-in and only when verifying against the live service.

## Commands

```bash
uv sync --dev                # full contributor environment (dev depends on asr-engine[all]: every provider extra and the mcp extra)
uv run ruff check .
uv run ruff format .
uv run pyright
uv run pytest tests/
uv run pytest tests-e2e/     # opt-in live tier — see Testing for the key-sourcing invocation
```
