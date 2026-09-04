# ASREngineConfig.from_dict

**Status:** Done

Implements the settled behavior in `specs/configuration.md` ("`engine` block → `ASREngineConfig`"). Promotes the module-private `_parse_engine` into a public `ASREngineConfig.from_dict(engine_block)` classmethod so direct importers can build a validated engine config from an in-memory dict, with `load_config` delegating to it. Behavior-preserving refactor — no new validation or defaults.

## Scope

- `src/asr_engine/config.py` — move the body of `_parse_engine` into `ASREngineConfig.from_dict`; remove `_parse_engine`; have `load_config` call `ASREngineConfig.from_dict(data.get("engine", {}))`.
- `tests/test_config.py` — functional tests driving `from_dict` directly (happy path, defaults, credential-free build, each validation error, parity with `load_config`).
- `specs/configuration.md` — document `from_dict` as the public in-memory constructor; note `load_config` is implemented in terms of it.

## Steps

1. Add `from_dict(cls, engine_block)` classmethod on `ASREngineConfig` holding the exact logic previously in `_parse_engine`, returning `cls(...)`.
2. Delete `_parse_engine` and point `load_config` at `ASREngineConfig.from_dict`.
3. Add the `from_dict` tests alongside the existing `load_config` tests, including a `load_config(path).engine == ASREngineConfig.from_dict(block)` parity check.
4. Update the spec text and keep its status honest (`Implemented → Updated → Implemented`).

## Verification

Add the tests above; then run lint (`uv run ruff check .`), format check, type-check (`uv run pyright`), and the fast tier (`uv run pytest tests/`). Mark this plan `Done` (here and in [_index.md](_index.md)) only once all pass.
