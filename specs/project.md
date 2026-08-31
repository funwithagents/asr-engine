---
code:
  - pyproject.toml
  - src/asr_mcp/_logging.py
tests:
  - tests/test_project_map.py
---

# Project

**Status:** Implemented

## Purpose

Structure and tooling for the ASR MCP project itself: Python version, dependency/packaging management with `uv`, repo layout conventions, and development tooling.

## Decided

- **Python version:** 3.11+ minimum.
- **Package layout:** `src/` layout — `src/asr_mcp/...` — not flat, to avoid accidentally importing an uninstalled package from the repo root. One module per core concept; the pluggable ASR backends live in the `src/asr_mcp/modules/` subpackage, and bundled audio cues in `src/asr_mcp/sounds/`.
- **Dependency/venv management:** `uv`. Dev tooling lives in the `dev` dependency group (`uv sync --dev`), not in runtime `dependencies`. Runtime deps: `mcp[cli]`, `sounddevice`, `numpy`, `deepgram-sdk`.
- **Build backend:** `uv_build`, packaging the bundled WAV cues via `[tool.uv_build] include = ["src/asr_mcp/sounds/*.wav"]`.
- **Linting/formatting:** `ruff`. Config in `[tool.ruff]`, `target-version` pinned to the minimum Python.
- **Testing:** `pytest` (with `pytest-asyncio`, `pytest-mock`, `pytest-timeout`), in two physically-separated tiers — a fast, deterministic, no-network default run (`tests/`) and an opt-in live tier (`tests-e2e/`) that calls the real Deepgram API. Full strategy is specced in [testing.md](testing.md).
- **Type checking:** `pyright` (`standard` mode), a dev dependency run via `uv run pyright`. Config lives in `[tool.pyright]` in `pyproject.toml`, targeting `src`, `tests`, and `tests-e2e`, pinned to the `.venv`.
- **Entry points** (`[project.scripts]`): `asr-mcp-server` → `asr_mcp.cli:main`, `asr-mcp-client` → `asr_mcp.asr_resource_client:main`, `asr-to-terminal` → `asr_mcp.asr_to_terminal:main`.
- **Logging:** library modules only acquire a logger (`log = logging.getLogger(__name__)`) and never configure handlers; entry points call `setup_logging()` from `asr_mcp._logging` at startup. See [AGENTS.md](../AGENTS.md) "Logging conventions".
- **Repo shape:**
  - `src/asr_mcp/` — the package, one module per core concept.
  - `specs/` — pre-implementation design docs, one per concept (this folder).
  - `plans/` — implementation plans turning settled specs into buildable steps.
  - `tests/` at repo root, mirroring the `src/asr_mcp/` module structure.
  - `tests-e2e/` at repo root, for the live tier — not collected by the default `pytest` run.

## Open questions

None currently.
