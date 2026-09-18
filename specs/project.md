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
  - tests/test_no_mcp_import.py
---

# Project

**Status:** Implemented

## Purpose

Structure and tooling for the ASR engine project itself: Python version, dependency/packaging management with `uv`, repo layout conventions, and development tooling.

## Decided

- **Python version:** 3.11+ minimum.
- **Package layout:** `src/` layout — `src/asr_engine/...` — not flat, to avoid accidentally importing an uninstalled package from the repo root. One module per core concept; the pluggable ASR backends live in the `src/asr_engine/modules/` subpackage, and bundled audio cues in `src/asr_engine/sounds/`.
- **Dependency/venv management:** `uv`. Dev tooling lives in the `dev` dependency group (`uv sync --dev`), not in runtime `dependencies`. Core runtime deps: `sounddevice`, `numpy`, `soundfile` (libsndfile — decodes file audio sources, incl. MP3, without ffmpeg) — the engine only. **The MCP server stack is not a core dependency either:** it lives in the `mcp` extra (see "Dependency strategy for transports" below). **No ASR provider SDK is a core dependency:** each real backend's SDK lives in an optional extra named after the provider (`[project.optional-dependencies] deepgram = ["deepgram-sdk"]`), so no provider is implicitly the default. The core install can only run the [`fake`](fake-module.md) test double; users install the extra for the backend they choose (`pip install 'asr-engine[deepgram]'`). See [asr-module-interface.md](asr-module-interface.md) "Optional dependencies (extras)".
- **`all` means every extra; the `dev` group names the ones it needs.** These are two different questions and are deliberately kept apart. The **`all` extra is a public install target** — `pip install 'asr-engine[all]'` must bundle *every* provider and transport extra, with no omissions, or the name lies. The **`dev` dependency group is about contributors**: pyright type-checks the provider modules and the MCP server and both test tiers exercise them (including the MCP `examples/`), so `dev` lists those extras explicitly (`"asr-engine[deepgram,mcp,kyutai-mlx]"`) instead of self-referencing `all`. Keeping them separate means a heavy, niche backend still ships to users who ask for `[all]` without landing in every contributor's venv on a plain `uv sync` — the live case is `kyutai-torch` (≈ 2.5 GB of torch), which is in `all` but not in `dev`, while its light sibling `kyutai-mlx` is in both (see [kyutai-module.md](kyutai-module.md) "Installation"). The cost is that a new extra is added in **two** places, so the module checklist names both steps. Library consumers never pull the dev group.
- **Build backend:** `uv_build`, packaging the bundled WAV cues via `[tool.uv_build] include = ["src/asr_engine/sounds/*.wav"]`.
- **Linting/formatting:** `ruff`. Config in `[tool.ruff]`, `target-version` pinned to the minimum Python.
- **Testing:** `pytest` (with `pytest-asyncio`, `pytest-mock`, `pytest-timeout`), in two physically-separated tiers — a fast, deterministic, no-network default run (`tests/`) and an opt-in real-time tier (`tests-e2e/`) whose per-module conformance scenarios call the real provider APIs. Full strategy is specced in [testing.md](testing.md).
- **Type checking:** `pyright` (`standard` mode), a dev dependency run via `uv run pyright`. Config lives in `[tool.pyright]` in `pyproject.toml`, targeting `src`, `tests`, and `tests-e2e`, pinned to the `.venv`.
- **Entry points** (`[project.scripts]`): `asr-engine-mcp` → `asr_engine.mcp_server_cli:main` — the **only** console script. The client-side apps live in `examples/` (outside the wheel) and so cannot be `[project.scripts]` entries; they run as `python -m examples.mcp_client.asr_resource_client` and `python -m examples.asr_to_terminal.asr_to_terminal` (same pattern as the Gradio demo).
- **Logging:** library modules only acquire a logger (`log = logging.getLogger(__name__)`) and never configure handlers, levels, or call `basicConfig` — the library configures nothing. `asr_engine/__init__.py` attaches a `NullHandler` to the `asr_engine` logger so a bare `import asr_engine` that configures nothing drops records silently instead of hitting the last-resort handler. All configuration is the **application layer's** job: entry points and scripts call `setup_logging()` from `asr_engine._logging` (which owns `basicConfig`) at startup. The `asr-engine-mcp` server takes a `--log-level` CLI flag (default `INFO`) as the sole level control — there is no config-file logging block. The `examples/` CLIs are application entry points too and follow the same boundary — module loggers throughout, no `print` for diagnostics — but each configures logging with its **own** `logging.basicConfig(...)` in `main()` rather than importing `asr_engine._logging.setup_logging`, so an example depends only on the public library, never on a private `_`-module.
- **Dependency strategy for transports.** A transport is an interface onto the engine, not the engine itself, and it brings its own server stack: the MCP SDK pulls in `pydantic`, `starlette`, `httpx`, `sse-starlette`, `jsonschema`, `pyjwt`, and `uvicorn`. A program that only does `import asr_engine` (the engine, `AsrTools`) needs none of that, and `pydantic`/`starlette` in particular often have to match versions the host application already pins. So transports follow the same rule as providers:
  - **One optional extra per transport.** `mcp = ["mcp<2", "uvicorn"]` today (`mcp` 2.x renamed `FastMCP`, which `server.py` uses). Installing the server is `pip install 'asr-engine[mcp]'` / `uv sync --extra mcp`; a deployment combines it with a provider, e.g. `asr-engine[mcp,deepgram]`.
  - **The plain SDK, not `mcp[cli]`.** The SDK's `cli` extra only adds `typer`/`python-dotenv` for its own `mcp dev`/`mcp install` tooling, which this project does not use. `uvicorn` is listed explicitly even though the SDK depends on it, because `server.py` imports it directly.
  - **Only the transport's own files import its stack.** `server.py` is the only `src/` module that imports `mcp`, `uvicorn`, or `pydantic`, and nothing on the `import asr_engine` path reaches it. Transport *configs* stay in the base: `MCPServerConfig` is a plain dataclass in `config.py`, importable without the extra.
  - **The console script owns the install hint.** `asr-engine-mcp` is always installed, so `mcp_server_cli.py` has no top-level import of `asr_engine.server`; `main` imports it after parsing arguments and turns a missing `mcp`/`uvicorn` into an install hint (see [mcp-server.md](mcp-server.md) "Installation").
  - **The `examples/` MCP clients** (`mcp_client`, `asr_to_terminal`) also need the SDK; they run from a source checkout whose `dev` group has the extra, and their READMEs install it explicitly.
  - **Guarded by a subprocess test.** `tests/test_no_mcp_import.py` makes `mcp` and `uvicorn` unimportable, then proves `import asr_engine`, `MCPServerConfig.from_dict`, and an `AsrTools` `start`/`stop` round trip on the `fake` module all work, and that `asr_engine.mcp_server_cli.main()` exits with the `asr-engine[mcp]` hint.
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
