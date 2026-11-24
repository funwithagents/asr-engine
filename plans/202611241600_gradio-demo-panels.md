# Gradio demo on module-setup's panels

**Status:** Done

Implements [specs/gradio-demo.md](../specs/gradio-demo.md), rewritten (`Implemented → Updated → Implemented` in this change): the demo becomes a page with its own Listen tab (a session over the saved config, locked while it runs) next to module-setup's Setup and Secrets panels over `asr_engine.setup` ([setup.md](../specs/setup.md)). Leaves out autostart, which the demo never had.

## Scope

- `examples/gradio_demo/app.py` — `APP` (one section, `asr`; host `asr-engine`; the `fake` module with a script as the default config), `build_page` (Listen, Setup, Secrets; opens on Setup), `main` with `--folder`, `--host`, `--port` in place of `--config`
- `examples/gradio_demo/__main__.py` — new: `python -m examples.gradio_demo`
- `examples/gradio_demo/controller.py` — `DemoController` without a config of its own: `start(model)` / `listen(model, mode)` read the saved config, refuse with its blockers, build the engine (relative paths resolved against the folder) and release it at Stop; `locked()`; the device and module pickers and the segmentation params are gone (they are the Setup panel's)
- `examples/gradio_demo/README.md`, `examples/README.md`, `README.md`, `docs/setup.md`, `AGENTS.md`, `specs/overview.md`
- `tests/examples/test_gradio_demo.py` — replaces `test_gradio_controller.py`
- `pyproject.toml`, `uv.lock` — the `demo` group is `module-setup[ui]`; `kyutai-mlx` declared in conflict with it; typer's deprecation warnings filtered

## Steps

1. The controller over an `AppModel`: blockers, build from `used_config`, release, `locked`.
2. The page: the Listen tab, then `setup_panel` and `secrets_panel`; the model built with `locked=controller.locked`.
3. Tests over a real `AppModel` on a temporary folder and a fake engine.
4. The spec, the READMEs.

## Verification

`uv run ruff check .`, `uv run ruff format --check .`, `uv run pyright`, `uv run pytest`. The page was run on a fresh folder and driven through its endpoints: Start on the default config replayed the fake module's script from the microphone, and Stop released it.
