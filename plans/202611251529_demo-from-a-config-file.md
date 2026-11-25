# The demo from a config file, and a config app

**Status:** Done

Implements `specs/gradio-demo.md` ("Running it": the three launches, the keys, the config is the engine block), with the example configs of `specs/configuration.md` and the documentation checks of `specs/project.md`. The demo runs from its folder or from a config file run as it is (its session started at launch), a config app edits one file with the Setup and Secrets panels, and every config the demo reads or writes is the engine block alone. The MCP server's format is unchanged; it gets one example of its own. Left out: a `--start`/`--no-start` autostart for the folder launch (the demo has none).

## Scope

- `pyproject.toml`, `uv.lock` — app-setup on its `config-file` branch (back to `main` once it is merged there)
- `examples/gradio_demo/app.py` — `App(..., bare=True)`; `add_config_arguments` / `open_source`, a `ConfigFileError` as `parser.error`; `open_demo(argv, controller)` starting a file's session at launch (`starts_at_launch`); the page opens on Listen for a file
- `examples/gradio_demo/default_config.json` — the engine block alone
- `examples/gradio_demo/__main__.py`, `controller.py` — the command line and the `base_dir` comment
- `examples/config_app/__init__.py`, `__main__.py` — the config app: `add_config_app_arguments`, `open_config_app(options, APP)`, `config_page(model)`
- `examples/configs/*.json` — the engine block alone
- `examples/mcp/config.json` — the MCP server's example: a `server` block, the `fake` module
- `.gitignore` — `.env`; `examples/mcp/config.json` kept despite the `config.json` rule
- `tests/examples/test_gradio_demo.py` — `bare` and `source`; the launches (file, blockers at launch, `.env` next to the file, missing file, folder)
- `tests/examples/test_config_app.py` — the config app saving the engine block to its file; a missing file refused
- `tests/examples/test_configs.py` — `check_config` over every engine config; the MCP example as a server config, its engine block replaceable by each
- `tests/test_setup.py` — the conformance check reads the engine configs as they are
- `tests/test_docs.py` — named configs under `examples/mcp/` checked too
- `README.md`, `AGENTS.md`, `docs/` (`index.md`, `setup.md`, `agents-and-mcp.md`, `modules/*.md`), `examples/README.md`, `examples/gradio_demo/README.md`, `examples/mcp_client/README.md` — the launches, the `.env`, engine configs, the MCP example
- `specs/gradio-demo.md`, `specs/configuration.md`, `specs/project.md` (and the wording in `specs/asr-module-interface.md`, `specs/modules/*.md`)

## Steps

1. Point app-setup at `config-file`; `uv lock --upgrade-package app-setup`; `uv sync`.
2. Unwrap `default_config.json` and every `examples/configs/*.json` (the `engine` block); write `examples/mcp/config.json`.
3. The demo: `bare=True`, the shared command line, `open_demo` starting a file's session, the page's first tab.
4. The config app.
5. The tests above; the documentation.
6. Verify, then flip the statuses.

## Verification

`uv run ruff check .`, `uv run ruff format src tests examples`, `uv run pyright`, `uv run pytest tests/`. Mark this plan `Done` (here and in [_index.md](_index.md)) only once all pass, with `specs/gradio-demo.md`, `specs/configuration.md` and `specs/project.md` back to `Implemented`.
