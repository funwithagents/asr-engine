---
code:
  - src/asr_engine/segment_timing.py
  - src/asr_engine/engine.py
  - src/asr_engine/segmenter.py
  - src/asr_engine/vad/gate.py
  - src/asr_engine/modules/base.py
  - pyproject.toml
tests:
  - tests/test_segment_timing.py
  - tests/vad/test_gate.py
  - tests/conftest.py
---

# Segment timing

**Status:** Implemented

## Purpose

An application that reacts to speech wants to know how long the person waited and where that time went. The engine is the only place that knows when the person stopped speaking, when something decided they had finished, and when the final transcript was produced. Before this spec `SpeechSegment` and `SpeechUtterance` carried no timing.

The engine now reports these moments for every final segment (and the start of speech as soon as an interim arrives), in two forms filled from the same stamps:

- **Plain data**: a `timing` object on every `SpeechSegment` and `SpeechUtterance`, complete on a final one, for a caller with no tracing setup.
- **An OpenTelemetry span** per final segment, current while `on_speech_segment` is awaited. An application that traces its own work gets what it does in the callback placed under the span with no code of its own, so the span is the root of the trace of that exchange.

The engine measures; it does not display, export or aggregate. Nothing here changes how speech is recognized or segmented, and there is no configuration. Timing is in process only: the MCP resources and tools ([mcp-server.md](mcp-server.md), [tools.md](tools.md)) are unchanged.

## Core concepts / Decided

### The four moments

| Moment | Field of `timing` | Meaning |
|---|---|---|
| Start of speech | `speech_started_at` | the start of the first frame the VAD gate scored as speech, in the speech the segment transcribes |
| End of speech | `speech_ended_at` | the end of the last such frame that silence followed |
| End decided | `end_decided_at`, `end_decided_by` | when something decided the person had finished, and what: the gate closing (`vad_gate`) or the ASR module's own end of turn (`module`) |
| Finalized | `finalized_at` | when the final segment was produced |

In order: start of speech ≤ end of speech ≤ end decided ≤ finalized.

`end_decided_at` separates two things a latency view shows apart: **waiting for the decision** (`end_decided − speech_ended`), which is configuration (`min_silence_duration_ms`, a backend's end-of-turn threshold), and **the recognizer's work** after it (`finalized − end_decided`).

**A stamp that cannot be taken is `None`, never guessed.** The two speech stamps come from the VAD gate ([vad.md](vad.md)): they are `None` with no `engine.vad`, once the gate has failed open, and when the gate detected no speech before the final. `end_decided_at` needs no gate.

### The data

```python
@dataclass(frozen=True)
class UtteranceTiming:
    speech_started_at: datetime | None
    speech_ended_at: datetime | None
    end_decided_at: datetime | None
    end_decided_by: Literal["vad_gate", "module"] | None
    emitted_at: datetime


@dataclass(frozen=True)
class SegmentTiming:
    speech_started_at: datetime | None
    speech_ended_at: datetime | None
    end_decided_at: datetime | None
    end_decided_by: Literal["vad_gate", "module"] | None
    emitted_at: datetime
    finalized_at: datetime | None


@dataclass
class SpeechUtterance:
    ...
    timing: UtteranceTiming | None = None


@dataclass
class SpeechSegment:
    ...
    timing: SegmentTiming | None = None
```

- **One sub-object, not loose fields**, so the two result types stay small. `tts-engine` does the same (`result.timing`). Both types are frozen and exported from `asr_engine`.
- Datetimes are timezone-aware UTC, at microsecond precision.
- **`emitted_at`** is when the engine received the utterance from the module, or produced this update of the segment. It is always set.
- **An interim has the fields that make sense for it**: `speech_started_at` and `emitted_at`. Nothing has ended while the person is speaking, so `speech_ended_at`, `end_decided_at`, `end_decided_by` and `finalized_at` are `None`. `emitted_at − speech_started_at` of the first interim is the recognizer's time to first partial.
- **A final** utterance always has `end_decided_at` and `end_decided_by`. A final segment always has `finalized_at`, equal to its `emitted_at`.
- **`timing` says nothing about finality**: read `is_final`. Every utterance and segment the engine delivers has a `timing`; the field defaults to `None` only because a module, or a test, builds these objects without one.
- **The engine stamps, not the module.** A module builds `SpeechUtterance(transcript, is_final, confidence)` as before; the engine hands its consumers and the `Segmenter` a copy with `timing` set. The module interface is unchanged, so a third-party module gets timing for free.
- `GateSpeechTiming` (`segment_timing.py`) is internal: the raw nanosecond stamps the gate hands the engine for one final, from which the engine builds the `UtteranceTiming`.

### Where the speech stamps are taken: the gate

`VadGate` scores every frame, so it knows the first and the last frame it scored as speech.

- **The last speech frame, not the gate closing.** The gate closes `min_silence_duration_ms` later, and a module with its own end-of-turn detection can finalize before that, because the hangover is forwarded live.
- **The onset, not its confirmation, and not the pre-roll.** The gate opens `min_speech_duration_ms` after the onset and forwards `speech_pad_ms` of audio from before it; the start stamp is the start of the onset frame.
- **Derived from audio time.** A chunk is about 100 ms long and its frames are scored in one burst, so a clock read at scoring would be wrong by up to a chunk. The gate stamps each chunk when it receives it, and places a frame's end at that stamp minus the duration of the samples received after the frame. With a resampled analysis copy the position is still counted in pipeline samples.
- **Stamps never run backwards.** When chunks arrive in a burst (a busy event loop), several are stamped at nearly the same instant and the subtraction alone could put a later frame before an earlier one. Each frame's end is at least the previous frame's.
- **The stamps are late by the capture path**: the device buffer, the chunking, the queues, the format converter. They are taken where the gate receives audio. See "Open questions".

### Which speech a final transcribes

The gate and the module are decoupled: nothing in a module's final says which audio it covers. The gate keeps the **runs** it was open for, each with its onset, its latest speech frame and its closing time, and the engine asks it once per final utterance (`VadGate.take_speech_timing()`, which returns a `GateSpeechTiming`):

- **The speech of a final is what the gate detected since the previous final.** It starts with the first of those runs.
- **It ends with the last speech that silence followed.** If the person is speaking again when the final arrives (a late final, or a pause shorter than the hangover), the gate's latest speech frame belongs to the next turn. The end is then the end of the stretch before that pause; a single silent frame counts as a pause.
- **A run with no pause yet is left for the next final.** A backend that commits text mid-speech (`deepgram-v1`'s `is_final`) gets the start and no end for that final, and the final that follows the pause gets both.
- **A final with no new speech is given the latest run again.** The module only ever receives gated audio, so a second final (a backend that splits one stretch of speech into several) transcribes the same run.
- At most 32 unclaimed runs are kept, for a module that emits no final for a long time.

**An interim** claims nothing. The engine reads the start of the first unclaimed run (`VadGate.peek_speech_start()`), which is the start the next final will be given; with no unclaimed run, the latest run's.

### What decided the end

Inferred by the engine when the final arrives, with no report from the module:

- the gate **had closed** on that speech: `vad_gate`, at the time the gate closed (the reception of the chunk holding the deciding frame);
- otherwise (the gate is still open, or there is no gate): `module`, at the final's arrival.

**A module that beats the gate.** A backend's own end of turn can arrive while the gate is still counting silence, before `min_silence_duration_ms` has run. The final then has `end_decided_by = module` at its arrival, and `speech_ended_at` is the gate's last speech frame: silence has followed it (one scored silent frame is enough) even though the gate has not closed. `end_decided − speech_ended` is then the backend's endpointing delay.

A module's own end of turn that lands just after the gate closed is attributed to the gate: the gate closed first, so the wait was the gate's. Telling the two apart would need each module to report it; see "Open questions".

### Segments of several utterances

In the `trigger_word` and `timeout` modes a segment holds several final utterances ([engine.md](engine.md)):

- `speech_started_at` is the first utterance's that has one; `speech_ended_at` the last utterance's.
- `end_decided_at` and `end_decided_by` describe **the end of the last utterance**. The segmenter's own wait (the silence timer) then shows as `finalized − end_decided`, and `end_reason` says why the segment closed.
- In `trigger_word` mode the trigger utterance is not part of the segment's text, but the segment's speech ends with it.
- A segment closed with no utterance (`initial_silence_timeout`) has `emitted_at` and `finalized_at` only.
- An **open** segment starts with the first speech among its committed utterances and the interim that updated it.

**What starts the silence timer.** In `timeout` mode the `end_of_speech_timeout_s` timer is restarted by **every utterance the segmenter receives, interim or final**, when it arrives. It does not run from the end of speech, which the segmenter never sees. For a segment it closes, `finalized − end_decided` is therefore the recognizer finalizing the last utterance plus the whole timer when that final is the last event received, and a little more than the timer when the backend sent something after it. The span carries the timer's length (`asr_engine.segmentation.end_of_speech_timeout_s`) so a reader can show it as configured time, as with the gate's `min_silence_duration_ms`.

**Pauses inside a segment read as speech**: the segment's speech runs from its first onset to its last end.

### The span

One span per **final** segment; an interim segment opens none.

- **Start time:** `speech_ended_at` when known, else `finalized_at`.
- **Current while `on_speech_segment` is awaited** for that segment, and ended when the callback returns. A span the callback opens is its child.
- **Events carry the times they name**, not the time the span is built: `end_decided` (when known) and `finalized`.
- **Parent.** In the always-on stream and during a dictation the span is always a **root**, a new trace, whatever was current at `start()` or `start_dictation()`, and it carries no span link. The task that delivers segments is created inside `start()` and inherits the context current there, which would otherwise make every segment a child of a span that ended long ago. And an application that calls `start_dictation()` from inside its reaction to the previous segment must not see the dictated segment as a follow-up of that reaction. For `listen()` the span is a child of the span current where `listen()` is awaited; it has ended when `listen()` returns the segment.
- **A callback that raises** ends the span with the exception recorded and an error status, and the exception propagates as before. A cancellation ends it with no error.

### The names are a contract

A trace reader finds a segment by its tracer and span name, and reads its steps from the event names. These names are public and stable: renaming one is a breaking change. They are constants in [segment_timing.py](../src/asr_engine/segment_timing.py).

| Kind | Name | Meaning |
|---|---|---|
| Tracer | `asr_engine` | the instrumentation scope of every span the package opens |
| Span | `asr_engine.segment` | one final segment, from the end of its speech to the end of the callback |
| Event | `end_decided` | `end_decided_at`; absent when unknown |
| Event | `finalized` | `finalized_at` |
| Attribute | `asr_engine.module` | the module's registered type (a renamed type under its current name) |
| Attribute | `asr_engine.end_reason` | the segment's `end_reason` |
| Attribute | `asr_engine.speech_end_source` | `vad` when the span starts at the gate's end-of-speech stamp, `none` when no stamp could be taken |
| Attribute | `asr_engine.end_decided_by` | `vad_gate` or `module`; absent when unknown |
| Attribute | `asr_engine.vad.min_silence_duration_ms` | the gate's hangover, when a VAD is configured |
| Attribute | `asr_engine.speech_started_unix_ns` | `speech_started_at` in nanoseconds since the epoch, when known. An attribute, not an event: it is before the span's start |
| Attribute | `asr_engine.segmentation_mode` | the mode the segment was closed under: `utterance`, `trigger_word` or `timeout` |
| Attribute | `asr_engine.segmentation.end_of_speech_timeout_s` | the silence timer's length in seconds, only when that timer closed the segment (`end_reason` is `end_of_speech_timeout`) |

Attributes carry the package's name as their prefix, like the tracer: a trace holds spans from many libraries, and `asr.` could be another library's or a future OpenTelemetry convention's. Event names need no prefix, since an event only exists inside its span. The same convention as `tts-engine`.

### The clock

Stamps are **wall-clock**, so they compare with the stamps of other packages in the same trace: nanoseconds since the epoch on the span, UTC `datetime`s in the data. A monotonic clock cannot be compared across packages. Every stamp is read through one function, `segment_timing.now_ns()`, which the tests replace with a scripted clock.

### Dependency

`opentelemetry-api` is a base dependency ([project.md](project.md)): the API only, never the SDK. With no SDK installed the span is a no-op object, and the cost is a few clock reads per final segment. An application that wants the spans installs the SDK and an exporter of its choice; the engine has no exporter, no enable flag and no configuration. The SDK is a `dev` dependency, for its in-memory exporter in the tests.

The tracer is obtained per segment (`trace.get_tracer("asr_engine")`), so an SDK installed after the engine was built is picked up.

## Testing

Fast tier, per [testing.md](testing.md), on a scripted wall clock (the `clock` fixture) and the SDK's in-memory exporter (the `exporter` fixture), both in `tests/conftest.py`.

- `tests/vad/test_gate.py`: the gate on the scripted VAD, fed as a real-time source would deliver it. The start and end stamps are the speech's first and last frame, to the nanosecond, whatever the chunk size; an interim reads the start without claiming it; a final in the hangover, speech resumed before the final, a late final after new speech began, several runs before one final, a second final for the same run; a burst keeps the order; a resampled pipeline is right to the frame; no timing without speech, after a failure, after a restart.
- `tests/test_segment_timing.py`: `ASREngine` on the `fake` module. With the scripted VAD: a final segment carries the four moments and exactly one span starts at the end of its speech, with its events and attributes; the span is current in the callback of a final segment and of no interim, whose timing has a start and nothing ended; a final before the gate closes is decided by the module; a gate that failed open gives no end of speech. Without a VAD: the span starts at `finalized_at`. With no tracer provider the stamps are still on the segment. The span's parent in the always-on stream, in a dictation started inside a span, and in `listen()`; the mode and the timer's length on a segment the silence timer closed. The `Segmenter` on stamped utterances for the two aggregating modes and the empty segment.

## Open questions

Deferrals only:

- **Capture latency.** The stamps are late by the capture path. For the engine's own `input-device` source, the input stream's reported latency could be carried as an attribute so an application can correct for it; a custom source is its owner's to account for.
- **A module reporting its own end of turn**, to tell Flux's `EndOfTurn` from the gate's marker when both land close together, and to stamp the decision when the backend took it instead of when its final arrived.
- **Timing in the MCP payloads.** `asr://segment` and the `listen` tool could carry the stamps as ISO strings; spans cannot cross that boundary.
- **A `tracer_provider` argument on `ASREngine`**, the usual way to give a library a provider other than the global one.
