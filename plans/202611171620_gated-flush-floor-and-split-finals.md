# Gated flush floor (mistral-realtime) and split finals in the live VAD case

**Status:** Done

Implements `specs/modules/mistral.md` ("VAD gating", step 3: the floor) and the relaxed live-tier rule of `specs/vad.md` ("Live tier") and `specs/e2e-testing.md` ("Behind the VAD gate"). It fixes the intermittent live-tier failures where a gated utterance came out as two finals (`"The sky is"`, `"blue."`). It does not add a configuration field: the round-trip allowance is a module constant.

## Scope

- `src/asr_engine/modules/mistral/realtime.py` — `_FLUSH_ROUND_TRIP_S`; `finalize()` sets the floor; `_arm_flush()` arms or restarts the flush timer for `max(quiet window, time left to the floor)`, from `finalize()` and from each delta
- `tests/modules/test_mistral_realtime.py` — a `round_trip` fixture keeps the floor short in the gated tests; a regression test: tail text later than the quiet window but before the floor joins the final
- `tests-e2e/test_engine_modules.py` — `test_engine_modules_vad` checks the joined finals of the first utterance and no longer requires exactly one final
- `specs/modules/mistral.md`, `specs/vad.md`, `specs/e2e-testing.md` — the spec changes above

## Steps

1. In the mistral module, compute the floor in `finalize()` after sending the burst: marker time + `streaming_delay_ms` + `_FLUSH_MARGIN_S` + `final_margin_ms` + `_FLUSH_ROUND_TRIP_S` (0.5 s), on the loop clock.
2. Route both the marker's arming and the per-delta restart through `_arm_flush()`, which never schedules the final before the floor.
3. Add the fast regression test and run it against the old code to see it fail (it emits `"The sky is"` early).
4. In the live VAD case, wait for `"the sky is blue"` in the joined finals of the first utterance and drop the single-final assertion.

## Verification

- Fast tier: the new `test_tail_text_slower_than_the_quiet_window_joins_the_final` fails on the old module and passes on the new one; the whole `tests/` passes.
- `ruff check`, `ruff format --check`, `pyright`.
- Live: `test_engine_modules_vad[mistral-realtime]` repeated, and the full `tests-e2e` once with all keys.

Mark this plan `Done` (here and in [_index.md](_index.md)) only once all pass.
