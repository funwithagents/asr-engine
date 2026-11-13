# Batch base class and mlx_audio module

**Status:** Done

Implements [specs/batch-asr-module.md](../specs/batch-asr-module.md) (the shared `BatchASRModule`) and [specs/modules/mlx-audio.md](../specs/modules/mlx-audio.md) (its first subclass, local STT on Apple Silicon through mlx-audio). It delivers the base class, the module, the exclusive `mlx-audio` extra and its separate contributor environment, fast tests on both, a live e2e row that runs only behind the VAD gate, and the doc touch-ups. It leaves out what the specs defer: streaming mlx-audio models, language memory, a wall-clock interim timer.

## Scope

- `src/asr_engine/modules/batch.py` — `BatchASRModule`
- `src/asr_engine/modules/mlx_audio/{__init__,module,runner,download}.py` — the module, its mlx-audio runner, and its weights download
- `src/asr_engine/modules/kyutai/download.py` — Kyutai's weights download (replaces the benchmark script as the fetch path)
- `src/asr_engine/modules/__init__.py` — `mlx_audio` registry entry (`extra=None`)
- `pyproject.toml`, `uv.lock`, `.gitignore` — `mlx-audio` extra, `[tool.uv] conflicts`, `all`/`dev` holding mlx-audio (named directly in `dev`) and dropping the Kyutai extras, `.venv-kyutai/`
- `tests/modules/test_batch.py`, `tests/modules/test_mlx_audio.py`
- `tests-e2e/helpers.py`, `tests-e2e/test_engine_modules.py` — `AUDIO_WAV_16000`, the row, the local-model gate (weights cached, via each module's `download.py`), ungated cases skipping `"required"` modules
- `examples/config/mlx_audio.json`, `tests/examples/test_configs.py`
- Specs and docs: `specs/_index.md`, `plans/_index.md`, `asr-module-interface.md`, `project.md`, `vad.md`, `e2e-testing.md`, `configuration.md`, `ci.md`, `README.md`, `examples/README.md`, `AGENTS.md`

## Steps

1. **Dependencies.** Add the extra and the conflicts, relock. *Changed in review:* the first version kept Kyutai in `all`/`dev` and gave mlx-audio its own environment; it was flipped, since `moshi-mlx` is the stale side. Now `all`/`dev` hold mlx-audio (`dev` names `mlx-audio[stt]` directly, for the uv quirk) and the Kyutai extras run in `.venv-kyutai`. Check that the main env has mlx-audio 0.6.x and that `.venv-kyutai` gets `moshi-mlx`.
2. **`BatchASRModule`.** Config parsing, one-thread executor, resident load future, the drain loop (buffer, interim scheduling by audio clock, length cap, marker), generation-guarded interims, chained finals, stop flush with a bounded wait, load retry with backoff while draining.
3. **`mlx_audio`.** `runner.py` (load and warm, transcribe via `mx.array`), and `module.py` (config, platform and package check unless a loader is injected, delegate to the runner). Register it.
4. **Fast tests** per both specs' "Fast tier" lists.
5. **E2E.** Add `AUDIO_WAV_16000` and the row. *Changed in review:* the env-var opt-ins (`ASR_ENGINE_E2E_KYUTAI`/`_MLX_AUDIO`) gave way to a cached-weights gate, with a `download.py` in each local module (`kyutai`, `mlx_audio`) that fetches and checks the weights. Add an ungated table that leaves out `"required"` modules. Run it live in the main environment, and the `kyutai` rows in `.venv-kyutai`.
6. **Example config and docs.** Set the statuses (both specs `Implemented`, this plan `Done`) in the files and the indexes.

## Verification

- `uv run ruff check .`, `uv run ruff format --check .`, `uv run pyright` clean (main env, no mlx-audio)
- `uv run pytest tests/` green
- `zsh -ic 'uv run pytest tests-e2e'` green; with an empty Hugging Face cache, the local rows skip and name the download command
- live run of `-k mlx_audio` in the main environment (weights cached), green
- live run of `-k kyutai` in `.venv-kyutai` (weights cached), green
- CI on the pull request green, with the `mlx_audio` case among the expected skips
