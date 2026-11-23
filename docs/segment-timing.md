# Measure how long the speaker waited

Every final segment says when its speech started and ended, when something decided the speaker had finished, and when the final transcript was produced. Interim results already say when the speech started. The same stamps come in two forms: fields on the segment, and an OpenTelemetry span. Design and guarantees: [specs/segment-timing.md](../specs/segment-timing.md).

## The timing of a segment

Every segment has a `timing` object. It is complete on a final segment:

```python
from asr_engine import SpeechSegment


async def on_segment(segment: SpeechSegment) -> None:
    timing = segment.timing
    if not segment.is_final or timing is None:
        return
    if timing.speech_ended_at is not None:
        waited = timing.finalized_at - timing.speech_ended_at
        print(f"final {waited.total_seconds() * 1000:.0f} ms after the last word")
```

| Field of `segment.timing` | Meaning | On an interim |
|---|---|---|
| `speech_started_at` | the start of the first speech the segment transcribes | set |
| `emitted_at` | when this update of the segment was produced | set |
| `speech_ended_at` | the end of its last speech: the last word, not the moment the engine noticed the silence | `None` |
| `end_decided_at` | when something decided the speaker had finished | `None` |
| `end_decided_by` | what decided: `vad_gate` (the gate closed) or `module` (the backend's own end of turn) | `None` |
| `finalized_at` | when the final segment was produced | `None` |

Times are timezone-aware UTC datetimes. A `SpeechUtterance` has a `timing` too (`UtteranceTiming`), with the same fields except `finalized_at`. Both types are importable from `asr_engine`. Use `is_final`, not the timing, to tell an interim from a final.

While the person is still speaking nothing has ended, so an interim only knows when its speech started and when it was emitted. `emitted_at − speech_started_at` on the first interim is the backend's time to first partial.

**The two speech stamps need the [VAD gate](vad.md).** They are `None` when no `engine.vad` is configured, when the gate has failed, and when it detected no speech. `emitted_at`, `end_decided_at` and `finalized_at` need no gate. A stamp that cannot be taken is `None`, never an estimate.

Useful differences:

| Duration | What it tells you |
|---|---|
| `end_decided_at − speech_ended_at` | the wait for the decision. It is configuration: the gate's `min_silence_duration_ms`, or the backend's end-of-turn setting |
| `finalized_at − end_decided_at` | the recognizer's work after the decision. In the `trigger_word` and `timeout` modes it also holds the segmenter's wait, which `end_reason` explains |
| `finalized_at − speech_ended_at` | what the speaker waited for the transcript |

A segment of several utterances (the [`trigger_word` and `timeout` modes](segmentation.md)) starts with its first speech and ends with its last; `end_decided_at` is about its last utterance. In `timeout` mode the silence timer restarts each time the backend sends an utterance, interim or final, so `finalized_at − end_decided_at` includes `end_of_speech_timeout_s`.

The stamps are taken where the engine receives the audio, so they are late by the capture path: the sound device's buffer and about one 100 ms chunk.

## The span

Each final segment is also one [OpenTelemetry](https://opentelemetry.io/docs/languages/python/) span. `asr-engine` depends on the OpenTelemetry API only, so nothing is recorded or sent until your application installs the SDK and an exporter:

```python
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor

provider = TracerProvider()
provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
trace.set_tracer_provider(provider)
```

The span starts at the end of the speech (at `finalized_at` when that is unknown) and is current while your `on_speech_segment` callback runs for that segment. What the callback does is therefore placed under it, with no identifier to pass:

```python
tracer = trace.get_tracer("my_application")


async def on_segment(segment: SpeechSegment) -> None:
    if segment.is_final:
        with tracer.start_as_current_span("answer the user"):
            ...  # a child of the segment's span: one trace per exchange
```

In the continuous stream and during a dictation the span is the root of a new trace. For `listen()` it is a child of the span current where you await `listen()`. An interim segment has no span.

These names are stable; a trace reader can rely on them.

| Kind | Name | Meaning |
|---|---|---|
| Tracer | `asr_engine` | every span the package opens |
| Span | `asr_engine.segment` | one final segment, from the end of its speech to the end of your callback |
| Event | `end_decided` | `end_decided_at`; absent when unknown |
| Event | `finalized` | `finalized_at` |
| Attribute | `asr_engine.module` | the ASR module's type |
| Attribute | `asr_engine.end_reason` | the segment's `end_reason` |
| Attribute | `asr_engine.speech_end_source` | `vad` when the span starts at the end of speech, `none` when it starts at `finalized_at` |
| Attribute | `asr_engine.end_decided_by` | `vad_gate` or `module` |
| Attribute | `asr_engine.vad.min_silence_duration_ms` | the gate's setting, when a VAD is configured |
| Attribute | `asr_engine.speech_started_unix_ns` | `speech_started_at`, in nanoseconds since the epoch, when known |
| Attribute | `asr_engine.segmentation_mode` | the mode that closed the segment: `utterance`, `trigger_word` or `timeout` |
| Attribute | `asr_engine.segmentation.end_of_speech_timeout_s` | the silence timer's length, when that timer closed the segment |

The MCP server's resources and tools carry no timing: the stamps and the span are for an application that imports the engine.
