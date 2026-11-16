# Running the engine

An `ASREngine` delivers its results through two async callbacks and can run in three ways: continuously, for one segment (`listen`), or with a dictation session on top of a running engine.

## Utterances and segments

The engine exposes two independent callbacks. Pass them to the constructor, or assign them later as `engine.on_speech_utterance` and `engine.on_speech_segment`. You can use either or both:

```python
from asr_engine import ASREngine, SpeechSegment, SpeechUtterance


async def on_utterance(utterance: SpeechUtterance) -> None:
    print("utterance", utterance.transcript, utterance.is_final)


async def on_segment(segment: SpeechSegment) -> None:
    print("segment", segment.transcript, segment.is_final, segment.end_reason)


engine = ASREngine(
    config,
    on_speech_utterance=on_utterance,
    on_speech_segment=on_segment,
)
```

A `SpeechUtterance` is one result from the ASR module. The utterance callback receives every non-empty result, interim and final:

| Field | Type | Meaning |
|---|---|---|
| `transcript` | `str` | The text recognized so far for this utterance |
| `is_final` | `bool` | `False` for an interim result that may still change, `True` for the final one |
| `confidence` | `float \| None` | The backend's confidence, or `None` when it gives none |

A `SpeechSegment` is the engine's aggregation of utterances under the active [segmentation mode](segmentation.md). The segment callback receives every change while a segment grows, then one final value when it closes:

| Field | Type | Meaning |
|---|---|---|
| `transcript` | `str` | The text of the segment so far |
| `is_final` | `bool` | `True` on the value that closes the segment |
| `end_reason` | `str \| None` | Why the segment closed (`utterance`, `trigger_word`, `initial_silence_timeout`, `end_of_speech_timeout`); `None` while it grows |
| `utterances` | `list[SpeechUtterance]` | The final utterances committed to the segment. Interim utterances contribute to `transcript` but are not stored here |

## Continuous recognition

`start()` runs the pipeline continuously in `utterance` mode: one segment per final utterance, delivered through the callbacks until `stop()`.

```python
await engine.start()
try:
    await run_your_application()
finally:
    await engine.stop()
```

Constructing an engine starts nothing. The caller owns its lifecycle.

## Listen for one segment

`listen()` starts a stopped engine in the requested segmentation mode, waits for one segment to close, stops the engine, and returns that segment:

```python
segment = await engine.listen(mode="trigger_word")
print(segment.transcript, segment.end_reason)
```

Omit `mode`, or pass `None`, to use `listen_default_segmentation_mode`. An optional `on_update` callback receives the segment's intermediate values:

```python
segment = await engine.listen(mode="timeout", on_update=on_segment)
```

`listen()` requires the engine to be stopped. With sound feedback enabled, the engine plays its start and stop cues around the session.

## Dictation on a running engine

A dictation session temporarily changes how a running engine aggregates utterances. Starting it does not block and does not replace the engine's callbacks:

```python
await engine.start()
await engine.start_dictation(
    segmentation_mode="trigger_word",
    end_on_final_segment=False,
)

# Aggregated updates continue through on_speech_segment.

await engine.stop_dictation()
await engine.stop()
```

Omit `segmentation_mode`, or pass `None`, to use `dictation_default_segmentation_mode`. With `end_on_final_segment=True`, the default, the first closed segment ends the dictation and the engine returns to `utterance` mode. Otherwise call `stop_dictation()` yourself. The engine keeps running in both cases.

## The three ways compared

| Operation | Engine must be | Blocks until a segment closes | Stops the engine afterward |
|---|---|---|---|
| `start()` | Stopped | No | No |
| `listen()` | Stopped | Yes | Yes |
| `start_dictation()` | Running | No | No |

## Other operations

| API | Purpose |
|---|---|
| `status()` | The engine's `running` state, the backend's `connected` state, and the configured `vad` type (`None` without one) |
| `vad_state` | With a VAD configured: whether speech is reaching the module right now (`None` without one) |
| `dictating` | Whether a dictation session is active |
| `segmentation_mode` | The mode active right now |
| `audio_format` | The audio format reconciled with the module ([audio-input.md](audio-input.md#audio-format-negotiation)) |
| `await set_segmentation_params(...)` | Change the trigger words or timeouts and rebuild the segmenter |
| `set_listen_default_segmentation_mode(mode)` | Change the default for future listens |
| `set_dictation_default_segmentation_mode(mode)` | Change the default for future dictations |

The full contract, including what happens to an open segment on each transition, is in the [engine specification](../specs/engine.md).
