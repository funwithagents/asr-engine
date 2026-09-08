# Config constructor ladder + MCPServerConfig rename

**Status:** Done

Implements the settled shape in `specs/configuration.md` ("Constructors"). Renames the whole-file config `AppConfig` → `MCPServerConfig`, drops the free `load_config` function, and gives both `ASREngineConfig` and `MCPServerConfig` the same `from_dict` / `from_json` / `from_json_file` ladder. `ASREngineConfig`'s file/string constructors take the `engine` block shape; `MCPServerConfig`'s take the whole `{server, engine}` file shape.

## Scope

- `src/asr_engine/config.py` — rename `AppConfig` → `MCPServerConfig`; add `MCPServerConfig.from_dict/from_json/from_json_file`; add `ASREngineConfig.from_json/from_json_file`; drop `load_config` (folded into `MCPServerConfig.from_json_file`); shared `_read_file` + `_parse_config_json` helpers; retype `validate_asr_type` to take `ASREngineConfig` (it only ever read `.module.type`).
- `src/asr_engine/mcp_server_cli.py` — load via `MCPServerConfig.from_json_file`.
- `src/asr_engine/server.py` — `run_server(config: MCPServerConfig, …)`.
- `examples/gradio_demo/app.py` — `MCPServerConfig.from_json_file(args.config).engine`.
- `tests/test_config.py` — swap call sites; add `from_json`/`from_json_file` tests for both configs (engine-block vs whole-file shape, malformed-JSON, file naming itself in parse errors); `validate_asr_type` helper now builds an `ASREngineConfig`.
- `tests/test_server.py` — `AppConfig` → `MCPServerConfig`.
- `specs/configuration.md` — document the constructor ladder and the two file shapes.

## Steps

1. In `config.py`, add module-level `_read_file(path)` and `_parse_config_json(text, source=None)` helpers.
2. Add `ASREngineConfig.from_json`/`from_json_file` (engine-block shape) over the existing `from_dict`.
3. Rename `AppConfig` → `MCPServerConfig`; give it `from_dict` (parses `server`, delegates `engine`), `from_json`, `from_json_file`; delete `load_config`.
4. Retype `validate_asr_type(config: ASREngineConfig, registry)`.
5. Update `mcp_server_cli.py`, `server.py`, `gradio_demo/app.py` to the new names.
6. Update tests; add the new constructor tests.
7. Update `specs/configuration.md` prose.

## Verification

`uv run ruff check .`, `uv run ruff format .`, `uv run pyright` (0 errors), `uv run pytest tests/` (all pass, incl. the new constructor tests). Done.
