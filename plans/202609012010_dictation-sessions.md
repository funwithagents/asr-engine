# Dictation sessions

**Status:** Done

Implements the dictation feature settled in `specs/engine.md` ("Dictation",
`_start_with_segmentation_mode`, `_set_segmentation_mode`), `specs/configuration.md`
(`auto_start_dictation`, `dictation_default_segmentation_mode`, no always-on
`segmentation_mode`), `specs/tools.md`, `specs/mcp-server.md`,
`specs/asr-to-terminal.md`, and `specs/gradio-demo.md`. Adds a non-blocking
dictation session (the long-running counterpart to `listen`) that switches the
always-on segmentation mode on a running engine and reverts to `utterance` when
it ends, plus the config/tool/UI surface around it. Deliberately leaves the
`_analysis.md` items beyond the mode-swap correctness fix (items 1–2, minimal)
out of scope.

## Scope

- `src/asr_engine/config.py` — drop `ASREngineConfig.segmentation_mode`; add
  `auto_start_dictation: bool = False` and
  `dictation_default_segmentation_mode: str = "trigger_word"`; widen
  `listen_default` validation to all three modes; validate the new default and
  that `auto_start_dictation` implies `auto_start`; parse the new fields.
- `src/asr_engine/engine.py` — `_start_with_segmentation_mode(mode)` primitive; `start()` = `_start_with_segmentation_mode(
  "utterance")`; private async `_set_segmentation_mode` (validate-first,
  stop-old-segmenter-before-swap); dictation state + `asyncio.Lock`;
  `start_dictation` / `stop_dictation`; `dictating` property;
  `set_dictation_default_segmentation_mode` / `set_listen_default_segmentation_mode`;
  `listen` via `_start_with_segmentation_mode(mode)` + revert on `stop()`; `stop()` clears dictation and
  resets stored mode to `utterance`; first-final auto-end watcher in the segment
  path.
- `src/asr_engine/tools.py` — `start_dictation` / `stop_dictation` /
  `is_dictation_running` / the two default-mode setters (pass-throughs).
- `src/asr_engine/server.py` — register the five new MCP tools; honor
  `auto_start_dictation` right after the startup `start()`.
- `examples/gradio_demo/controller.py` — remove `set_segmentation_mode`; add
  `start_dictation` / `stop_dictation`; `"dictating"` phase; `can_dictate` state;
  at-rest mode = `"utterance"`; drop `config.segmentation_mode` reads.
- `examples/gradio_demo/app.py` — dictation row (mode + end-on-final + Start/Stop
  dictation) and enablement wiring.
- `tests/test_config.py` — new fields, widened/added validation, `auto_start`
  dependency.
- `tests/test_engine.py` — dictation lifecycle, auto-end, mode revert, private
  setter validate-first / stop-old-segmenter, `dictating` getter.
- `tests/test_tools.py` — the new `AsrTools` methods against a fake engine.
- `tests/test_server.py` — new tools registered; `auto_start_dictation` wiring.
- `tests-e2e/helpers.py` — drop the `segmentation_mode` kwarg from `build_engine`
  (field gone); refresh the `start_mcp_server` docstring's `engine_overrides`
  examples (`segmentation_mode` → `auto_start_dictation` /
  `dictation_default_segmentation_mode`).
- `tests-e2e/test_engine_modules.py` — drop the now-default
  `segmentation_mode="utterance"` call; **add the per-module dictation conformance
  tests** (trigger_word, timeout, end-on-final self-end), driven through
  `engine.start_dictation` / `stop_dictation` with a `ScriptableAudioSource`,
  alongside the existing `listen` tests.
- `tests-e2e/test_mcp_tools.py` — add single-provider dictation **tool** tests
  (control plane + `is_dictation_running` + guard errors).
- `tests-e2e/test_mcp_resource.py` — add a single-provider `auto_start_dictation`
  aggregation test (subscribe `asr://segment`).
- `tests-e2e/test_asr_to_terminal.py` (+ any committed `*.config.json`) — migrate
  `segmentation_mode` server config to `auto_start_dictation=true` +
  `dictation_default_segmentation_mode`.

## Steps

1. **Config.** In `config.py`: delete the `segmentation_mode` field and its
   parsing/validation; add `auto_start_dictation` and
   `dictation_default_segmentation_mode` to `ASREngineConfig` and `_parse_engine`.
   Validate `dictation_default_segmentation_mode` and `listen_default_segmentation_mode`
   against all three modes (drop `_LISTEN_MODES`; use `_SEGMENT_MODES`). Raise a
   clear `ValueError` when `auto_start_dictation` is true and `auto_start` is
   false. Update `asr_engine/__init__` exports if needed (no new dataclass).
2. **Engine core.** Add `_start_with_segmentation_mode(self, segmentation_mode)` that builds the
   `Segmenter` for that mode (raising on unknown before any capture starts) and
   starts capture/module/segmenter; make `start()` call `_start_with_segmentation_mode("utterance")`.
   Make `_set_segmentation_mode` private + async: build candidate segmenter first
   (validate), `await self._segmenter.stop()`, swap, `await new.start()`. Store
   `dictation_default_segmentation_mode` / `listen_default_segmentation_mode` and
   add the two `set_*_default_segmentation_mode` setters (validate mode; don't
   change current mode).
3. **Dictation.** Add `self._dictating`, `self._dictation_end_on_final`, and an
   `asyncio.Lock` serializing mode/dictation transitions. Implement
   `start_dictation(end_on_final_segment=True, segmentation_mode=None)` (guards:
   not running, already dictating; resolve mode; `_set_segmentation_mode`; set
   state) and `stop_dictation()` (guard: not dictating; clear state;
   `_set_segmentation_mode("utterance")`). Add the `dictating` read-only property.
   Make `stop()` clear dictation state and reset the stored mode to `"utterance"`.
4. **Auto-end watcher.** In the engine's segment emission path (`_emit_segment`),
   after awaiting the public `on_speech_segment`, if dictating with
   `end_on_final_segment` and `segment.is_final`, schedule the dictation end as a
   separate task (so the current emit/segmenter operation unwinds first) that
   ends the dictation and reverts to `utterance`.
5. **listen.** Rewrite `listen` to install its wrapped callback, then
   `await self._start_with_segmentation_mode(mode)` (mode resolved against `listen_default`, `utterance`
   allowed), await the future, and in `finally` `await stop()` (which resets the
   mode) and restore the callback — no `_set_segmentation_mode` calls in `listen`.
6. **Tools.** Add `start_dictation` / `stop_dictation` / `is_dictation_running` /
   `set_dictation_default_segmentation_mode` / `set_listen_default_segmentation_mode`
   to `AsrTools`, returning the documented dicts (`is_dictation_running` composes
   `engine.dictating` + `engine.segmentation_mode`).
7. **Server.** Register the five tools as thin MCP adapters. In `run_server`,
   after `if auto_start: await engine.start()`, add
   `if engine_config.auto_start_dictation: await engine.start_dictation(end_on_final_segment=False)`.
8. **Gradio demo.** In `controller.py`, replace `set_segmentation_mode` with
   `start_dictation` / `stop_dictation` (marshaled onto the loop thread), add the
   `"dictating"` phase, `can_dictate`, and make the at-rest mode `"utterance"`; in
   `app.py`, build the dictation row and wire enablement to the new state.
9. **e2e migration + dictation coverage.** Grep the repo for `segmentation_mode`
   and migrate every server config (committed `*.config.json`,
   `helpers.build_engine`, `test_asr_to_terminal.py`) to the new fields. Then add
   dictation e2e that mirrors the `listen` layering:

   **Per-module, through the engine** (`test_engine_modules.py`, parametrized over
   `MODULES`, using a `ScriptableAudioSource` so audio plays *after* dictation is
   armed — no race, same pattern as `test_engine_streams`):
   - **`test_dictation_trigger_word`** — `engine.start()` →
     `start_dictation(end_on_final_segment=False, segmentation_mode="trigger_word")`
     (assert `engine.dictating` and `engine.segmentation_mode == "trigger_word"`) →
     `source.play(FIXTURE_BLUE_VALIDATE, trailing_silence_s=silence_s)` → wait for a
     collected **final** segment with `end_reason="trigger_word"` excluding
     "validate" → `stop_dictation` (assert `dictating` False, mode back to
     `utterance`).
   - **`test_dictation_timeout`** — same with `segmentation_mode="timeout"`,
     `end_of_speech_timeout_s=2.0`, `FIXTURE_BLUE`; final `end_reason=
     "end_of_speech_timeout"`, transcript "the sky is blue".
   - **`test_dictation_end_on_final_segment`** — `start_dictation(
     end_on_final_segment=True, segmentation_mode="timeout")` → play once →
     `_wait_until(lambda: not engine.dictating)`; assert the mode auto-reverted to
     `utterance` and a final segment was emitted.

   **Module-agnostic, single provider** (`default_provider`):
   - `test_mcp_tools.py`: **`test_dictation_tool_control`** (timing-independent —
     server `auto_start=true`, `auto_start_dictation=false`; `is_dictation_running`
     → `start_dictation(end_on_final_segment=false, segmentation_mode="timeout")`
     returns `{status:"dictating", mode:"timeout", end_on_final_segment:false}` →
     `is_dictation_running` `{dictating:true, segmentation_mode:"timeout"}` →
     `stop_dictation` `{status:"dictation_stopped"}` → `is_dictation_running` back
     to `utterance`) and **`test_dictation_guard_errors`** (double `start_dictation`
     → "Dictation is already in progress."; `start_dictation` with `auto_start=false`
     → "ASR is not running.").
   - `test_mcp_resource.py`: **`test_auto_start_dictation_aggregates_segment`** —
     server with `auto_start_dictation=true`,
     `dictation_default_segmentation_mode="trigger_word"`,
     `trigger_words=["validate"]`, `FIXTURE_BLUE_VALIDATE` (+ `trailing_silence_s`);
     subscribe `asr://segment` and assert a final `trigger_word` segment excluding
     "validate" (dictation is armed before the file plays).
10. **Statuses.** Flip the six specs (`engine`, `configuration`, `tools`,
    `mcp-server`, `asr-to-terminal`, `gradio-demo`) back to `Implemented` in each
    file and in `specs/_index.md`, and mark this plan `Done` in both places, once
    verification passes. (No new `src/asr_engine/` module, so the AGENTS.md
    project map and spec frontmatter are unchanged.)

## Verification

- New/updated fast tests: `tests/test_config.py` (fields + validation, including
  `auto_start_dictation` requires `auto_start`), `tests/test_engine.py`
  (dictation start/stop, `end_on_final_segment` true vs false, mode reverts to
  `utterance` on dictation end and on `listen` end/cancel, `_set_segmentation_mode`
  leaves state untouched on an invalid mode and stops the old segmenter so no
  stale timeout segment emits after a switch, `listen` accepts `utterance`,
  `dictating` getter), `tests/test_tools.py` (the five methods against a fake
  engine, including the `is_dictation_running` shape and the guard errors),
  `tests/test_server.py` (tools registered; `auto_start_dictation` triggers
  `start_dictation` after start).
- Run `uv run ruff check .`, `uv run ruff format .`, `uv run pyright`, and
  `uv run pytest tests/` — all green.
- Opt-in: `zsh -ic 'uv run pytest tests-e2e'` (needs `DEEPGRAM_API_KEY`) — the
  migrated configs plus the new per-module dictation tests in
  `test_engine_modules.py` (one backend: `zsh -ic 'uv run pytest tests-e2e -k
  "deepgram_v1 and dictation"'`) and the module-agnostic dictation tests in
  `test_mcp_tools.py` / `test_mcp_resource.py`. The `end_on_final_segment=True`
  self-end is pinned both here (per module) and deterministically in the fast
  `tests/test_engine.py` via a scripted source.
- Manual: run the gradio demo, Start, then Start-dictation in `trigger_word`
  mode, confirm segments aggregate and Stop-dictation reverts to per-utterance.

Mark this plan `Done` (here and in [_index.md](_index.md)) only once all pass.
