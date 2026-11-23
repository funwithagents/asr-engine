# Gated finals from turn.end (mistral-realtime) and split finals in the live VAD case

**Status:** Done

Implements `specs/modules/mistral.md` ("VAD gating") and the relaxed live-tier rule of `specs/vad.md` ("Live tier") and `specs/e2e-testing.md` ("Behind the VAD gate"). It fixes the intermittent live-tier failures where a gated utterance came out as two finals (`"The sky is"`, `"blue."`): the module's local flush timer closed the utterance before the server had sent its last word. A gated final now comes from the server's own `turn.end`. No configuration field is added: the silence length is a module constant.

## Scope

- `src/asr_engine/modules/mistral/realtime.py` — `_FLUSH_SILENCE_S` (2.0 s) replaces `_FLUSH_MARGIN_S`; `start()` cuts that silence into ordinary chunks; `finalize()` sends them and arms nothing; the `flush` timer and its re-arming on each delta are removed
- `tests/modules/test_mistral_realtime.py` — the gated tests: the silence is 2.0 s in chunks with no `input_audio.end`; the final waits for `turn.end` and takes the tail that arrived after the marker; the silence timer is the fallback; nothing accumulated emits nothing
- `tests-e2e/test_engine_modules.py` — `test_engine_modules_vad` checks the joined finals of the first utterance and no longer requires exactly one final
- `specs/modules/mistral.md`, `specs/vad.md`, `specs/e2e-testing.md` — the spec changes above

## Steps

1. Replace the burst of `streaming_delay_ms` + 200 ms with `_FLUSH_SILENCE_S` of silence, cut at `audio_format.chunk_bytes` (one `input_audio.append` may not exceed 256 KiB; 3.5 s at 44.1 kHz in one message closed the connection).
2. Remove the `flush` timer: `finalize()` only sends the silence. `turn.end` closes the utterance through the existing `turn` timer, and the existing `silence` timer is the fallback.
3. Rewrite the gated fast tests and run them against the old module to see them fail.
4. In the live VAD case, wait for `"the sky is blue"` in the joined finals of the first utterance and drop the single-final assertion.

## Verification

- Fast tier: the new gated tests fail on the old module and pass on the new one; the whole `tests/` passes.
- `ruff check`, `ruff format --check`, `pyright`.
- Live: `test_engine_modules_vad[mistral-realtime]` repeated, and the full `tests-e2e` once with all keys.

Mark this plan `Done` (here and in [_index.md](_index.md)) only once all pass.
