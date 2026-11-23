# Segment timing: speech stamps and a span per final segment

**Status:** Done

Implements [specs/segment-timing.md](../specs/segment-timing.md): the VAD gate stamps the start and the end of speech, the engine attaches them (and when the end was decided) to each final utterance, a final `SpeechSegment` carries them with `finalized_at` in its `timing`, and one OpenTelemetry span per final segment is current during `on_speech_segment`. It leaves out the capture latency of the input device, any change to the ASR module interface, and timing in the MCP payloads.

## Scope

- `pyproject.toml`, `uv.lock` — `opentelemetry-api>=1.20,<2` in the base dependencies, `opentelemetry-sdk` in the `dev` group
- `src/asr_engine/segment_timing.py` — new: the span, event and attribute names, the clock (`now_ns`, `to_datetime`, `to_ns`), the public `UtteranceTiming` and `SegmentTiming`, the internal `GateSpeechTiming`, `segment_span`
- `src/asr_engine/vad/gate.py` — stamps each chunk at reception, places each frame in wall-clock time, keeps the speech runs, `take_speech_timing()`
- `src/asr_engine/modules/base.py` — `SpeechUtterance.timing`
- `src/asr_engine/segmenter.py` — `SpeechSegment.timing`; final segments are stamped from the utterances they cover
- `src/asr_engine/__init__.py` — exports `UtteranceTiming` and `SegmentTiming`
- `src/asr_engine/engine.py` — stamps each final utterance; opens the span around the callback of a final segment; a `listen()` passes its caller's context as the parent
- `tests/conftest.py` — the `clock` and `exporter` fixtures
- `tests/vad/test_gate.py`, `tests/test_segment_timing.py` — the tests listed in the spec
- `tests/test_engine.py` — one test compared a final utterance by identity with what the module emitted
- `specs/` — the new spec; `engine.md`, `vad.md`, `asr-module-interface.md`, `project.md` and `_index.md` updated
- `docs/segment-timing.md` — new user page; `docs/index.md`, `docs/running-the-engine.md` and `docs/vad.md` point to it
- `AGENTS.md` — the new module in the project map

## Steps

1. Add the dependency and lock it.
2. Write `segment_timing.py`: the names, the clock helpers, the three timing types, and `segment_span` (explicit start time and event timestamps, an empty context for a root, attach and detach around the block, error status on an exception but not on a cancellation).
3. In `VadGate`: read the clock when a chunk is received; after each frame is cut, compute its end from the samples received after it and clamp it to the previous frame's; record the onset frame when a run of speech frames begins, a `_SpeechRun` when the gate opens, its latest speech frame, the end of the stretch before each silent frame, and its closing time. `take_speech_timing()` applies the rules of "Which speech a final transcribes". A failure and a restart forget the runs.
4. Add `timing` to both dataclasses, defaulting to `None`, so every existing construction keeps working; export the two public types from `asr_engine`.
5. In the engine, `_stamp_final` builds the stamped copy of a final utterance before it reaches `on_speech_utterance` and the segmenter. `_emit_segment` wraps the callback of a final segment in `segment_span`.
6. In the segmenter, build every final segment through one helper that takes the utterances it covers (the trigger utterance included) and stamps `finalized_at`.
7. Tests, then the specs, the docs and the project map.

## Verification

- `uv run pytest tests/vad/test_gate.py tests/test_segment_timing.py` — the "Done when" list of the request: one span per final and none per interim; the span is current in the callback; with a gate the span starts at the known end of speech and `speech_ended_at` says the same; the events are present and ordered; with no VAD or a failed gate `speech_ended_at` is `None` and the span starts at `finalized_at`; with no tracer provider the existing tests pass unchanged.
- `uv run ruff check .`, `uv run ruff format --check .`, `uv run pyright`, `uv run pytest tests/` all pass.
