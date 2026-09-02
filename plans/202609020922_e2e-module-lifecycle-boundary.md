# E2E module lifecycle boundary

**Status:** Done

Implements the revised layering in `specs/e2e-testing.md`: every live ASR module
runs one streaming-lifecycle conformance scenario, while direct-engine APIs, MCP
APIs, and example consumers run once on a centrally selected default module.
Backend reconnection fault injection and exhaustive segmentation matrices remain
in the deterministic tier.

## Scope

- `tests-e2e/helpers.py` — rename the default selector around modules, and
  centralize robust transcript normalization.
- `tests-e2e/test_engine_modules.py` — reduce the per-module matrix to one
  start/connect/stream/finalize/reuse/stop lifecycle scenario.
- `tests-e2e/test_engine_api.py` — add default-module live coverage for the public
  direct-engine `listen` and dictation APIs, including the 16 kHz fixture.
- `tests-e2e/test_mcp_resource.py` — use the default-module terminology and robust
  transcript assertions.
- `tests-e2e/test_mcp_tools.py` — use the default-module terminology and remove
  deterministic guard-error checks from the live tier.
- `tests-e2e/test_asr_to_terminal.py` — use the default-module terminology and
  shared transcript normalization.
- `specs/e2e-testing.md` / `specs/testing.md` — specify the ownership boundary,
  test inventory, and non-exact live assertion strategy.
- `specs/_index.md` / `plans/_index.md` — keep statuses synchronized.
- `AGENTS.md` — update contributor guidance for the per-module/default-module split.

## Steps

1. Mark the affected implemented specs `Updated` and register this plan as
   `In progress`.
2. Refactor shared helpers around `default_module()` and transcript normalization.
3. Keep one parametrized module lifecycle test and move representative public
   engine API scenarios to a default-module test file.
4. Update the MCP and terminal tests to the same terminology/assertion helpers;
   remove the live guard-error scenario already covered deterministically.
5. Rewrite the e2e spec and contributor guidance to match the implemented matrix.
6. Run format, lint, type checking, the fast suite, and the opt-in live suite.
7. Mark the plan `Done` and both specs `Implemented` only after verification passes.

## Verification

- `uv run ruff format .`
- `uv run ruff check .`
- `uv run pyright`
- `uv run pytest tests/`
- `zsh -ic 'uv run pytest tests-e2e/'`

The live run must pass both parametrized module lifecycle cases and every
default-module pipeline/API scenario. Mark this plan `Done` (here and in
[_index.md](_index.md)) only once all checks pass.

Verified on 2026-09-02: formatting and lint clean, pyright reports no errors,
226 deterministic tests pass, and all 13 live e2e cases pass.
