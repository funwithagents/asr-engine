# Server-only package: move clients & asr-to-terminal to examples

**Status:** Done

Implements the settled reframing in `specs/project.md` ("Repo shape"),
`specs/overview.md` ("Components"), `specs/demo-client.md`, `specs/asr-to-terminal.md`,
and `specs/gradio-demo.md` ("Testing"): the `asr_engine` package becomes
**server-only** (engine, tools, MCP server, config, audio, modules,
segmentation, sound feedback, logging). Every client-side concern — the MCP
subscription SDK, the demo CLI, and the `asr-to-terminal` bridge — moves out to
`examples/`, and the example tree gains fast (`tests/`) and live (`tests-e2e/`)
coverage, including new fast tests for the Gradio `DemoController`. It
deliberately does **not** change any engine/server/tool behavior — this is a
relocation + test-coverage change, not a feature change.

## Scope

The exact files this plan touches.

### Files moved out of the package (git `mv`, preserving names)

- `src/asr_engine/resource_subscriber.py` → `examples/mcp_client/resource_subscriber.py`
- `src/asr_engine/resource_client.py` → `examples/mcp_client/resource_client.py`
- `src/asr_engine/asr_resource_client.py` → `examples/mcp_client/asr_resource_client.py`
- `src/asr_engine/terminal_typer.py` → `examples/asr_to_terminal/terminal_typer.py`
- `src/asr_engine/asr_to_terminal.py` → `examples/asr_to_terminal/asr_to_terminal.py`

### New files

- `examples/mcp_client/__init__.py` — package marker (like `examples/gradio_demo/__init__.py`)
- `examples/asr_to_terminal/__init__.py` — package marker
- `tests/examples/__init__.py` — package marker for the example test subtree
- `tests/examples/test_gradio_controller.py` — new fast tests for `DemoController`

### Fast tests relocated (they now cover `examples/` code)

- `tests/test_subscriber.py` → `tests/examples/test_subscriber.py`
- `tests/test_client.py` → `tests/examples/test_client.py`
- `tests/test_asr_to_terminal.py` → `tests/examples/test_asr_to_terminal.py`

### Import / reference updates

- Internal imports in the moved files (see Steps 2).
- Patch targets + imports in the relocated fast tests.
- `tests-e2e/test_asr_to_terminal.py` — `from asr_engine.asr_to_terminal import AsrToTerminal`
  → `from examples.asr_to_terminal.asr_to_terminal import AsrToTerminal`.
- `tests-e2e/test_mcp_resource.py` — `from asr_engine.resource_client import AsrResourceClient`
  → `from examples.mcp_client.resource_client import AsrResourceClient`.

### Config / packaging

- `pyproject.toml` — drop the `asr-mcp-client` and `asr-to-terminal` entries from
  `[project.scripts]` (only `asr-engine-mcp` remains); add `"."` to
  `[tool.pytest.ini_options].pythonpath` so `examples.*` resolves under pytest in
  both tiers.

### Docs / spec mappings (the machine-checked flips deferred from the spec edits)

- `AGENTS.md` — remove the five moved modules from the Project map table; reword
  "What this project is" (clients/bridge are examples, not deliverables); update
  the entry-points and Commands sections.
- `README.md` — `Running` (drop the two `uv run` client commands; show
  `python -m examples.…`), the `asr-to-terminal` section, the "Available push
  clients" list, and the `AsrResourceClient` path reference.
- Spec `code:`/`tests:` frontmatter: `project.md` (drop the two moved scripts),
  `demo-client.md` (→ `examples/mcp_client/*`), `asr-to-terminal.md`
  (→ `examples/asr_to_terminal/*`), `gradio-demo.md` (add the new test to `tests:`).
- Flip the five specs `Updated` → `Implemented` in each spec and in
  `specs/_index.md` once verification passes.
- `__init__.py` of `asr_engine`: no change — it already exports only
  engine/tools/config/audio/data types, none of the moved modules.

## Steps

Ordered so the tree is importable before references are repointed.

1. **Create the example subpackages.** `git mv` the five modules to their new
   homes (names unchanged). Add `examples/mcp_client/__init__.py` and
   `examples/asr_to_terminal/__init__.py`.
2. **Fix intra-example imports.** In the moved files, rewrite the cross-module
   imports to the new locations:
   - `resource_client.py`: `from asr_engine.resource_subscriber import ResourceSubscriber`
     → `from examples.mcp_client.resource_subscriber import ResourceSubscriber`.
   - `asr_resource_client.py`: `from asr_engine.resource_client import AsrResourceClient`
     → `from examples.mcp_client.resource_client import AsrResourceClient`.
   - `asr_to_terminal.py`: `from asr_engine.resource_client import AsrResourceClient`
     → `from examples.mcp_client.resource_client import AsrResourceClient`; the
     `terminal_typer` import → `from examples.asr_to_terminal.terminal_typer import KeystrokeSink, TerminalTyper`.
   - `resource_subscriber.py`, `terminal_typer.py`: no `asr_engine` imports — no change.
3. **Drop the two console scripts** from `[project.scripts]` in `pyproject.toml`,
   leaving `asr-engine-mcp`. Add `"."` to `pythonpath`. Run `uv sync` so the
   removed entry points are unregistered.
4. **Relocate + repoint the fast tests** into `tests/examples/` (add
   `tests/examples/__init__.py`). Update every import and `patch(...)` target
   from `asr_engine.<mod>` to the new `examples.<pkg>.<mod>` path — e.g.
   `patch("asr_engine.asr_to_terminal.AsrResourceClient")`
   → `patch("examples.asr_to_terminal.asr_to_terminal.AsrResourceClient")`,
   `patch("asr_engine.resource_subscriber.ClientSession")`
   → `patch("examples.mcp_client.resource_subscriber.ClientSession")`, etc.
5. **Repoint the e2e imports** in `tests-e2e/test_asr_to_terminal.py` and
   `tests-e2e/test_mcp_resource.py` (see Scope). No behavior change.
6. **Add `tests/examples/test_gradio_controller.py`** — fast tests driving
   `DemoController` with a fake in-process engine (no Gradio import, no real
   audio/backend). Cover the scenarios `gradio-demo.md` now names: rebuild-on-
   config-change (`set_device`/`set_module` discard the engine while stopped,
   raise while running), `listen` cancellation via `stop()`, param validation
   (non-positive timeouts rejected with a message), and phase transitions
   (`stopped`→`running`→`dictating`→`running`, `listening`→`stopped`). Drive the
   controller's public methods and assert on `state()`, per `testing.md`
   (scenario-not-field, public-API).
7. **Update the machine-checked mappings** in one pass so the guard tests stay
   green: remove the five rows from the `AGENTS.md` Project map, update the spec
   frontmatter `code:`/`tests:` lists (Scope), and update `AGENTS.md` prose +
   `README.md` running/clients sections to the `python -m examples.…` form.
8. **Flip statuses** `Updated` → `Implemented` for `project.md`, `overview.md`,
   `demo-client.md`, `asr-to-terminal.md`, `gradio-demo.md` (in each spec and in
   `specs/_index.md`), and mark this plan `Done` here and in `plans/_index.md`.

## Verification

- `uv run ruff check .` and `uv run ruff format .` — clean.
- `uv run pyright` — resolves `examples.mcp_client.*` / `examples.asr_to_terminal.*`
  from both the moved files and the tests (examples/ is already on pyright's
  `include`); no unresolved imports.
- `uv run pytest tests/` — green, including:
  - `tests/test_project_map.py` (map lists exactly the remaining `src/asr_engine/*.py`;
    every spec's `code:` paths exist; every concept module still governed).
  - the relocated `tests/examples/test_subscriber.py` / `test_client.py` /
    `test_asr_to_terminal.py` (behavior unchanged, new import paths).
  - the new `tests/examples/test_gradio_controller.py`.
- `zsh -ic 'uv run pytest tests-e2e'` (opt-in, needs `DEEPGRAM_API_KEY`) — the
  repointed `test_asr_to_terminal.py` and `test_mcp_resource.py` still pass
  against the live backend.
- Smoke-run the relocated apps by module: `uv run python -m examples.mcp_client.asr_resource_client`
  and `uv run python -m examples.asr_to_terminal.asr_to_terminal` start and print
  their usage/connect lines.

Mark this plan `Done` (here and in `plans/_index.md`) and flip the five specs to
`Implemented` only once all of the above pass.
