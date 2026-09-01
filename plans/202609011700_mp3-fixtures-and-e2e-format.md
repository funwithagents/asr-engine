# MP3 file sources + explicit-format e2e fixtures

**Status:** Done

Follow-up to [202609011400_configurable-audio-format.md](202609011400_configurable-audio-format.md).
Adds MP3 (and any `libsndfile` codec) decoding to file audio sources, widens
`deepgram_v2` to the same format support as `deepgram_v1`, and reworks the e2e
suite to drive every test with an **explicit** `AudioFormat` — defaulting to the
44.1 kHz mono MP3 fixtures, with the 16 kHz WAV reserved for a dedicated
sample-rate-compatibility test.

## Scope

- `pyproject.toml` — add `soundfile` to runtime `dependencies`.
- `src/asr_engine/audio.py` — decode file sources via `soundfile`/`libsndfile`
  (`_iter_audio_chunks` replacing the `wave`-based `_iter_wav_chunks`); validate
  rate/channels from `sf.info` (no resample); stream `sf.blocks` as s16 and
  transcode per `AudioFormat`. Drops the `wave` import.
- `src/asr_engine/modules/deepgram_v2.py` — declare the same capabilities as
  `deepgram_v1` (`{8000,16000,24000,44100,48000}` / `{1}` / `{linear16, mulaw}`);
  verified live that Flux accepts 44.1 kHz.
- `tests-e2e/helpers.py` — centralized `FIXTURE_*` paths + `FORMAT_MP3_44100` /
  `FORMAT_WAV_16000`; `build_engine` / `build_file_engine` / `start_mcp_server`
  gained an `audio_format` (default 44.1 kHz mp3) written explicitly into the
  engine `audio` config.
- `tests-e2e/test_engine_modules.py` — default fixtures → 44.1 kHz mp3 (validate
  fixture for the trigger-word test); new `test_sample_rate_compat_16000_wav`
  (parametrized per module) on the 16 kHz WAV.
- `tests-e2e/test_mcp_resource.py` / `test_mcp_tools.py` / `test_asr_to_terminal.py`
  — repointed to the mp3 fixtures (the MCP-tools trigger_word test gained
  `trailing_silence_s` so Deepgram finalizes the trigger utterance).
- `tests-e2e/fixtures/README.md` — documents the naming convention + file table.
- Specs (editorial, stay Implemented): `project.md` (add `soundfile` dep),
  `deepgram-module.md` (v2 support), `configuration.md` +
  `asr-module-interface.md` (file sources are WAV/MP3/… via libsndfile),
  `e2e-testing.md` (fixture set, explicit-format helpers, extra test).

## Verification

- `uv run ruff check . && uv run ruff format . && uv run pyright` — clean.
- `uv run pytest tests/` — 196 pass (soundfile decode path covered in
  `test_audio.py`).
- `zsh -ic 'uv run pytest tests-e2e'` — 15 pass, including Flux at 44.1 kHz and
  the 16 kHz WAV compat test.
