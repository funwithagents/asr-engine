# Audio format conversion

**Status:** Done

Implements [specs/format-conversion.md](../specs/format-conversion.md): the engine keeps a source format and a module format and converts between them, instead of refusing a format the module does not support. Leaves out the audio timeline through the converter, a module-side `channels` / `encoding` choice, and per-instance rates on modules other than `openai-realtime-compatible` (the spec's open questions).

## Scope

- `src/asr_engine/format_converter.py` — new: `AudioFormatConverter` (resample, channels, encoding), `ConvertedAudioSource` (the `AudioSource`-shaped wrapper), `check_convertible`
- `src/asr_engine/modules/base.py` — `resolve_module_audio_format` and `parse_sample_rate_overrides` replace `reconcile_audio_format`; `ASRModule.supported_sample_rates` / `default_sample_rate` instance properties
- `src/asr_engine/modules/openai_realtime_compatible/module.py` — `supported_sample_rates` / `default_sample_rate` config fields
- `src/asr_engine/config.py` — `AudioConfig.sample_rate` / `channels` default to `None`; `ModuleConfig.sample_rate`; `on_unsupported_format` removed (ignored with a warning)
- `src/asr_engine/audio.py` — `read_file_format` (a file's rate and channels from its header)
- `src/asr_engine/engine.py` — `_resolve_formats`, `module_audio_format`, the converter wrapped around the source before the VAD gate; the `soxr` check for the VAD removed
- `pyproject.toml`, `uv.lock` — `soxr` moves from the `silero` extra to the core dependencies
- `tests/test_format_converter.py` — new; `tests/modules/test_base.py`, `tests/test_engine.py`, `tests/test_config.py`, `tests/test_server.py`, `tests/modules/test_kyutai.py`, `tests/modules/test_openai_realtime_compatible.py` — updated and extended
- `tests-e2e/helpers.py`, `tests-e2e/test_engine_modules.py`, `tests-e2e/test_mcp_resource.py` — `MODULES_CONVERTED` and `test_engine_modules_converted`; one keyless scenario through the server
- `examples/config/*.json` — the `sample_rate` fields that are no longer needed are dropped
- Specs: new `format-conversion.md`; `configuration.md`, `asr-module-interface.md`, `audio-source.md`, `engine.md`, `architecture.md`, `vad.md`, `e2e-testing.md`, `ci.md`, `modules/openai-realtime-compatible.md`, `modules/kyutai.md`, `modules/mlx-audio.md`, `modules/openai-transcriptions-compatible.md`, `_index.md`
- Docs: `docs/audio-input.md`, `docs/configuration.md`, `docs/running-the-engine.md`, `docs/troubleshooting.md`, `docs/local-speech-recognition.md`, `docs/modules/kyutai.md`, `docs/modules/openai-realtime-compatible.md`, `tests-e2e/fixtures/README.md`, `AGENTS.md` (project map)

## Steps

1. **Resolution.** In `modules/base.py`, replace `reconcile_audio_format` with `resolve_module_audio_format` (per dimension: the source's value when supported, else the module default; an explicit rate must be supported) and add the two instance properties. Add `parse_sample_rate_overrides` and use it in `openai-realtime-compatible`.
2. **Converter.** Write `format_converter.py`: carry incomplete frames, decode `mulaw`, mix channels, resample with a `soxr.ResampleStream` (imported only when the rates differ), encode. Wrap it in `ConvertedAudioSource`, which builds a fresh converter on each `start()`.
3. **Config.** Make the source rate and channel count optional, add `engine.module.sample_rate` (stripped from the module's own fields), drop `on_unsupported_format` with a warning for old configs.
4. **Engine.** Resolve both formats at construction (reading the file header for a file source), expose `module_audio_format`, wrap the source in the converter when the formats differ, give the gate and the module the module format. Remove the `soxr` availability check.
5. **Dependency.** Move `soxr` to core and relock.
6. **Tests.** Fast tier per the spec's "Testing"; the live `test_engine_modules_converted` and the keyless server scenario.
7. **Specs, docs, examples.** Update every page that described reconciliation, the "must be 24000" rule, or "files are not resampled".

## Verification

- `uv run ruff check .`, `uv run ruff format --check .`, `uv run pyright`: clean.
- `uv run pytest tests/`: green, including `tests/test_docs.py` (module page fields vs spec, links, anchors) and `tests/test_project_map.py`.
- `zsh -ic 'uv run pytest tests-e2e'`: green. `test_engine_modules_converted` ran live for `deepgram-v1`, `openai-realtime-compatible`, `openai-transcriptions-compatible` and `mlx-audio`; `kyutai` skips outside its own environment.
