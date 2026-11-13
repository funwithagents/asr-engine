# OpenAI Realtime-compatible module

**Status:** Done

Implements [specs/modules/openai-realtime-compatible.md](../specs/modules/openai-realtime-compatible.md): an `openai_realtime_compatible` ASR module that speaks the OpenAI Realtime API's transcription protocol (GA) over a raw WebSocket, in a new `openai-realtime-compatible` extra. It delivers the module, its fast-tier tests behind a scripted fake connection, the live probe script, a live e2e conformance row (ungated and VAD), the CI secret mapping, an example config and the doc touch-ups. It deliberately leaves out everything the spec defers: engine-side resampling, per-instance sample rates, `vllm_realtime` and a shared WebSocket driver, logprobs/diarization, and live probes of compatible servers other than hosted OpenAI.

## Scope

- `scripts/probe_openai_realtime.py` — live probe (already run; its results are in the spec's "Measured")
- `src/asr_engine/modules/openai_realtime_compatible/__init__.py` — new package; imports nothing
- `src/asr_engine/modules/openai_realtime_compatible/module.py` — `OpenAIRealtimeCompatibleModule`
- `src/asr_engine/modules/__init__.py` — registry entry (`extra="openai-realtime-compatible"`)
- `pyproject.toml` / `uv.lock` — the extra, added to `all` and to the `dev` group
- `tests/modules/test_openai_realtime_compatible.py` — functional tests against a scripted fake connection
- `tests-e2e/helpers.py` — `OPENAI_API_KEY_ENV` and a `MODULES` row
- `examples/config/openai_realtime_compatible.json` + `tests/examples/test_configs.py` — example config with the VAD gate on
- `.github/workflows/ci.yml` — map the `OPENAI_API_KEY` secret into the live tier
- `specs/modules/openai-realtime-compatible.md` — status → `Implemented`
- `specs/asr-module-interface.md` — registry snippet, extras table, naming rule for protocol modules
- `specs/ci.md`, `specs/e2e-testing.md`, `specs/vad.md`, `specs/configuration.md`, `specs/project.md` — module rows and lists
- `specs/_index.md`, `plans/_index.md` — new rows
- `README.md`, `examples/README.md`, `AGENTS.md` — module table, install line, config section, project map

## Steps

1. **Extra and registry.** Add `openai-realtime-compatible = ["websockets>=14"]`, extend `all` and `dev`, run `uv lock`. Register the `LazyModule`.
2. **Config validation.** Parse every field of the spec's table in `__init__`, raising `ValueError` naming the field for each rule. Require a key when `url`'s host is `api.openai.com`.
3. **Connection seam.** Keyword-only `connect` argument, same shape as `mistral_realtime`'s; the default wraps `websockets.connect(url, additional_headers=headers)`.
4. **Session.** Connect, wait for `session.created` then (after sending `session.update`) `session.updated`, both under the 10 s handshake timeout; an `error` instead raises with the server's message. Then mark connected and run the sender (queue → append, marker → `finalize()`) and receiver until stop or failure.
5. **Item tracking.** An ordered dict `item_id → {text, done}` registered on first `committed` or `delta`. Deltas append and emit an interim for the head item. `completed`/`failed` mark the item done. Then the head is drained: each done head emits its final and is popped, and a new head with text emits its interim. All under one emit lock.
6. **Commit-or-clear.** Track `server_turn_open` (`speech_started` → true, `committed` → false) and `uncommitted_bytes` (append → +, commit/clear → 0). `finalize()`: with turn detection on, commit if the turn is open, else clear. With it off, commit at ≥ 100 ms, clear below it, and do nothing on an empty buffer.
7. **Ending a session.** On stop: commit-or-clear, then wait until no open items remain or `stop_timeout_s`, then flush the open items as finals in order and close. On connection loss: flush, report disconnected, back off 1/2/4/8 s while draining the queue (markers dropped), with the 401/403/404 hints.
8. **Fast tests.** Cover every bullet of the spec's "Fast tier" list, plus registry resolution and the extra's install hint.
9. **E2E row, example config and CI.** Add the `MODULES` row (24 kHz WAV, `silence_s` 3.0), the example config and its test update, and the CI secret. Run `zsh -ic 'uv run pytest tests-e2e -k openai_realtime_compatible'`.
10. **Docs and statuses.** Update the specs and docs in Scope. Set the spec to `Implemented` and this plan to `Done`, in both files and both indexes.

## Verification

- `uv run ruff check .`, `uv run ruff format --check .`, `uv run pyright` clean
- `uv run pytest tests/` green, including the new test file, `tests/examples/test_configs.py` and `tests/test_project_map.py`
- `zsh -ic 'uv run pytest tests-e2e -k openai_realtime_compatible'` green against hosted OpenAI (ungated and VAD)
- CI on the pull request: static gate, fast tier on Linux/macOS/Windows, live tier on Linux (the OpenAI cases skip there until the `OPENAI_API_KEY` repository secret exists, which is an expected skip per `specs/ci.md`)
