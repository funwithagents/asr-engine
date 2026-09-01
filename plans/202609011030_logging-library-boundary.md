# Logging: library boundary + `--log-level` CLI

**Status:** Done

Implements the logging convention in `specs/project.md` ("Decided → Logging"),
`specs/engine.md` ("Construction"), `specs/configuration.md` (drops
`engine.logging`), and `specs/mcp-server.md` (adds `--log-level`). Makes the
importable library configure nothing, moves logging control to the application
layer, and gives the MCP server a `--log-level` CLI flag as the sole level
control (config-file logging is dropped).

## Scope

- `src/asr_engine/__init__.py` — attach a `NullHandler` to the `asr_engine`
  logger; drop `LoggingConfig` from imports/`__all__`.
- `src/asr_engine/engine.py` — remove the `getLogger("asr_engine").setLevel(...)`
  call in `__init__` (engine no longer touches global logging).
- `src/asr_engine/config.py` — remove `LoggingConfig`, the `logging` field on
  `ASREngineConfig`, and the level parse/validate block in `_parse_engine`.
- `src/asr_engine/_logging.py` — `setup_logging` accepts a level (`int | str`)
  and keeps `basicConfig` (it is the application-layer helper).
- `src/asr_engine/mcp_server_cli.py` — add `--log-level` (choices from
  `logging.getLevelNamesMapping()`, default `INFO`); call `setup_logging` with
  it and pass it into `run_server`.
- `src/asr_engine/server.py` — `run_server(config, log_level=...)`; thread the
  level into the uvicorn `log_config`/`uvicorn.Config`.
- `tests/test_config.py` — drop the three `engine.logging` tests.
- `tests/test_engine.py` — drop `test_constructor_sets_package_logger_level`.
- `tests/test_mcp_server_cli.py` — add `--log-level` parsing/precedence + default
  tests; assert `run_server` receives the resolved level.

## Steps

1. **Library stops configuring logging.**
   - In `engine.py` `__init__`, delete the `logging.getLogger("asr_engine").setLevel(config.logging.level)` block and its comment. Keep the module-level `log = logging.getLogger(__name__)`.
   - In `__init__.py`, add `logging.getLogger("asr_engine").addHandler(logging.NullHandler())` (import `logging`), so a bare `import asr_engine` that configures nothing drops records silently instead of hitting `lastResort`.

2. **Drop config-file logging.**
   - Remove the `LoggingConfig` dataclass, the `logging` field on `ASREngineConfig`, and the `log_data`/`level`/validation block in `_parse_engine`. Remove now-unused `import logging` from `config.py`.
   - Remove `LoggingConfig` from `__init__.py` imports and `__all__`.

3. **Application-layer level control.**
   - `_logging.py`: `setup_logging(level: int | str = logging.INFO)` → `basicConfig(level=level, format=...)` (kept). A string level name is accepted (basicConfig resolves it).
   - `mcp_server_cli.py`: add `--log-level` with `choices=sorted(n for n in logging.getLevelNamesMapping() if n != "NOTSET")`, `default="INFO"`. Call `setup_logging(args.log_level)` before running, and pass `log_level=args.log_level` to `run_server`.
   - `server.py`: `run_server(config, log_level="INFO")`; use `log_level.upper()` for the `uvicorn`/`uvicorn.error` logger levels and `uvicorn.Config(..., log_level=log_level.lower())`. Keep `uvicorn.access` at `WARNING` and `mcp` at `WARNING`.

4. **Tests.**
   - `test_config.py`: delete `test_load_config_invalid_logging_level` and `test_load_config_logging_level_case_insensitive`, and the `logging` entries/assertions in the round-trip tests.
   - `test_engine.py`: delete `test_constructor_sets_package_logger_level`.
   - `test_mcp_server_cli.py`: add tests that (a) default `--log-level` is `INFO` and reaches `run_server`; (b) an explicit `--log-level DEBUG` reaches `run_server`; (c) an invalid level exits non-zero (argparse `choices`).

5. **Specs + statuses.** Update `project.md`, `engine.md`, `configuration.md`,
   `mcp-server.md` per the spec edits below; flip each `Implemented → Updated`
   while building, back to `Implemented` when verified. Update both `_index.md`
   files.

## Verification

- `uv run ruff check .` and `uv run ruff format .`
- `uv run pyright`
- `uv run pytest tests/` — all pass, including the new `--log-level` tests.
- Manual smoke: `uv run asr-engine-mcp --log-level DEBUG --config config.json`
  shows engine DEBUG logs; default run shows INFO.

Mark `Done` (here and in [_index.md](_index.md)) only once lint, type-check, and
the fast tier all pass.
