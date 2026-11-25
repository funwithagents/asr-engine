# Demo: the unsaved-changes line and a refused start's current reasons

**Status:** Done

Implements `specs/gradio-demo.md` "What the Listen tab says about the Setup tab" (`Implemented → Updated → Implemented` in this change), on app-setup's Setup panel with a draft and a saved state (app-setup #10, its `draft-and-saved` branch until merged).

## Scope

- `pyproject.toml`, `uv.lock` — app-setup on `rev = "draft-and-saved"`, a comment to go back to `main` once app-setup #10 is merged; only app-setup moves in the lock
- `examples/gradio_demo/controller.py` — `refresh_blockers(model)`; the message remembers whether it is a refused start's blockers
- `examples/gradio_demo/app.py` — the unsaved-changes line above Start (`unsaved_notice`), wired to `setup.draft.change` and `setup.saved.change`; the blockers refreshed on the tab's `select`, on `setup.saved.change`, and on the existing tick when `model.secrets.revision` moved from the tab's `gr.State`
- `tests/examples/test_gradio_demo.py` — the tests below
- `specs/gradio-demo.md` — the section, the controller's method, the tests

## Steps

1. Point the app-setup source at the branch; `uv lock --upgrade-package app-setup`; `uv sync`.
2. The controller: a refused-start flag set with the blockers message and cleared by every other message; `refresh_blockers` recomputing `model.blockers(saved)` only while it is set.
3. The page: the `notice` Markdown, the tab's outputs and its refresh step returned by `_listen_tab`, wired in `_wire` once the Setup panel exists; the tick taking and returning the key revision.

## Verification

- A refused start's reasons follow a saved fix and a saved key; another message is left alone; the unsaved line is empty, said, then empty after a save.
- `uv run ruff check .`, `uv run ruff format --check src tests examples scripts tests-e2e`, `uv run pyright`, `uv run pytest tests/`; the demo in a headless browser on a fresh folder: the Setup tab's table with Save at the bottom, the unsaved line appearing on an edit and clearing on Save.
