# ASR engine refactor: rename to `asr_engine`, self-configured engine + tools layer

**Status:** Done

Implements the settled design in the specs updated on 2026-08-31
([overview](../specs/overview.md), [architecture](../specs/architecture.md),
[engine](../specs/engine.md), [configuration](../specs/configuration.md),
[tools](../specs/tools.md), [mcp-server](../specs/mcp-server.md),
[sound-feedback](../specs/sound-feedback.md), [project](../specs/project.md)).

Turns the repo from "an ASR MCP server" into "a reusable ASR **engine** with an
MCP server on top": renames the package `asr_mcp` → `asr_engine`, makes
`ASREngine` self-configured from a single `ASREngineConfig`, moves sound feedback
and the logging level into the engine, extracts a transport-agnostic `AsrTools`
layer, restructures config (`server` + nested `engine`; `asr` → `module`;
`listen` block deleted; `audio.output_device` removed), and slims the MCP server.
The repo **directory** stays `asr-mcp`; only the importable package and dist name
change.

## Scope

Grouped by phase. Every `src/asr_mcp/*` path becomes `src/asr_engine/*`.

**Rename (mechanical):**
- `src/asr_mcp/` → `src/asr_engine/` (all modules + `modules/`, `sounds/`) — `git mv`, then update every `import asr_mcp` / `from asr_mcp` across `src/`, `tests/`, `tests-e2e/`, `scripts/`.
- `pyproject.toml` — `name = "asr-engine"`; scripts `asr-engine-mcp`/`asr-mcp-client`/`asr-to-terminal` → `asr_engine.*`; `[tool.uv_build] include` → `src/asr_engine/sounds/*.wav`.
- `tests/test_project_map.py` — `_PKG`, `_MODULE_LINK`, and the `startswith("src/asr_mcp/")` check → `asr_engine`.
- `AGENTS.md` — project map paths `src/asr_mcp/*` → `src/asr_engine/*`; add a `tools.py` row; adjust prose (entry-point commands, package name).
- All spec frontmatter `code:`/`tests:` `src/asr_mcp/*` → `src/asr_engine/*`.
- `README.md`, `config.example.json`, `tests-e2e/e2e.config.json`, `scripts/*` — new package name, new command, new config schema.

**Config restructure — `src/asr_engine/config.py` + `tests/test_config.py`:**
- New dataclasses `SegmentationConfig`, `SoundFeedbackConfig`, `LoggingConfig`, `ModuleConfig` (renamed from `ASRConfig`), `ASREngineConfig`; `AppConfig{server, engine}`.
- Delete `ListenConfig`; remove `AudioConfig.output_device`.
- Rewrite `load_config` parsing + validation for the nested schema.

**Engine — `src/asr_engine/engine.py` + `tests/test_engine.py`:**
- Constructor takes `ASREngineConfig`; applies logging level, builds module/segmenter/sound-feedback; stores `listen_default_segmentation_mode`.
- `set_segmentation_mode(mode)` (mode only); new `set_segmentation_params(...)`.
- `listen(mode=None, *, on_update=None)` — default mode from config, params untouched, plays cues.

**Tools layer — `src/asr_engine/tools.py` (new) + `tests/test_tools.py` (new):**
- `AsrTools(engine)` with `start`/`stop`/`is_running`/`listen(mode, on_progress)` and the in-progress lock.

**Server — `src/asr_engine/server.py` + `tests/test_server.py`:**
- `create_mcp_server(engine)` (engine only); build `AsrTools`, register the four MCP tools mapping `on_progress` → `ctx.report_progress`; drop sound-feedback + `set_segmentation_mode` wiring.
- `run_server(config)` builds the engine from `config.engine` (incl. `FileAudioSource` when `engine.audio.audio_file` is set) and reads `config.engine.auto_start`.

## Steps

1. **Rename the package.** `git mv src/asr_mcp src/asr_engine`. Sweep `asr_mcp` → `asr_engine` in all `.py` imports (`src/`, `tests/`, `tests-e2e/`, `scripts/`). Update `pyproject.toml` (name, scripts, `uv_build` include) and `tests/test_project_map.py` constants. `uv sync --dev` to re-install the renamed dist. Confirm imports resolve before touching behavior.

2. **Restructure config** ([configuration.md](../specs/configuration.md)). Add the new dataclasses and `ASREngineConfig`; make `AppConfig` hold `server` + `engine`. Rewrite `load_config` to parse `engine.{auto_start, segmentation_mode, listen_default_segmentation_mode, segmentation{}, sound_feedback{}, logging{}, audio{}, module{}}`, with validation for the two mode fields, `module.type` presence/known-ness, and `logging.level`. Delete `ListenConfig` and `AudioConfig.output_device`. Rewrite `tests/test_config.py` for the new schema (valid parse, defaults, each validation error).

3. **Rework `ASREngine`** ([engine.md](../specs/engine.md)). Change the constructor to `ASREngine(config: ASREngineConfig, *, on_speech_utterance=None, on_speech_segment=None, audio_source=None)`. In `__init__`: `logging.getLogger("asr_engine").setLevel(config.logging.level)`; instantiate the module from `config.module`; store segmentation params + `segmentation_mode` + `listen_default_segmentation_mode`; build the segmenter; construct `SoundFeedback`/`NoOpSoundFeedback` from `config.sound_feedback`. Split `set_segmentation_mode` into a mode-only setter plus `set_segmentation_params`. Change `listen` to `(mode=None, *, on_update=None)`: resolve `mode` from `listen_default_segmentation_mode`, `set_segmentation_mode(mode)` only, wrap start/stop with `play_start`/`play_stop`, restore prior mode + callback. Update `tests/test_engine.py`.

4. **Add the tools layer** ([tools.md](../specs/tools.md)). Create `src/asr_engine/tools.py` with `AsrTools` and `ProgressCallback`. Move the listen lock + progress-on-committed-final logic here. Add `tests/test_tools.py` driving `AsrTools` against a fake engine (start/stop/is_running dicts, listen return + mode pass-through, progress firing rules, both `ValueError` guards).

5. **Slim the MCP server** ([mcp-server.md](../specs/mcp-server.md)). `create_mcp_server(engine)`: keep the resources/subscription wiring; construct `AsrTools(engine)`; register `start`/`stop`/`is_running`/`listen` as thin adapters (the `listen` tool builds an `on_progress` that calls `ctx.report_progress`). Remove the `listen_config`/`audio_config`/`engine_config` params, the `SoundFeedback` construction, and the startup `set_segmentation_mode`. Update `run_server` to build the engine from `config.engine` and honour `config.engine.auto_start`. Update `tests/test_server.py`.

6. **Update docs, samples, scripts, and the map.** `AGENTS.md` project map (renamed paths + `tools.py` row) and prose (commands, package name, logging convention); every spec's frontmatter paths; `README.md`; `config.example.json` and `tests-e2e/e2e.config.json` to the nested schema; `scripts/*` imports/commands. Flip the eight refactored specs to **Implemented** (and `tools.md` `Stable` → `Implemented`) in each file and in [specs/_index.md](../specs/_index.md).

7. **Mark this plan `Done`** here and in [_index.md](_index.md) once verification passes.

## Verification

- `uv run ruff check .` and `uv run ruff format --check .` — clean.
- `uv run pyright` — clean (watch for the constructor/signature changes rippling through server, cli, scripts, tests).
- `uv run pytest tests/` — all green, including `tests/test_project_map.py` (map ↔ modules, and every spec frontmatter path now exists under `src/asr_engine/`, including `tools.py`/`test_tools.py`) and the rewritten `test_config`/`test_engine`/`test_server` plus new `test_tools`.
- Opt-in: `zsh -ic 'uv run pytest tests-e2e'` after updating `e2e.config.json` to the new schema (only when verifying against the live Deepgram service).
- Manual smoke: `uv run asr-engine-mcp --config config.example.json` starts and serves; `uv run asr-mcp-client` connects.
