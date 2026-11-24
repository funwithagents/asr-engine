# Setup items one by one: required or not, each prepared on its own, with progress

**Status:** Todo

Implements the changes to `specs/setup.md` ("`check_setup` and `prepare`", "`prepare`: one item at a time", "Progress"): every `check_setup` item says whether a session can start without it (`required`), and `prepare` makes one item ready per call, safe to run for two items at the same time, reporting the bytes it downloads against the size it expects. It also moves the conformance check onto `app-setup`, the new name of the package that defines the contract. Nothing else of the contract changes.

## Scope

- `pyproject.toml`, `uv.lock` — the `dev` dependency `module-setup` becomes `app-setup` (from git, `main`)
- `src/asr_engine/modules/base.py` — `required` on the extra, key and unknown-file items; `Declared.preparable_items`; `Declared.prepare(config, item, on_progress)` raising `ValueError` by default
- `src/asr_engine/modules/downloads.py` — new: `ByteProgress` (bytes to progress events) and `fetch` (a Hugging Face repository's files, one by one, their bytes counted through a `tqdm_class` stand-in, or read from the `.incomplete` file on a `huggingface_hub` without the hook)
- `src/asr_engine/modules/kyutai/download.py`, `src/asr_engine/modules/mlx_audio/download.py` — `download(..., on_progress=)` through `fetch`; mlx-audio lists the repository's files matching mlx-audio's own patterns
- `src/asr_engine/modules/kyutai/module.py`, `src/asr_engine/modules/mlx_audio/module.py`, `src/asr_engine/vad/silero/module.py` — items `required: false` and preparable whatever their status (a local path `required: true`, never preparable); `preparable_items`; `prepare` of one item; Silero's `ensure_model(on_progress)` counting the bytes read
- `src/asr_engine/setup.py` — `prepare(config, item, *, on_progress, base_dir)`, the item handed to the selected module that prepares it, `ValueError` otherwise
- `tests/test_setup.py`, `tests/modules/test_downloads.py` — see Verification
- `specs/setup.md`, `docs/setup.md`, the kyutai, mlx-audio and VAD specs, `specs/project.md`, `AGENTS.md` — the above, and the package's new name

## Steps

1. Rename the dev dependency and the import in the tests; relock so `app-setup` is pinned at its `main`.
2. `Declared`: `required: True` on every item it builds; `preparable_items` (`[]`) and `prepare(config, item, on_progress)` raising `ValueError` naming the item.
3. `downloads.py`: `ByteProgress` (start `0.0` or `None`, steps of at least a percent below `1.0`, `1.0` once ready; thread-safe) and `fetch` (sizes from `HfApi.get_paths_info`, cached files counted first, `hf_hub_download` per missing file with the counting bar when its signature takes `tqdm_class`, else the `.incomplete` poller).
4. kyutai and mlx-audio `download.download` through `fetch`; Silero's `ensure_model` feeding `ByteProgress` from `Content-Length`.
5. Each local module: `check_files` items with `required` and a constant `preparable`; `preparable_items`; `prepare` of its item (an already cached one reports `1.0` only), deferring any other id to `Declared.prepare`.
6. `setup.prepare`: find the selected module whose `preparable_items` holds the id, else `ValueError` listing the config's items.

## Verification

- `tests/test_setup.py`: the conformance check against `app_setup`; `required` on every kind of item (extra, key, a model downloaded on first load, a local path); the Silero model prepared alone with fractions from `0.0` to `1.0`, then ready, then prepared again without a fetch; a failed Silero download raising; a local path refused by `prepare`; an unknown id raising `ValueError` naming it; preparable items are exactly what `prepare` takes; the module's model and the Silero model prepared concurrently on two threads, each waiting at a barrier for the other.
- `tests/modules/test_downloads.py`, with and without the `tqdm_class` hook: bytes against size, cached files counted at once, a failure raising, an unknown size reported as `None`.
- `uv run ruff check .`, `uv run ruff format --check src tests examples scripts tests-e2e`, `uv run pyright`, `uv run pytest tests/`.
