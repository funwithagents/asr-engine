# Implementation Details — Plan 01: Project Setup

## What was implemented

- Initialized a `uv` lib project (`uv init --lib`) with `requires-python = ">=3.11"`
- Added all runtime deps (`mcp[cli]`, `sounddevice`, `numpy`, `deepgram-sdk`) and dev deps (`pytest`, `pytest-asyncio`, `pytest-mock`) in `pyproject.toml`
- Defined entry points `asr-mcp-server` and `asr-mcp-client` under `[project.scripts]`
- Created stub files for the full source tree under `src/asr_mcp/` and test tree under `tests/`
- Configured pytest with `testpaths = ["tests"]` and `asyncio_mode = "auto"` via `[tool.pytest.ini_options]`
- Created `config.example.json` with a Deepgram template and `.gitignore` excluding `config.json`

## Deviations from spec

None.

## Non-obvious decisions

- `uv init --lib` generates a `[build-system]` using `uv_build`, which is the correct backend for uv-managed lib projects.
- Dev dependencies are placed under `[dependency-groups]` (uv convention) rather than `[project.optional-dependencies]`.
- `uv sync` resolved to Python 3.11.12 (the floor version) despite 3.13.3 being available on the system — this is because uv picks the interpreter matching `requires-python` exactly when available. The environment still works with 3.13.
- Added a `test_placeholder` sentinel test in `tests/test_config.py` to avoid pytest exit code 5 (no tests collected). It will be replaced by real tests in plan 02.

## Known limitations

- All source files are empty stubs; no real functionality yet.
