# VAD gate and end-of-speech finalization

**Status:** Done

Implements [specs/vad.md](../specs/vad.md): the optional `VadGate` speech gate, the `VADModule` registry with a Silero backend (ONNX, no torch), the in-band `END_OF_SPEECH` marker, and `finalize()` in all five bundled ASR modules. Leaves out everything in the spec's Open questions (VAD as a segmentation signal, voice-activity callback, markers from custom sources, `max_speech_duration_s`, Smart Turn, other VAD backends) and any batch ASR module.

**Precondition:** `specs/vad.md` is `Stable` (design review passed). The design was approved for implementation on 2026-11-12, and the spec went `Draft → Implemented` with this plan.

## Outcome

Built as planned, with these results and deviations:

- **Step 1, Voxtral:** the tail text does **not** arrive without further audio, so `finalize()` is the silence-first variant: one burst of `streaming_delay_ms` + 200 ms, then a wait of `streaming_delay_ms + final_margin_ms` that each delta restarts (a fixed deadline was too tight against the measured 0.46–0.56 s). The existing probe already had the stop-sending mode (`--trailing-silence 0 --idle N`); it gained `--burst-silence`.
- **Step 1, Flux:** `EndOfTurn` 0.17 s and 1.16 s after speech on real silence (the 1500 ms recommendation stands); no `EndOfTurn` when audio just stops; an unpaced silence burst ends the turn in about 0.5 s. The close-and-reconnect fallback was not needed.
- **Step 4:** output is flushed once per incoming chunk instead of being held to exactly `CHUNK_MS`, so the gate adds no latency.
- **Step 5, changed after review:** the model is **not** committed or shipped in the wheel. `load()` downloads it on first use from upstream tag `v6.2.3` into the user cache and checks a pinned SHA-256; `engine.vad.model_path` points at a local copy instead. So there is no `silero_vad.onnx`, no `NOTICE.md` and no wheel include, contrary to the Scope and Verification lists below, and the real-model fast tests download the model once per machine and skip when offline. `onnxruntime` carries an Intel-macOS marker so the environment still syncs there. `uv lock` added four packages and changed no existing pin.
- **Step 9:** a turn first reported after the marker is flushed when its transcript arrives.
- **Tests:** the shared VAD test doubles live in `tests/vad/scripted.py` (importable from `tests/test_engine.py`), not in a `conftest.py`.
- **Example configs, changed after review:** moved from the repo root to `examples/config/`, one file per ASR module named after it (`deepgram_v1.json` is new; the old `config.example.json` is `deepgram_v2.json`). The three cloud configs enable the gate with their recommended setting; `kyutai.json` has `"vad": null`. `tests/examples/test_configs.py` keeps them valid.
- **Added after review:** `ASREngine.vad_state` (gate state and sent/captured counters) and a VAD row in the Gradio demo showing the configured gate, live voice activity and the audio sent to the module ([gradio-demo.md](../specs/gradio-demo.md)).
- **Live tier:** `test_engine_modules_vad` passed on `deepgram_v1`, both `deepgram_v2` cases, `mistral_realtime` and (locally, `ASR_ENGINE_E2E_KYUTAI=1`) `kyutai`.

## Scope

New:

- `src/asr_engine/vad/__init__.py` — `LazyVAD`, `REGISTRY`, `resolve_vad_class`
- `src/asr_engine/vad/base.py` — `VADModule` ABC with import-time attribute checks
- `src/asr_engine/vad/gate.py` — `VadGate`: analysis conversion, reframing, state machine, marker, fail-open
- `src/asr_engine/vad/silero/__init__.py`, `module.py`, `silero_vad.onnx`, `NOTICE.md` — `SileroVAD` on `onnxruntime`, the bundled model, its provenance and licence
- `tests/vad/__init__.py`, `test_base.py`, `test_gate.py`, `test_silero.py`, and a scripted `VADModule` shared through `tests/vad/conftest.py`
- `scripts/probe_deepgram_flux_silence.py` — the Flux measurement of step 1

Changed:

- `src/asr_engine/audio.py` — `EndOfSpeech` / `END_OF_SPEECH`, `mulaw_to_linear16`
- `src/asr_engine/modules/base.py` — `VAD_GATING`, default `finalize()`, `audio_queue` type widened
- `src/asr_engine/modules/deepgram/v1.py`, `v2.py`, `mistral/realtime.py`, `kyutai/module.py`, `fake/module.py` — `VAD_GATING = "supported"`, marker handling, `finalize()`
- `src/asr_engine/config.py` — `VadConfig`, `ASREngineConfig.vad`, parsing and validation
- `src/asr_engine/engine.py` — VAD resolution and checks at construction, gate wrap at start, `status()["vad"]`
- `scripts/probe_mistral_realtime.py` — a mode that stops sending audio after the speech (step 1)
- `pyproject.toml`, `uv.lock` — `silero` extra, in `all` and `dev`; the `.onnx` in the wheel
- `tests/test_audio.py`, `tests/test_config.py`, `tests/test_engine.py`, `tests/modules/test_*.py` — per the spec's Testing section
- `tests-e2e/test_engine_modules.py`, `tests-e2e/helpers.py` — the `test_engine_modules_vad` scenario and its `onnxruntime` skip
- Specs: `vad.md` (frontmatter, status), `asr-module-interface.md`, `engine.md`, `configuration.md`, `architecture.md`, `project.md`, `e2e-testing.md`, `ci.md`, `audio-source.md`, `modules/deepgram.md`, `modules/mistral.md`, `modules/kyutai.md`, `modules/fake.md`, `_index.md`
- `AGENTS.md` (top-level layout row for `vad/`, `tests/vad/`), `README.md` (VAD section, extras table), `config.example.json` siblings only if an example with VAD is wanted (one `vad` block in `config.mistral.example.json`, commented in the README rather than enabled)

## Steps

1. **Measure the two unverified backend behaviours** (live, before any module code; record the results in the module specs as the Mistral probe table does).
   - *Voxtral:* extend `scripts/probe_mistral_realtime.py` with a mode that sends a fixture then **stops sending** (no silence, no `input_audio.end`). Does the tail text arrive, and how long after the last chunk? If yes within `streaming_delay_ms + final_margin_ms`, `finalize()` is the local deadline. If not, `finalize()` first sends silence covering the streaming delay.
   - *Flux:* `scripts/probe_deepgram_flux_silence.py`, two measurements. (a) Send a fixture followed by real-time silence and log when `EndOfTurn` arrives after the speech ends: this sets the hangover the spec recommends for Flux (1500 ms is the working figure; correct it in the spec if the measurement disagrees). (b) Send a fixture, stop, then send a burst of encoded silence and log when `EndOfTurn` arrives. Find the smallest burst that reliably ends the turn and whether it must be paced. If no burst works, `finalize()` is `send_close_stream()` + reconnect on the next chunk.
2. **Marker and module contract.** `audio.py`: `EndOfSpeech`, `END_OF_SPEECH`, `mulaw_to_linear16` (numpy, inverse of the existing encoder). `modules/base.py`: `VAD_GATING` default `"unsupported"`, `finalize()` no-op, widen `start`'s `audio_queue` annotation; update the five modules' annotations. No behaviour change. Tests: μ-law round trip.
3. **VAD interface and registry.** `vad/base.py`, `vad/__init__.py`, modelled on `modules/base.py` and `modules/__init__.py`. Tests: attribute enforcement, unknown type, missing-extra hint, eager registration.
4. **`VadGate`.** In this order, each with its tests on the scripted VAD: state machine at the native rate; ring-buffer pre-roll and hangover; marker emission; output coalescing; μ-law and multi-channel analysis copies; `soxr` streaming resample with the boundary mapping; restart; fail-open.
5. **Silero.** Fetch `silero_vad.onnx` from a pinned upstream tag; write `NOTICE.md` (tag, SHA-256, MIT text). Inspect the model's input/output names and shapes with `onnxruntime` and port upstream's ONNX wrapper state handling (state tensor, per-rate context samples) — read upstream's `utils_vad.py` at that tag rather than relying on memory. Add the `silero` extra to `pyproject.toml` (also in `all` and `dev`), make the build ship the `.onnx` (check with `uv build` and list the wheel), `uv lock`. Read the lock diff for ripples against the `moshi-mlx` pins before continuing. Tests on the real model with the 16 kHz fixture.
6. **Config.** `VadConfig`, parsing, validation, tests.
7. **Engine.** Construction-time resolution, the two `VAD_GATING` errors, the `soxr` check, lazy threaded `load()`, gate wrap, `status()`. Tests with a recording module and with `fake`.
8. **`fake` and `deepgram_v1`.** `fake` skips the marker in its drain loop. `deepgram_v1`: marker branch in `_audio_loop` calling `finalize()` → `conn.send_finalize()`; markers dropped in `_drain_queue_for`. Fast tests on the fake connection.
9. **`deepgram_v2`.** Track whether a turn is open in `on_message` (set on any transcript, cleared on `EndOfTurn`). `finalize()` does nothing when no turn is open, otherwise acts per step 1's result. Fast tests for both paths.
10. **`mistral_realtime`.** `_next_chunk` passes the marker through; `_send_audio` calls `finalize()`, which arms the deadline timer (alongside the `turn` and `silence` timers, same `_arm` mechanism) or first sends silence, per step 1. Check that a marker does not count as audio for idle parking in a way that delays it past the server's 30 s limit. Fast tests.
11. **`kyutai`.** `_feed_loop` on the marker: zero-pad the partial frame, enqueue the flush frames, then a control item the worker echoes to the emit loop, which force-finalizes and resets its utterance state (the `_RECYCLED` branch without the backend reset). `_drop_oldest` already preserves control items; cover it. Fast tests on the scripted backend.
12. **Live tier.** `test_engine_modules_vad` over `MODULES` with Silero; `deepgram_v2` twice (1500 ms and 300 ms hangover). Run every row that has a key or opt-in:
    `zsh -ic 'uv run pytest tests-e2e -k vad'` and `zsh -ic 'ASR_ENGINE_E2E_KYUTAI=1 uv run pytest tests-e2e -k "vad and kyutai"'`. Then the full `tests-e2e` once, to confirm the non-VAD paths are untouched.
13. **Specs and docs**, in the same change as the code they describe: set each touched `Implemented` spec to `Updated` when its code starts changing and back to `Implemented` at the end; `vad.md` gets its new files in the frontmatter and goes `Stable → Implemented`; `mistral.md` drops its "Speech gating" open question and "no VAD stage" wording; `project.md` gets the Intel macOS caveat and the `vad/` layout; `ci.md` gets the new expected-skip rows if any; `AGENTS.md`, `README.md`, both `_index.md` files.

## Verification

- `uv run ruff check . && uv run ruff format --check .`
- `uv run pyright`
- `uv run pytest tests/` — green, still within the speed rule (`--durations=10`: no VAD test above a few hundred ms)
- `uv build` — the wheel contains `asr_engine/vad/silero/silero_vad.onnx`
- CI green on all four fast-tier platforms (this is where the `onnxruntime` and `soxr` wheels are proven on Linux arm64 and Windows)
- Live tier per step 12; a skip outside [ci.md](../specs/ci.md)'s expected-skips table is a regression to read

Mark this plan `Done` (here and in [_index.md](_index.md)) only once all of the above pass. If a live row could not be run (no key, no weights), say which in the `_index.md` row instead of claiming it.
