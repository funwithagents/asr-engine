# openai_transcriptions_compatible module

**Status:** Done

Implements [specs/modules/openai-transcriptions-compatible.md](../specs/modules/openai-transcriptions-compatible.md): a `BatchASRModule` that uploads each VAD-delimited utterance to any `POST /v1/audio/transcriptions` server (hosted OpenAI by default). It delivers the module, its extra, fast tests against an in-process server, a live e2e row behind the VAD gate, an example config and the doc touch-ups. It leaves out what the spec defers: request retries, streaming responses, compressed uploads.

## Scope

- `src/asr_engine/modules/openai_transcriptions_compatible/{__init__,module}.py`
- `src/asr_engine/modules/batch.py`: `DEFAULT_INTERIM_INTERVAL_S` class attribute (a subclass's default for `interim_interval_s`)
- `src/asr_engine/modules/__init__.py`: registry entry
- `pyproject.toml`, `uv.lock`: `openai-transcriptions-compatible` extra (`httpx>=0.27`), in `all` and `dev`
- `tests/modules/test_openai_transcriptions_compatible.py`
- `tests-e2e/helpers.py`: the `MODULES` row, `VAD_REQUIRED_MODULES`
- `examples/config/openai_transcriptions_compatible.json`, `tests/examples/test_configs.py`
- Specs and docs: `specs/_index.md`, `plans/_index.md`, `asr-module-interface.md`, `batch-asr-module.md`, `vad.md`, `e2e-testing.md`, `configuration.md`, `ci.md`, `README.md`, `examples/README.md`, `AGENTS.md`

## Steps

1. **Probe** hosted OpenAI: response shape, latency cold and warm, silence, the minimum length, error bodies (recorded in the spec's "Measured").
2. **Base class:** `DEFAULT_INTERIM_INTERVAL_S`, so this module defaults interims off.
3. **Module:** config validation, the `httpx.Client` and warm-up in `load()`, WAV encoding, the post with its error mapping in `transcribe()`. Register it, add the extra, relock.
4. **Fast tests** per the spec's "Fast tier" list.
5. **E2E row** and `VAD_REQUIRED_MODULES`; run it live.
6. **Example config and docs.** Set the statuses (spec `Implemented`, this plan `Done`) in the files and the indexes.

## Verification

- `uv run ruff check .`, `uv run ruff format --check .`, `uv run pyright` clean
- `uv run pytest tests/` green
- `zsh -ic 'uv run pytest tests-e2e'` green, the new case included
- CI on the pull request green, the new case running on the live job (`OPENAI_API_KEY` secret)
