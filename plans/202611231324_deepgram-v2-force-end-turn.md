# Deepgram v2: end a gated turn with ForceEndTurn

**Status:** Done

Implements the updated behavior in `specs/modules/deepgram.md` ("VAD gating"). When the VAD gate closes on an open Flux turn, `finalize()` sends Flux's `ForceEndTurn` control message instead of a burst of `eot_timeout_ms` + 0.5 s of silence. The turn tracking, the late-turn path and the recommended 1500 ms hangover are unchanged.

## Scope

- `pyproject.toml`, `uv.lock` — `deepgram-sdk>=7.9,<8` (7.9 adds `send_force_end_turn()`)
- `src/asr_engine/modules/deepgram/v2.py` — `_flush_open_turn()` sends `ForceEndTurn`; the silence burst, `_FLUSH_MARGIN_S` and `_flush_silence` go; a `ListenV2Warning` is logged at debug level; the keepalive docstring no longer names SDK 6
- `tests/modules/test_deepgram_v2.py` — the gated-finalize tests assert on the control messages that reach the socket, not on silence; one test runs `send_force_end_turn()` through the SDK's real `AsyncV2SocketClient`
- `scripts/probe_deepgram_flux_silence.py` — `--force-end-turn` / `--force-again` modes (the measurements in the spec)
- `specs/modules/deepgram.md`, `specs/vad.md`, `specs/e2e-testing.md` — the updated design and measurements
- `docs/modules/deepgram.md` — what the module does when the gate closes on an open turn

## Steps

1. Raise the pin to `>=7.9,<8` and relock.
2. In `v2.py`, replace the `send_media(self._flush_silence)` in `_flush_open_turn()` with `send_force_end_turn()`; drop the burst sizing in `start()` and the `_silence` import.
3. In `on_message`, log a `ListenV2Warning` (code and description) at debug level; other non-`TurnInfo` messages stay ignored.
4. Rework the gated tests: an open turn gets one `ForceEndTurn` and no silence; no open turn gets nothing; a turn reported after the marker gets one as soon as it appears, once per gap; each gated utterance left open gets its own. Add a fast-tier test that sends it through the SDK's real socket client over a recording websocket and checks the JSON.
5. Update the user doc page.

## Verification

- `uv run ruff check .`, `uv run ruff format --check .`, `uv run pyright`, `uv run pytest tests/`
- Live: `zsh -ic 'uv run pytest tests-e2e -k deepgram'`, in particular `deepgram-v2-marker_ends_turn`, where the gate closes before Flux decides
- Then set this plan `Done` and `specs/modules/deepgram.md` back to `Implemented`
