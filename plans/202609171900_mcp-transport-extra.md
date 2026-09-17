# MCP transport extra

**Status:** Done

Implements the settled behavior in `specs/project.md` ("Dependency strategy for transports", core deps, dev environment) and `specs/mcp-server.md` ("Installation"). Moves the MCP server stack out of core `dependencies` into an `mcp` extra (`mcp` + `uvicorn`, dropping the unused `[cli]` extra), makes `asr-engine-mcp` import the stack lazily and exit with an install hint when it is missing, and guards the boundary with a subprocess test — mirroring `tts-engine`. It deliberately leaves the `examples/` code unchanged: they run from a source checkout whose `dev` group has the extra; only their install docs change.

## Scope

- `pyproject.toml` — remove `mcp[cli]` from `dependencies`; add `[project.optional-dependencies] mcp = ["mcp<2", "uvicorn"]` (a fresh resolve otherwise picks mcp 2.x, which removed `mcp.server.fastmcp` — the unpinned `mcp[cli]` was already exposed to this; only `uv.lock` hid it); dev self-reference becomes `"asr-engine[deepgram,mcp]"`.
- `src/asr_engine/mcp_server_cli.py` — drop the top-level `from asr_engine.server import run_server`; import it inside `main` after argument parsing, mapping a missing `mcp`/`uvicorn` to `SystemExit` with the install hint.
- `tests/test_mcp_server_cli.py` — patch `asr_engine.server.run_server` (the lazy import resolves it there) instead of `asr_engine.mcp_server_cli.run_server`; add a test that a non-extra `ModuleNotFoundError` from the server import is re-raised.
- `tests/test_no_mcp_import.py` — **new**: subprocess guard (library + tools work with `mcp`/`uvicorn` poisoned; `main()` exits with the hint before reading the config).
- `README.md`, `examples/README.md`, `examples/mcp_client/README.md`, `examples/asr_to_terminal/README.md` — install the `mcp` extra where the server or MCP clients are used.
- `AGENTS.md` — "Commands" (`uv sync` comment) and "Adding a new ASR module" (dev self-reference now also carries `mcp`).
- Specs: `project.md` + `mcp-server.md` status `Updated` → `Implemented` once done (here and in `specs/_index.md`).

## Steps

1. **Packaging.** Edit `pyproject.toml` as scoped; `uv sync`; confirm `uv run python -c "import mcp, uvicorn"` works and `typer` is gone from the venv unless something else pulls it (`uv tree --invert --package typer`).
2. **CLI.** In `main`, after `parser.parse_args()`:
   ```python
   try:
       from asr_engine.server import run_server
   except ModuleNotFoundError as exc:
       if exc.name not in _MCP_EXTRA_MODULES:
           raise
       raise SystemExit(_MCP_EXTRA_HINT) from exc
   ```
   (exact name match, not the root: a missing `mcp.server.fastmcp` means an incompatible `mcp`, not a missing extra) with `_MCP_EXTRA_MODULES = {"mcp", "uvicorn"}` and `_MCP_EXTRA_HINT = "asr-engine-mcp requires the mcp extra: pip install 'asr-engine[mcp]'"`. Keep the rest of `main` (logging, config errors, `(ValueError, ImportError)` around `run_server`) as is.
3. **CLI tests.** Retarget the `patch(...)` calls; add a parametrized re-raise test: inside `patch.dict(sys.modules)`, drop `asr_engine.server` and poison `asr_engine.server` or `mcp.server.fastmcp` with `None` — the resulting `ModuleNotFoundError` must propagate (not `SystemExit`).
4. **Guard test.** `tests/test_no_mcp_import.py` runs a script in `subprocess` that sets `sys.modules["mcp"] = sys.modules["uvicorn"] = None`, then: `import asr_engine`; `MCPServerConfig.from_dict({"engine": {"module": {"type": "fake", "utterances": []}, "sound_feedback": {"enabled": False}}})`; builds `ASREngine(cfg.engine, audio_source=ScriptableAudioSource(real_time=False))` and round-trips `AsrTools.start()` / `is_running()` / `stop()`; runs `main()` with `--config missing.json` and asserts `SystemExit` carrying the `asr-engine[mcp]` hint; asserts both sentinels are still `None` (never imported).
5. **Docs.** README install block (`uv sync --extra deepgram --extra mcp` / `pip install 'asr-engine[mcp,deepgram]'` for the server; engine-only users need just the provider extra), MCP server section note, dev section; examples READMEs; AGENTS.md.
6. **Statuses.** Flip both specs to `Implemented` and this plan to `Done` once verification passes.

## Verification

- `uv run pytest tests/test_mcp_server_cli.py tests/test_no_mcp_import.py tests/test_project_map.py`
- `uv run ruff check .`, `uv run ruff format --check .`, `uv run pyright`, `uv run pytest tests/`
- Manual: in a throwaway venv with only the core install (`uv run --no-default-groups --isolated --with . asr-engine-mcp --config x.json` or equivalent), confirm the hint and exit 1.

Mark this plan `Done` (here and in [_index.md](_index.md)) only once all pass.
