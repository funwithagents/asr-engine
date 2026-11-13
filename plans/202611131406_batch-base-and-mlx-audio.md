# Batch base class and mlx_audio module

**Status:** Done

Implements [specs/batch-asr-module.md](../specs/batch-asr-module.md) (the shared `BatchASRModule`) and [specs/modules/mlx-audio.md](../specs/modules/mlx-audio.md) (its first subclass, local STT on Apple Silicon through mlx-audio). It delivers the base class, the module, the exclusive `mlx-audio` extra and its separate contributor environment, fast tests on both, a live e2e row that runs only behind the VAD gate, and the doc touch-ups. It leaves out what the specs defer: streaming mlx-audio models, language memory, a wall-clock interim timer.

## Scope

- `src/asr_engine/modules/batch.py` — `BatchASRModule`
- `src/asr_engine/modules/mlx_audio/{__init__,module,runner}.py` — the module and its mlx-audio runner
- `src/asr_engine/modules/__init__.py` — `mlx_audio` registry entry (`extra=None`)
- `pyproject.toml`, `uv.lock`, `.gitignore` — `mlx-audio` extra, `[tool.uv] conflicts`, `dev` naming `moshi-mlx` directly, `.venv-mlx-audio/`
- `tests/modules/test_batch.py`, `tests/modules/test_mlx_audio.py`
- `tests-e2e/helpers.py`, `tests-e2e/test_engine_modules.py` — `AUDIO_WAV_16000`, the row, the opt-in, ungated cases skipping `"required"` modules
- `examples/config/mlx_audio.json`, `tests/examples/test_configs.py`
- Specs and docs: `specs/_index.md`, `plans/_index.md`, `asr-module-interface.md`, `project.md`, `vad.md`, `e2e-testing.md`, `configuration.md`, `ci.md`, `README.md`, `examples/README.md`, `AGENTS.md`

## Steps

1. **Dependencies.** Add the extra and conflicts, name `moshi-mlx` directly in `dev`, relock. Check that the main env still has `moshi-mlx` and that `.venv-mlx-audio` gets `mlx-audio` 0.6.x (done while designing).
2. **`BatchASRModule`.** Config parsing, one-thread executor, resident load future, the drain loop (buffer, interim scheduling by audio clock, length cap, marker), generation-guarded interims, chained finals, stop flush with a bounded wait, load retry with backoff while draining.
3. **`mlx_audio`.** `runner.py` (load and warm, transcribe via `mx.array`), and `module.py` (config, platform and package check unless a loader is injected, delegate to the runner). Register it.
4. **Fast tests** per both specs' "Fast tier" lists.
5. **E2E.** Add `AUDIO_WAV_16000` and the row, `LOCAL_MODEL_OPT_IN_ENV["mlx_audio"]`, and an ungated table that leaves out `"required"` modules. Run it live in `.venv-mlx-audio`.
6. **Example config and docs.** Set the statuses (both specs `Implemented`, this plan `Done`) in the files and the indexes.

## Verification

- `uv run ruff check .`, `uv run ruff format --check .`, `uv run pyright` clean (main env, no mlx-audio)
- `uv run pytest tests/` green
- `zsh -ic 'uv run pytest tests-e2e'` unchanged (the `mlx_audio` row skips: not opted in)
- `ASR_ENGINE_E2E_MLX_AUDIO=1` live run of `-k mlx_audio` in `.venv-mlx-audio`, green
- CI on the pull request green, with the `mlx_audio` case among the expected skips
