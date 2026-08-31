# Utterance / Segment model — segmentation moves into the engine

**Status:** Done

Implements the settled design in [specs/engine.md](../specs/engine.md), plus the
`Updated` specs [architecture.md](../specs/architecture.md),
[mcp-server.md](../specs/mcp-server.md), [configuration.md](../specs/configuration.md),
[asr-module-interface.md](../specs/asr-module-interface.md),
[demo-client.md](../specs/demo-client.md), and
[asr-to-terminal.md](../specs/asr-to-terminal.md).

Renames `ASRResult` → `SpeechUtterance`, introduces `SpeechSegment` + a
`Segmenter`, moves all end-of-utterance logic out of the client-side
`EndOfUtteranceDetector` and into `ASREngine`, exposes two resources
(`asr://utterance`, `asr://segment`), and reduces `AsrToTerminal` to a thin
consumer of `asr://segment`. Deletes `end_of_utterance_detector.py`.

**Notable behaviour change (flagged for review):** `asr-to-terminal` no longer
chooses its segment mode / trigger words / timeouts — those move to the server's
`engine` config block, and its `--mode` / `--trigger-words` /
`--end-of-speech-timeout` / `--initial-silence-timeout` CLI flags are removed.

## Scope

- `src/asr_mcp/modules/base.py` — rename `ASRResult` → `SpeechUtterance`,
  `ResultCallback` → `UtteranceCallback`.
- `src/asr_mcp/modules/deepgram_v1.py`, `deepgram_v2.py` — update to the renamed
  type / callback (param `on_result` → `on_utterance`).
- `src/asr_mcp/segmenter.py` — **new**: `SpeechSegment`, `SegmentCallback`,
  `Segmenter` (utterance / trigger_word / timeout, continuous, timer-driven).
- `src/asr_mcp/engine.py` — `on_speech_utterance` / `on_speech_segment`
  callbacks, `set_segment_mode`, `listen`, wire the segmenter.
- `src/asr_mcp/end_of_utterance_detector.py` — **deleted**.
- `src/asr_mcp/speech_utils.py` — unchanged (still `contains_trigger_word`).
- `src/asr_mcp/config.py` — `EngineConfig` gains `segment_mode` / `trigger_words`
  / `initial_silence_timeout_s` / `end_of_speech_timeout_s`; `ListenConfig`
  renames `end_of_utterance_mode` → `segment_mode`; validation for both.
- `src/asr_mcp/server.py` — two resources (`asr://utterance`, `asr://segment`),
  wire both engine callbacks, `set_segment_mode` from `engine` config at startup,
  `listen` tool delegates to `engine.listen`.
- `src/asr_mcp/resource_client.py` — `AsrResourceClient` gains a `resource_uri`
  arg (default `asr://utterance`).
- `src/asr_mcp/asr_resource_client.py` — subscribe to `asr://utterance`.
- `src/asr_mcp/asr_to_terminal.py` — consume `asr://segment`; drop the session
  loop, detector, and mode/trigger/timeout CLI flags.
- Tests: `tests/test_segmenter.py` (**new**), `tests/test_engine.py`,
  `tests/test_server.py`, `tests/test_config.py`, `tests/test_asr_to_terminal.py`,
  `tests/test_client.py`, `tests/modules/test_base.py`, and delete
  `tests/test_end_of_utterance_detector.py`. `tests/test_speech_utils.py` stays.
- `tests-e2e/` — update fixtures/callers for the renamed types and the
  `asr://segment` consumption in `test_asr_to_terminal.py`.

## Steps

1. **Rename the utterance type.** In `modules/base.py`, rename `ASRResult` →
   `SpeechUtterance` and `ResultCallback` → `UtteranceCallback`; update both
   deepgram modules (and their `on_result` param → `on_utterance`) and
   `tests/modules/test_base.py`. Grep for `ASRResult` / `ResultCallback` across
   `src`, `tests`, `tests-e2e` and update every reference.

2. **Build the `Segmenter`.** New `segmenter.py` with `SpeechSegment`,
   `SegmentCallback`, and `Segmenter(mode, trigger_words,
   initial_silence_timeout_s, end_of_speech_timeout_s, emit)`:
   - `start()` — begin a segment; in `timeout` mode start the initial-silence timer.
   - `on_utterance(u)` — per [engine.md](../specs/engine.md) "Segment modes":
     emit an interim segment on every event (committed finals joined + current
     interim); in `utterance` mode close on each final; in `trigger_word` mode
     close (excluding the trigger utterance) when `contains_trigger_word` hits;
     in `timeout` mode reset the end-of-speech timer on every event and append
     finals. Closing sets `is_final=True` + the matching `end_reason`, then resets
     and (in `timeout` mode) restarts the initial-silence timer.
   - `stop()` — cancel timers.
   Reuse the timer approach from the deleted `EndOfUtteranceDetector` but make it
   continuous (successive segments) and driven by the `emit` callback rather than
   a one-shot `wait()`.

3. **Rework the engine.** In `engine.py`: constructor takes
   `on_speech_utterance` / `on_speech_segment` (default no-op, public settable);
   hold a `Segmenter` (default `utterance` mode). Add `set_segment_mode(mode, *,
   trigger_words=None, initial_silence_timeout_s=None, end_of_speech_timeout_s=None)`
   that rebuilds the segmenter (keeping unspecified params), validates the mode,
   and wires `emit` → `self.on_speech_segment`. In the result handler, fire
   `on_speech_utterance(u)` then `segmenter.on_utterance(u)`. `start()` also calls
   `segmenter.start()`; `stop()` also calls `segmenter.stop()`. Add
   `async listen(*, mode, trigger_words, initial_silence_timeout_s,
   end_of_speech_timeout_s, on_update=None) -> SpeechSegment` per the spec
   (save/restore mode+callback, resolve a future on the first final segment,
   try/finally stop).

4. **Delete** `end_of_utterance_detector.py` and
   `tests/test_end_of_utterance_detector.py`. In the same step, swap them for
   `segmenter.py` / `tests/test_segmenter.py` in `engine.md`'s frontmatter (and
   drop the transitional NOTE), and update the AGENTS.md project-map row
   (`end_of_utterance_detector.py` → `segmenter.py`). Keep
   `tests/test_project_map.py` green.

5. **Config.** In `config.py`: add the four `EngineConfig` fields with defaults
   (`segment_mode="utterance"`, default trigger words, `10.0`, `5.0`) and parse +
   validate them (`segment_mode` ∈ {utterance, trigger_word, timeout}). Rename
   `ListenConfig.end_of_utterance_mode` → `segment_mode` (validate ∈
   {trigger_word, timeout}); update `load_config` accordingly. Update
   `tests/test_config.py`.

6. **Server.** In `server.py`: replace the single `asr://result` with
   `asr://utterance` (transcript/is_final/confidence/timestamp, fed by
   `on_speech_utterance`) and add `asr://segment`
   (transcript/is_final/end_reason/timestamp, fed by `on_speech_segment`), each
   with its own subscriber set and update fan-out. At startup call
   `engine.set_segment_mode(...)` from the `engine` config. Rewrite the `listen`
   tool to call `engine.listen(...)` with the `listen` config, wrapping it with
   the sound-feedback cues and mapping `on_update` → `report_progress` only when
   `len(segment.utterances)` grows. Update `tests/test_server.py`.

7. **Clients.** `resource_client.py`: add `resource_uri: str = "asr://utterance"`.
   `asr_resource_client.py`: subscribe to `asr://utterance` (log lines
   unchanged). `asr_to_terminal.py`: construct `AsrResourceClient(server_url,
   self._on_segment, resource_uri="asr://segment")`; on each update diff-type
   `transcript` and, when `is_final`, send Enter + reset `_pending`; delete the
   session loop / detector; trim the CLI to `--server` / `--display-server`.
   Update `tests/test_asr_to_terminal.py` and `tests/test_client.py`.

8. **E2E.** Update `tests-e2e/` for the renamed `SpeechUtterance` and, in
   `test_asr_to_terminal.py`, for `asr://segment` consumption (set the server's
   `engine.segment_mode` to `trigger_word` in the fixture). No live run required
   for the gate, but keep them compiling and type-clean.

9. **Statuses.** Flip the six `Updated` specs and the new `engine.md` to
   `Implemented` in each spec file and in [specs/_index.md](../specs/_index.md);
   mark this plan `Done` here and in [plans/_index.md](_index.md).

## Verification

- New `tests/test_segmenter.py` drives `Segmenter` through all three modes:
  interim growth, multi-final accumulation, trigger-word close (trigger utterance
  excluded), and both timeout paths (initial-silence and end-of-speech) using a
  controllable clock / short timeouts — asserting the sequence of emitted
  `SpeechSegment`s (transcript, is_final, end_reason, utterances).
- `tests/test_engine.py` covers `set_segment_mode` switching, both callbacks
  firing, and `engine.listen` returning the first closed segment (and rejecting a
  running engine).
- `tests/test_server.py` covers both resource payloads and the `listen` tool
  mapping to `{transcript, end_reason}` with progress only on committed finals.
- `tests/test_asr_to_terminal.py` covers progressive typing from segment updates
  and Enter on a closed segment.
- Full gate: `uv run ruff check .`, `uv run ruff format .`, `uv run pyright`,
  `uv run pytest tests/` all pass. `tests-e2e/` stays type-clean; run it against
  the live service only when validating end-to-end.
