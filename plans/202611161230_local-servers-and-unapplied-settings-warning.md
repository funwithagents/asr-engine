# Local servers: recipes, a check script, and a warning for settings a server did not apply

**Status:** Done

Implements "Settings the server did not apply" in `specs/modules/openai-realtime-compatible.md` (Decisions), and records the local-server runs behind "Measured against local servers" in that spec and in `specs/modules/openai-transcriptions-compatible.md`. Delivers the warning, the script that produced the measurements, and the user page built from them. It leaves out support for the older session layout, and any run against vLLM, LiteLLM, Dynamo or LocalAI.

## Scope

- `src/asr_engine/modules/openai_realtime_compatible/module.py` — `_unapplied_settings()` and `_warn_unapplied()`; `_expect()` returns the event it waited for
- `tests/modules/test_openai_realtime_compatible.py` — the warning cases
- `scripts/check_local_server.py` — runs a fixture through the engine against a local server, for both OpenAI-compatible modules
- `docs/local-speech-recognition.md` — new page: in process or a local server, what to pick, one recipe per server run
- `README.md`, `docs/index.md`, `docs/choosing-a-module.md`, `docs/modules/openai-realtime-compatible.md`, `docs/modules/openai-transcriptions-compatible.md` — links to the page; the warning and the lost first words in the two troubleshooting lists
- `specs/modules/openai-realtime-compatible.md`, `specs/modules/openai-transcriptions-compatible.md`, `specs/project.md`, `AGENTS.md` — the decision, the measurements, the page lists

## Steps

1. Run both modules against the mlx-audio server, whisper.cpp and speaches, and record what each does.
2. Spec the warning (`Implemented` → `Updated`): compare turn detection, `model` and `language` with what `session.updated` reports, in either layout; warn, do not fail.
3. Implement it and its fast-tier cases: a server ignoring the settings (one warning naming each, still connected); a server echoing them, reporting part of them, or nothing (no warning); a server without the turn detection asked for.
4. Write `scripts/check_local_server.py` on `ScriptableAudioSource`, so the audio starts once the module is connected, and re-run every recipe through it.
5. Write the docs page from the runs, link it, and add the measurements to the two specs (`Updated` → `Implemented`).

## Verification

- `uv run pytest tests/modules/test_openai_realtime_compatible.py`: the new cases.
- `uv run ruff check .`, `uv run ruff format --check .`, `uv run pyright`, `uv run pytest tests/`.
- Live, by hand: `scripts/check_local_server.py` against each server of the docs page (six runs, all `OK`), and against speaches realtime, where the warning names `language`, and `turn_detection` with `--turn-detection false`.
- `zsh -ic 'uv run pytest tests-e2e -k openai'`: hosted OpenAI still passes, with no warning.
