# Module-agnostic e2e scenarios on the `fake` module

**Status:** Done

Implements the settled behavior in `specs/e2e-testing.md` ("Scope", "Default module (`fake`)", "Assertion Strategy"), `specs/testing.md` (the two halves of `tests-e2e/`), and `specs/asr-to-terminal.md` ("E2E tests"). Delivers: `helpers.default_module(...)` returns the scripted `fake` module, so the direct-engine API, MCP resource/tool, and asr-to-terminal e2e scenarios run keyless and deterministic with exact assertions, while `test_engine_modules.py` stays the only live-provider part of the suite. Depends on [202609171830_asr-module-extras-and-fake.md](202609171830_asr-module-extras-and-fake.md) being `Done`. It deliberately does not move these scenarios into `tests/` — they spawn subprocess servers and play audio in real time, which the fast tier's speed rule forbids.

## Scope

- `tests-e2e/helpers.py` — `SCRIPT_BLUE`, `SCRIPT_BLUE_VALIDATE` script constants; `default_module(script)` returns `("fake", {"utterances": script})` without `require_api_key`; update docstrings that call the default a live backend.
- `tests-e2e/test_engine_api.py` — pass the matching script; exact assertions.
- `tests-e2e/test_mcp_resource.py` — same.
- `tests-e2e/test_mcp_tools.py` — same.
- `tests-e2e/test_asr_to_terminal.py` — same.
- `tests-e2e/test_engine_modules.py` — unchanged (verify it does not call `default_module`).
- `AGENTS.md` — "Live/e2e tests": only the per-module cases need keys; the rest run anywhere.

## Steps

1. **Measure fixture durations.** `soundfile.info(...)` on `FIXTURE_BLUE`, `FIXTURE_BLUE_VALIDATE`, `FIXTURE_BLUE_WAV_16000`. Put each script's speech window inside the file with the final at least ~100 ms (one chunk) before the file's end, e.g. `[{"text": "the sky is blue", "start_s": 0.2, "end_s": duration - 0.2}]`. Hardcode the resulting numbers as constants with a comment giving the measured duration (don't compute at import time — keeps helpers side-effect free). `FIXTURE_BLUE_WAV_16000` reuses `SCRIPT_BLUE` if its duration allows; otherwise add `SCRIPT_BLUE_WAV_16000`.

2. **`default_module(script)`.** Signature `default_module(script: list[dict]) -> tuple[str, dict]`; returns `("fake", {"utterances": script})`. Remove its `require_api_key` call (the fake has no `api_key_env`, so it would be a no-op anyway). Keep `require_api_key` for `MODULES`.

3. **Rewrite each default-module scenario** to call `default_module(<script for the fixture it plays>)`, and tighten assertions to exact values:
   - `test_engine_api.py`: `test_listen_pipeline` → `segment.transcript == "the sky is blue"`; `test_dictation_pipeline` → the closed trigger-word segment's transcript equals the script text minus the trigger word, as the `Segmenter` produces it (check the exact trigger-exclusion output in `tests/test_segmenter.py` and match it).
   - `test_mcp_resource.py`: final utterance transcript `== "the sky is blue"`; aggregated segment transcript exact.
   - `test_mcp_tools.py`: `listen` trigger-word / timeout results exact; streaming test asserts the exact progress messages sequence produced by the committed finals.
   - `test_asr_to_terminal.py`: `typer.line == "the sky is blue"` (or the exact form the bridge types, incl. spacing), committed lines exact.
   - Where a scenario sequences two utterances through one `ScriptableAudioSource` (dictation), remember the fake's clock restarts only on `start()`: script both utterances on one timeline covering the silence + both `play()` calls, or play once and script both utterances within it — whichever keeps the test readable.
   - Drop now-unused `normalize_transcript` imports in those files (it stays for `test_engine_modules.py`).

4. **Docs.** AGENTS.md "Live/e2e tests": state that only `test_engine_modules.py` needs provider keys (and the provider extras), everything else in `tests-e2e/` runs without credentials; `zsh -ic` is only needed for the per-module cases.

5. **Specs bookkeeping.** Flip statuses (see Verification). Update `specs/asr-to-terminal.md`'s e2e assertion table if the exact typed-text assertions differ from its "contains" wording.

## Verification

- `uv run ruff check .`, `uv run ruff format --check .`, `uv run pyright`, `uv run pytest tests/` — all green.
- **Without credentials** (plain non-interactive shell): `uv run pytest tests-e2e` → every default-module scenario **passes** (none skipped); only the `test_engine_modules.py` cases skip.
- **With credentials:** `zsh -ic 'uv run pytest tests-e2e'` → everything passes, including both live `deepgram_v*` conformance cases.
- Run the default-module scenarios three times in a row to confirm they're stable (no timing flakes from script windows near the end of the audio).

Then mark this plan `Done` (here and in [_index.md](_index.md)) and flip to `Implemented` (file + [specs/_index.md](../specs/_index.md)): `e2e-testing.md`, `asr-to-terminal.md`, and `testing.md`.
