# Shared e2e wait helper

**Status:** Done

Consolidates the duplicated condition-polling loops in the live e2e suite into
one typed helper without changing scenario behavior or production code.

## Scope

- `tests-e2e/helpers.py` — add the shared `wait_until` polling helper.
- `tests-e2e/test_engine_modules.py` — use the shared helper.
- `tests-e2e/test_engine_api.py` — use the shared helper.
- `tests-e2e/test_asr_to_terminal.py` — use the shared helper.
- `specs/testing.md` / `specs/e2e-testing.md` — keep helper ownership and
  infrastructure documentation current.
- `specs/_index.md` / `plans/_index.md` — synchronize lifecycle statuses.

## Steps

1. Mark the affected specs `Updated` and register this plan as `In progress`.
2. Add a typed `wait_until(predicate, timeout, interval)` helper using the running
   event loop's monotonic clock.
3. Delete the three local copies and update their callers.
4. Run formatting, lint, type checking, deterministic tests, and live e2e tests.
5. Mark the plan `Done` and specs `Implemented` after verification.

## Verification

- `uv run ruff format .`
- `uv run ruff check .`
- `uv run pyright`
- `uv run pytest tests/`
- `zsh -ic 'uv run pytest tests-e2e/'`

Mark this plan `Done` (here and in [_index.md](_index.md)) only once all checks
pass.

Verified on 2026-09-02: formatting and lint clean, pyright reports no errors,
226 deterministic tests pass, and all 13 live e2e cases pass.
