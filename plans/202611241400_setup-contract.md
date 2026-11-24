# Setup contract

**Status:** Done

Implements [specs/setup.md](../specs/setup.md) and the changes it brings to [configuration.md](../specs/configuration.md) (errors, the engine block's fields declared once), [asr-module-interface.md](../specs/asr-module-interface.md) (a registry of classes, the constructor contract, API keys), [engine.md](../specs/engine.md) (`secrets`), [vad.md](../specs/vad.md), [batch-asr-module.md](../specs/batch-asr-module.md) and each module spec under `specs/modules/`. Each of those specs went `Implemented → Updated → Implemented` in this change. Leaves out the demo page, rebuilt on these functions by its own plan.

## Scope

- `src/asr_engine/errors.py` — new: `ConfigError(ValueError)`, `MissingSecretError(ConfigError)`
- `src/asr_engine/modules/fields.py` — new: `Field`, `read_fields`, `field_problems`, `fields_schema` (the shared copy, unchanged)
- `src/asr_engine/config.py` — the engine block's own fields as `Field`s, `engine_problems`, `from_dict` raising the first; `ConfigError` throughout
- `src/asr_engine/modules/base.py` — `Declared` (the setup side of a module), `key_env_field`, `refuse_api_key`, the probe helpers (`get_json`, `models_url_for`, `probe_listing`); `format_for`; `resolve_api_key` becomes a classmethod
- `src/asr_engine/modules/__init__.py`, `src/asr_engine/vad/__init__.py` — registries of classes; `LazyModule` / `LazyVAD` removed
- every module (`fake`, `deepgram` (`_config.py` now `DeepgramModule`), `mistral`, `openai_realtime_compatible`, `openai_transcriptions_compatible`, `mlx_audio`, `kyutai`, `batch.py` with `batch_fields`, `vad/silero`) — declarations, `validated` first in `__init__`, provider imports moved into `imports()` and the methods using them, `secrets` for the modules that read a key, `probe`, and `check_files` / `prepare` for the local ones
- `src/asr_engine/engine.py` — `secrets`, `takes_secrets`, `gating_problem`, `ConfigError`
- `src/asr_engine/setup.py` — new: the seven functions; `check_setup`, `prepare`, `options`, `probe` take `base_dir`, and the loader (`from_dict` / `from_json` / `from_json_file`, the file's folder by default) resolves relative paths with the same `config.resolve_paths`
- `pyproject.toml`, `uv.lock` — `module-setup` in `dev`, from git, on its `base-dir` branch until module-setup #7 was merged, then `main`
- `examples/config/` → `examples/configs/`
- `tests/test_setup.py` (new), `tests/modules/test_fields.py` (new), `tests/conftest.py` (`provider_keys`), `tests/test_docs.py` (spec fields against the declarations), and the module, registry, config, engine and CLI tests updated for the new messages and for keys taken from the environment
- `docs/setup.md` (new), `docs/configuration.md`, `docs/troubleshooting.md`, `docs/modules/*`, `docs/index.md`, `README.md`, `examples/README.md`, `AGENTS.md`

## Steps

1. Copy `fields.py`; add `errors.py`.
2. `Declared` on `ASRModule` and `VADModule`; key helpers.
3. Each module: declare `kind`, `title`, `extra`, `fields` (and `schema_extras`, `config_rules`); `validated` then `imports` in `__init__`; move the provider imports out of module level; `secrets`; `probe`; local files.
4. Registries of classes; `resolve_module_class` / `resolve_vad_class` without import machinery.
5. `config.py`: fields, `engine_problems`, `from_dict` over them.
6. `engine.py`: `secrets`, `gating_problem`.
7. `setup.py`: schema, extras, checks, prepare, options, probe.
8. Tests, then specs and docs; move the example configs.

## Verification

`uv run ruff check .`, `uv run ruff format --check .`, `uv run pyright`, `uv run pytest` (the fast tier, the conformance check included). `setup.probe` was run live against Deepgram (v1, v2), Mistral, OpenAI (both protocol modules) and mlx-audio with Silero: each `ok`, and an unset variable reported. The live tier ran with the keys present.
