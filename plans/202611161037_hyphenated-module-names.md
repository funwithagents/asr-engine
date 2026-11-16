# Hyphenated module names and one `openai-compatible` extra

**Status:** Done

Implements the naming rule agreed on 2026-11-16 for asr-engine, tts-engine and WICA, written into `specs/asr-module-interface.md` ("Module names", "Optional dependencies (extras)"). Registry keys are now spelled with `-` (`deepgram-v1`, `mistral-realtime`, `mlx-audio`, `openai-realtime-compatible`, …). Each old underscore key keeps working through a rename table that logs a deprecation warning naming the new key. The two OpenAI-protocol extras merge into one `openai-compatible` extra. Python package and directory names (`modules/openai_realtime_compatible/`) keep their underscores, and the provider extras (`deepgram`, `mistral`, `mlx-audio`, `kyutai-*`) are unchanged. The segmentation modes (`trigger_word`, …) are exempt from the rule: the rule covers registry keys, not enumerated mode values. `engine.vad.type` has one key, `silero`, which needs no rename.

## Scope

- `src/asr_engine/modules/__init__.py`: the registry gets hyphen keys. Add `RENAMED_MODULE_TYPES` (old → new) and `canonical_module_type()`, which logs a warning on an old key. `resolve_module_class` applies it. Both protocol entries name `extra="openai-compatible"`.
- `src/asr_engine/config.py`: `ASREngineConfig.from_dict` stores the canonical `module.type`, so the engine, its logs and the demos see the new name. `validate_asr_type` accepts an old key.
- `src/asr_engine/modules/*/`: the module labels (`_LABEL`, `resolve_api_key` labels) and docstrings use the new keys and extra.
- `pyproject.toml`, `uv.lock`: `openai-compatible = ["websockets>=14", "httpx>=0.27"]` replaces `openai-realtime-compatible` and `openai-transcriptions-compatible`, with no aliases (those extras date from 2026-11-13 and nothing mirrors them). Update `all` and `dev` to match.
- `examples/config/*.json`: hyphen `type` values, and each file renamed to its hyphen name (`deepgram-v1.json`, …). Update every reference to them (READMEs, `tests/examples/test_configs.py`).
- `tests-e2e/helpers.py`, `tests-e2e/test_engine_modules.py`: `MODULES` rows, ids and `VAD_REQUIRED_MODULES` use the hyphen keys. `.github/workflows/ci.yml` and `scripts/` follow.
- Tests:
  - `tests/modules/test_registry.py`: each old key resolves to its new key's class and logs a warning naming the new key, and the unknown-type message lists only the new keys.
  - Both protocol modules' install hint names `asr-engine[openai-compatible]`.
  - `tests/test_config.py`: an old key parses to the new key.
  - `tests/test_engine.py`: an engine built from an old-name config and one built from the new name start the same module.
  - The per-module registry tests switch to the new keys.
- Specs: `asr-module-interface.md` (naming rule, rename table, shared-extra rule), `modules/*.md`, `configuration.md`, `vad.md`, `batch-asr-module.md`, `e2e-testing.md`, `ci.md`, `project.md`, `_index.md`. Also `AGENTS.md`, `README.md` and `examples/**/README.md`.

## Steps

1. Write the rule into `specs/asr-module-interface.md`, then update the other specs, `_index.md` and the docs to the new spellings. Set `asr-module-interface.md` to `Updated` until this plan is `Done`.
2. Add the registry keys, the rename table and `canonical_module_type()`. Apply it in `resolve_module_class`, `ASREngineConfig.from_dict` and `validate_asr_type`.
3. Merge the extras in `pyproject.toml`, then run `uv lock`.
4. Rename the example configs and their `type` values. Update module labels, docstrings, the e2e rows, CI comments and scripts.
5. Add the tests listed above, and move the existing tests to the new keys.
6. Run the gate. Set this plan to `Done`, and `asr-module-interface.md` back to `Implemented`.

## Verification

`uv run ruff check .`, `uv run ruff format --check .`, `uv run pyright`, `uv run pytest tests/`. Two tests in `tests/test_engine.py` and `tests/test_config.py` show that a config naming `deepgram_v1` and one naming `deepgram-v1` build the same module class, and that the old name logs the deprecation warning. Collect the live tier with `uv run pytest tests-e2e --collect-only -q` to confirm the new row ids (`-k deepgram-v1`).
