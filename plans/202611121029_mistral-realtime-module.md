# Voxtral realtime module

**Status:** Done

Implements [specs/modules/mistral.md](../specs/modules/mistral.md): a `mistral_realtime` ASR module over Mistral's Voxtral 3 realtime WebSocket API, in a new `mistral` extra. It delivers the module, its fast-tier tests behind a scripted fake connection, a live e2e conformance row, the CI secret mapping and the doc touch-ups. It deliberately leaves out everything the spec defers: diarization, `eager_end` speculation, session recycling, a VAD `finalize()`, self-hosting and batch Voxtral.

## Scope

- `src/asr_engine/modules/mistral/__init__.py` — new package; imports nothing, so the registry stays lazy
- `src/asr_engine/modules/mistral/realtime.py` — `MistralRealtimeModule`: config validation, session handshake, sender/receiver, utterance assembly and timers, stop flush, idle parking, reconnection
- `src/asr_engine/modules/__init__.py` — `mistral_realtime` registry entry (`extra="mistral"`)
- `pyproject.toml` / `uv.lock` — `mistral = ["websockets>=14"]`, added to `all` and to the `dev` group
- `tests/modules/test_mistral_realtime.py` — functional tests against a scripted fake connection
- `tests-e2e/helpers.py` — `MISTRAL_API_KEY_ENV` and a `MODULES` row
- `.github/workflows/ci.yml` — map the `MISTRAL_API_KEY` secret into the live tier
- `specs/modules/mistral.md` — frontmatter lists the new code/tests; status → `Implemented`
- `specs/asr-module-interface.md` — registry snippet and extras table
- `specs/ci.md` — live-tier job row, secrets paragraph, expected-skips row
- `specs/configuration.md` — a Voxtral example config
- `specs/project.md` — `mistral/` in the package layout list, `mistral` in the `dev` extras list
- `specs/e2e-testing.md` — the new `MODULES` row, if the spec enumerates rows
- `README.md` — available-modules table, install lines and a Voxtral section
- `AGENTS.md` — project map's backend package list

## Steps

1. **Extra and registry.** Add the `mistral` extra, extend `all` and `dev`, run `uv lock`. Register `mistral_realtime` as a `LazyModule` naming `extra="mistral"`.
2. **Config validation.** In `MistralRealtimeModule.__init__`, parse every field in the spec's table with its default and raise `ValueError` naming the field for each rule in "Validation in `__init__`". Keep the parsed values on private attributes and build the `session.update` payload from them.
3. **Connection seam.** Keyword-only `connect` argument: `(url, headers) → async context manager → connection` exposing `send(str)` and `recv()`. The default wraps `websockets.connect(url, additional_headers=headers)`.
4. **Session.** Connect, wait (10 s) for `session.created`, send `session.update`, mark connected, then run the sender (queue → `input_audio.append`, with a pending first chunk after parking) and receiver (deltas, `turn.end`, `transcription.done`, `error`) until the sender reports stop/idle or either side fails.
5. **Utterance assembly.** An emit lock serializes all emission. Deltas append and emit an interim, then (re)arm the silence timer. `turn.end` arms the margin timer. A timer detaches itself from its slot before taking the lock, so re-arming never cancels a final mid-delivery. Finalizing cancels both timers, emits a non-empty accumulator and resets it.
6. **Ending a session.** On stop/idle: send `input_audio.end`, let the receiver run until `transcription.done` or `stop_timeout_s`, then finalize and close.
7. **Run loop and lifecycle.** `start()` loops sessions: after an idle park, wait for the next chunk (or stop) and reconnect with it pending; on any failure, finalize, report disconnected, log (401/403 hints), back off 1/2/4/8 s while draining the queue, stop-aware. `stop()` sets the stop event and awaits the loop's exit (bounded), and the loop's exit reports disconnected.
8. **Fast tests.** A `FakeConnection` (records sent JSON, serves pushed events, can fail or auto-answer `input_audio.end` with scripted trailing events) and a connector that hands out a scripted sequence of connections or exceptions. Cover every bullet of the spec's "Fast tier" list, plus registry resolution.
9. **E2E row and CI.** Add the `MODULES` row (44.1 kHz MP3 audio, `silence_s` 3.0) and map the secret in `ci.yml`. Run `zsh -ic 'uv run pytest tests-e2e -k mistral_realtime'`.
10. **Docs and statuses.** Update the specs, README and AGENTS.md listed in Scope. Set the mistral spec's frontmatter to the new code and tests and its status to `Implemented`, and this plan to `Done`, in both files and both indexes.

## Verification

- `uv run ruff check .`, `uv run ruff format --check .`, `uv run pyright` clean
- `uv run pytest tests/` green, including the new `tests/modules/test_mistral_realtime.py` and `tests/test_project_map.py`
- `zsh -ic 'uv run pytest tests-e2e -k mistral_realtime'` green against the live API
- CI on the pull request: static gate, fast tier on Linux/macOS/Windows, live tier on Linux (the Voxtral case skips there until the `MISTRAL_API_KEY` repository secret exists — an expected skip, per `specs/ci.md`)
