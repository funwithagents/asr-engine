---
code:
  - pyproject.toml
  - src/asr_engine/_logging.py
  - src/asr_engine/mcp_server_cli.py
  - scripts/debug_server.py
  - scripts/test_deepgram_live.py
  - scripts/test_e2e_client.py
  - scripts/test_engine_live.py
tests:
  - tests/test_project_map.py
---

# Project

**Status:** Implemented

> **Refactor note (2026-08-31):** the importable package and distribution are
> renamed `asr_mcp` → `asr_engine` (repository renamed `asr-engine`), a new
> transport-agnostic `tools.py` module is added, entry-point commands are
> renamed, and the logging convention gains a config-driven engine level.
> Implemented by
> [plans/202608311612_asr-engine-refactor.md](../plans/202608311612_asr-engine-refactor.md).
>
> **Server-only-package update (2026-09-01):** the `asr_engine` package is
> narrowed to server-side concerns only (engine, tools, MCP server, config,
> audio, modules, segmentation, sound feedback, logging). All client-side code —
> the MCP subscription SDK (`resource_subscriber` / `resource_client`), the demo
> CLI (`asr_resource_client`), and the `asr-to-terminal` bridge
> (`terminal_typer` / `asr_to_terminal`) — moves out to `examples/`, and the
> `asr-mcp-client` / `asr-to-terminal` console scripts are dropped (those apps
> now run via `python -m examples.…`). To be implemented by
> [plans/202609012100_server-only-package.md](../plans/202609012100_server-only-package.md),
> which also flips this spec's `code:` frontmatter (dropping the two moved
> scripts) back to matching the code.

## Purpose

Structure and tooling for the ASR engine project itself: Python version, dependency/packaging management with `uv`, repo layout conventions, and development tooling.

## Decided

- **Python version:** 3.11+ minimum.
- **Package layout:** `src/` layout — `src/asr_engine/...` — not flat, to avoid accidentally importing an uninstalled package from the repo root. One module per core concept; the pluggable ASR backends live in the `src/asr_engine/modules/` subpackage, and bundled audio cues in `src/asr_engine/sounds/`.
- **Dependency/venv management:** `uv`. Dev tooling lives in the `dev` dependency group (`uv sync --dev`), not in runtime `dependencies`. Runtime deps: `mcp[cli]`, `sounddevice`, `numpy`, `deepgram-sdk`, `soundfile` (libsndfile — decodes file audio sources, incl. MP3, without ffmpeg).
- **Build backend:** `uv_build`, packaging the bundled WAV cues via `[tool.uv_build] include = ["src/asr_engine/sounds/*.wav"]`.
- **Linting/formatting:** `ruff`. Config in `[tool.ruff]`, `target-version` pinned to the minimum Python.
- **Testing:** `pytest` (with `pytest-asyncio`, `pytest-mock`, `pytest-timeout`), in two physically-separated tiers — a fast, deterministic, no-network default run (`tests/`) and an opt-in live tier (`tests-e2e/`) that calls the real Deepgram API. Full strategy is specced in [testing.md](testing.md).
- **Type checking:** `pyright` (`standard` mode), a dev dependency run via `uv run pyright`. Config lives in `[tool.pyright]` in `pyproject.toml`, targeting `src`, `tests`, and `tests-e2e`, pinned to the `.venv`.
- **Entry points** (`[project.scripts]`): `asr-engine-mcp` → `asr_engine.mcp_server_cli:main` — the **only** console script. The client-side apps live in `examples/` (outside the wheel) and so cannot be `[project.scripts]` entries; they run as `python -m examples.mcp_client.asr_resource_client` and `python -m examples.asr_to_terminal.asr_to_terminal` (same pattern as the Gradio demo).
- **Logging:** library modules only acquire a logger (`log = logging.getLogger(__name__)`) and never configure handlers, levels, or call `basicConfig` — the library configures nothing. `asr_engine/__init__.py` attaches a `NullHandler` to the `asr_engine` logger so a bare `import asr_engine` that configures nothing drops records silently instead of hitting the last-resort handler. All configuration is the **application layer's** job: entry points and scripts call `setup_logging()` from `asr_engine._logging` (which owns `basicConfig`) at startup. The `asr-engine-mcp` server takes a `--log-level` CLI flag (default `INFO`) as the sole level control — there is no config-file logging block. See [AGENTS.md](../AGENTS.md) "Logging conventions".
- **Repo shape:**
  - `src/asr_engine/` — the package, one module per core concept. **Server-side only** — engine, tools, MCP server, config, audio, modules, segmentation, sound feedback, logging. No client-side code lives here.
  - `examples/` — runnable consumers of the library that are not part of it and not built into the wheel: `gradio_demo/` (direct-import UI), `mcp_client/` (the MCP subscription SDK `resource_subscriber` / `resource_client` plus the `asr_resource_client` demo CLI), and `asr_to_terminal/` (`terminal_typer` + the `asr_to_terminal` bridge). Examples may import each other (e.g. `asr_to_terminal` uses `mcp_client`), mirroring how `tests-e2e/` helpers are shared.
  - `specs/` — pre-implementation design docs, one per concept (this folder).
  - `plans/` — implementation plans turning settled specs into buildable steps.
  - `tests/` at repo root — the fast, deterministic tier. Top-level files mirror the `src/asr_engine/` module structure; `tests/examples/` holds the fast tests that cover `examples/` code.
  - `tests-e2e/` at repo root, for the live tier — not collected by the default `pytest` run; also covers the `examples/` apps end-to-end.
  - Both test tiers import `examples.*` as a namespace package from the repo root (`pythonpath = ["."]` in `[tool.pytest.ini_options]`).

## Open questions

None currently.
