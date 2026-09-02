---
code:
  - tests/conftest.py
  - tests-e2e/helpers.py
tests:
---

# Testing

**Status:** Implemented

## Purpose

ASR MCP's testing strategy — the two-tier structure and what a good test looks like. It's a **cross-cutting practice**, not a runtime concept: nothing here ships in the library. It exists as a spec so the decisions have one honest home that stays in sync with the setup. The concrete shell commands to run each tier live in [AGENTS.md](../AGENTS.md) "Testing"; the file-based e2e pipeline design (audio source abstraction, fixtures, assertions) is its own concept spec, [e2e-testing.md](e2e-testing.md).

## Two tiers, physically separated

Tests split into two directories, and the split is structural — a directory boundary, not a marker or an opt-out flag:

| Tier | Directory | Network | Deterministic | Purpose |
|---|---|---|---|---|
| Unit / integration | `tests/` | never | yes | normal dev loop |
| Live / e2e | `tests-e2e/` | real Deepgram API | no | verify against a live service |

- **`tests/` is the normal dev loop.** Fast, deterministic, no real network, no credentials — this is what runs on every change and what any contributor or CI can run with zero secrets. `pyproject.toml`'s `testpaths = ["tests"]` points the default `uv run pytest` here. It mirrors the `src/asr_engine/` module layout (`test_<module>.py`, plus the `tests/modules/` subpackage and the `test_project_map.py` drift-guard).
- **`tests-e2e/` is opt-in.** It drives the full pipeline against the real Deepgram API — network, credentials (`$DEEPGRAM_API_KEY`), non-deterministic output — so it is deliberately *not* collected by the default run. Because `testpaths` already excludes it, no marker or flag is needed: the physical separation is the whole mechanism. Run it explicitly (`uv run pytest tests-e2e`). One streaming-lifecycle scenario is parametrized over the ASR modules; shared engine, MCP, and consumer scenarios run once on a default module. The terminal scenarios inject an in-memory keystroke sink, so the live tier does not require `xdotool`, `ydotool`, `xterm`, or a graphical display. Credentials are supplied by name: config carries `api_key_env` (the `$DEEPGRAM_API_KEY` variable name, never the value) and the module's `resolve_api_key` reads it; `tests-e2e/helpers.require_api_key()` skips when the variable is unset.

## What a good test asserts

- **Functional, not tautological.** Exercise what a feature actually does — inputs → outputs, state changes, side effects — not that it runs or matches its own signature. A test that would pass against a broken implementation (asserting a constant, that an object isn't `None`, that a mock was called) isn't worth writing.
- **Test scenarios, not fields.** When verifying a constructed/parsed object, one test asserts all relevant fields together — not one test per field.
- **Observable behavior only.** Assert return values, raised exceptions, calls to collaborators, and changes to public state. Never assert on private attributes (`_foo`).
- **One test per distinct code path.** Merge two tests that exercise the same branch with different data; keep variants only when they trigger genuinely different logic (e.g. `is_final=True` vs `is_final=False`). Merge lifecycle sequences (start/stop, connect/disconnect) into one test that exercises the full cycle.
- **Error paths deserve individual tests.** `missing_key`, `empty_key`, `unknown_type` are distinct validation branches with distinct messages.
- **Delete trivial structural tests.** `isinstance(x, SomeClass)` or `x.name == "literal"` only break if you intentionally change the type or name — not worth a dedicated test.
- **In the e2e tier, assert on behavior, not exact output.** Real service responses vary run to run, so a live test asserts a robust property ("a non-empty transcript came back", "the side effect happened"), never a specific string.

## Speed rule

The full unit suite (`pytest tests/`) must complete in a few seconds. If it doesn't, treat it as a bug: find the slow tests with `pytest --durations=10` and fix the root cause (usually a real timer firing in production code that needs to be made patchable, or a missing stop signal).

## Test isolation

If the package holds process-global or singleton state (a module-level registry, a cached client, a configured logger, a background timer/thread), reset it before and after each test via an `autouse` fixture in the tier's `conftest.py`, so no state leaks across tests.

Live scenarios that wait for asynchronous callbacks or state transitions share
`tests-e2e/helpers.wait_until()` rather than defining local polling loops.

## Open questions

1. **CI wiring.** Nothing here sets up continuous integration. The `tests/` tier is CI-ready (deterministic, no credentials); actually running it on a hosted runner is unbuilt.
