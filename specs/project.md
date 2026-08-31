---
code:
  - pyproject.toml
  - src/asr_engine/_logging.py
tests:
  - tests/test_project_map.py
---

# Project

**Status:** Implemented

> **Refactor note (2026-08-31):** the importable package and distribution are
> renamed `asr_mcp` → `asr_engine` (repo directory kept as `asr-mcp`), a new
> transport-agnostic `tools.py` module is added, entry-point commands are
> renamed, and the logging convention gains a config-driven engine level.
> Implemented by
> [plans/202608311612_asr-engine-refactor.md](../plans/202608311612_asr-engine-refactor.md).

## Purpose

Structure and tooling for the ASR engine project itself: Python version, dependency/packaging management with `uv`, repo layout conventions, and development tooling.

## Decided

- **Python version:** 3.11+ minimum.
- **Package layout:** `src/` layout — `src/asr_engine/...` — not flat, to avoid accidentally importing an uninstalled package from the repo root. One module per core concept; the pluggable ASR backends live in the `src/asr_engine/modules/` subpackage, and bundled audio cues in `src/asr_engine/sounds/`.
- **Dependency/venv management:** `uv`. Dev tooling lives in the `dev` dependency group (`uv sync --dev`), not in runtime `dependencies`. Runtime deps: `mcp[cli]`, `sounddevice`, `numpy`, `deepgram-sdk`.
- **Build backend:** `uv_build`, packaging the bundled WAV cues via `[tool.uv_build] include = ["src/asr_engine/sounds/*.wav"]`.
- **Linting/formatting:** `ruff`. Config in `[tool.ruff]`, `target-version` pinned to the minimum Python.
- **Testing:** `pytest` (with `pytest-asyncio`, `pytest-mock`, `pytest-timeout`), in two physically-separated tiers — a fast, deterministic, no-network default run (`tests/`) and an opt-in live tier (`tests-e2e/`) that calls the real Deepgram API. Full strategy is specced in [testing.md](testing.md).
- **Type checking:** `pyright` (`standard` mode), a dev dependency run via `uv run pyright`. Config lives in `[tool.pyright]` in `pyproject.toml`, targeting `src`, `tests`, and `tests-e2e`, pinned to the `.venv`.
- **Entry points** (`[project.scripts]`): `asr-engine-mcp` → `asr_engine.mcp_server_cli:main`, `asr-mcp-client` → `asr_engine.asr_resource_client:main`, `asr-to-terminal` → `asr_engine.asr_to_terminal:main`.
- **Logging:** library modules only acquire a logger (`log = logging.getLogger(__name__)`) and never configure handlers or call `basicConfig`; `ASREngine` additionally sets the *level* of the `asr_engine` package logger from `config.logging.level` at construction (level only). Entry points still call `setup_logging()` from `asr_engine._logging` at startup for handlers/formatting. See [AGENTS.md](../AGENTS.md) "Logging conventions".
- **Repo shape:**
  - `src/asr_engine/` — the package, one module per core concept.
  - `specs/` — pre-implementation design docs, one per concept (this folder).
  - `plans/` — implementation plans turning settled specs into buildable steps.
  - `tests/` at repo root, mirroring the `src/asr_engine/` module structure.
  - `tests-e2e/` at repo root, for the live tier — not collected by the default `pytest` run.

## Open questions

None currently.
