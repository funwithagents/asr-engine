# Engine robustness fixes: config validation, lifecycle, segmenter timeouts

**Status:** Done

Implements three corrections found by the repository audit of 2026-11-16, each specified in the same change: strict validation of configuration loaded from outside ([configuration.md](../specs/configuration.md) "Validation"), one lifecycle transition at a time ([engine.md](../specs/engine.md) "Lifecycle" and "`listen`"), and one event at a time in the `Segmenter` ([engine.md](../specs/engine.md) "`Segmenter`"). Left out: validation of the Deepgram modules' own fields, the callback failure policy across modules, and supervision of the pipeline's background tasks.

## Scope

- `src/asr_engine/config.py` — `from_dict` checks the shape of every block, refuses unknown fields outside `module` and `vad`, requires real booleans, finite positive timeouts, a list of non-empty trigger words, and a valid `server` block; each error names the field's path
- `src/asr_engine/segmenter.py` — refuses a timeout that is not a positive number; handles one utterance or one timeout at a time under a lock; a timer that has fired runs to the end
- `src/asr_engine/engine.py` — one engine lock for `start`, `stop`, dictation transitions and `set_segmentation_params`; `start()` refused on a running engine or during a `listen`; `listen` exclusive; `stop()` ends a `listen` in progress; `set_segmentation_params` stores nothing when a value is invalid
- `tests/test_config.py`, `tests/test_segmenter.py`, `tests/test_engine.py` — functional tests for the above
- `specs/engine.md`, `specs/configuration.md`, `specs/tools.md`, `specs/mcp-server.md` — the behavior above
- `docs/configuration.md`, `docs/running-the-engine.md`, `docs/troubleshooting.md` — the user's view of it

## Steps

1. **Configuration.** Add `_as_object`, `_as_bool` and `_as_seconds` helpers and the `_parse_segmentation`, `_parse_sound_feedback` and `_parse_server` parsers; call them from `ASREngineConfig.from_dict` and `MCPServerConfig.from_dict`. `null` for `segmentation`, `sound_feedback` and `audio_source` means the defaults. `engine.audio` stays let through to `_parse_audio_source`, which refuses it with the message naming `audio_source`.
2. **Segmenter.** Validate the two timeouts in the constructor. Take an `asyncio.Lock` in `on_utterance` and in both timers after their sleep. A timer clears its own task reference when it fires, so nothing cancels it while it delivers its final segment; a timer that finds it was replaced while waiting for the lock returns.
3. **Engine.** Rename the segmentation lock to the engine lock and take it in `_start_with_segmentation_mode` (which refuses a running engine) and `stop`. Claim `_listening` in `listen` before its first `await`; refuse `start()` while it is set. Keep the listen's future on the engine so `stop()` can fail it with `RuntimeError`. Build the candidate `Segmenter` in `set_segmentation_params` before storing the new values.
4. **Tests**, each failing on the previous code: malformed types and misspelled fields; a timeout and an utterance at the same boundary, driven by events rather than by timing; repeated and overlapping `start`, `stop` and `listen`.
5. **Specs and docs** in the same change. `engine.md` and `configuration.md` go `Implemented` → `Updated` → `Implemented` within it.

## Verification

- `uv run ruff check .`, `uv run ruff format --check .`, `uv run pyright`: pass.
- `uv run pytest tests/`: 865 passed (53 new; 50 of them fail on the code before this plan).
- `uv run pytest tests-e2e -k 'not engine_modules'` (the keyless scenarios on the `fake` module): 11 passed. The live per-module cases were not run.
