"""Segment timing and the span of a final segment (specs/segment-timing.md).

Driven through ``ASREngine`` on the ``fake`` module, with the OpenTelemetry
SDK's in-memory exporter and a scripted wall clock. With a VAD, the audio is
fed by hand as a real-time source would deliver it, so the audio at X ms
happened at ``clock.at_ms(X)`` and every stamp is a round number.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from opentelemetry import trace
from opentelemetry.trace import StatusCode

from asr_engine import (
    ASREngine,
    ASREngineConfig,
    AudioSourceConfig,
    ModuleConfig,
    SoundFeedbackConfig,
    SpeechSegment,
    SpeechUtterance,
    VadConfig,
)
from asr_engine.segment_timing import segment_span, to_datetime, to_ns
from asr_engine.segmenter import Segmenter
from tests.vad.scripted import HandFedSource, ScriptedVAD, loud, pcm, quiet, settle

BYTES_PER_MS = 32  # 16 kHz mono linear16

# Open after 30 ms of speech, close after 50 ms of silence, 20 ms of pre-roll:
# the figures of tests/vad/test_gate.py, on the scripted VAD's 10 ms frames.
_VAD = dict(min_speech_duration_ms=30, min_silence_duration_ms=50, speech_pad_ms=20)

# Speech from 200 ms to 300 ms. The gate forwards from 180 ms and closes on the
# frame ending at 350 ms: 170 ms of audio reach the module.
_SPEECH = pcm(quiet(200), loud(100), quiet(300, seed=3))


class Recorder:
    """The engine's callbacks: what was delivered, and the span current then."""

    def __init__(self) -> None:
        self.utterances: list[SpeechUtterance] = []
        self.segments: list[SpeechSegment] = []
        self.span_ids: list[int] = []
        self.final = asyncio.Event()

    async def on_utterance(self, utterance: SpeechUtterance) -> None:
        self.utterances.append(utterance)

    async def on_segment(self, segment: SpeechSegment) -> None:
        self.segments.append(segment)
        self.span_ids.append(trace.get_current_span().get_span_context().span_id)
        if segment.is_final:
            self.final.set()

    @property
    def finals(self) -> list[SpeechSegment]:
        return [s for s in self.segments if s.is_final]


def _vad_engine(
    recorder: Recorder, source: HandFedSource, *, end_s: float, vad_extra=None
) -> ASREngine:
    """``fake`` saying "hello there" over the first *end_s* seconds of the audio
    the gate lets through, behind the scripted VAD."""
    config = ASREngineConfig(
        sound_feedback=SoundFeedbackConfig(enabled=False),
        audio_source=AudioSourceConfig(type="custom"),
        module=ModuleConfig(
            type="fake",
            extra={
                "utterances": [{"text": "hello there", "start_s": 0.0, "end_s": end_s}]
            },
        ),
        vad=VadConfig(type="scripted", extra=vad_extra or {}, **_VAD),
    )
    with patch.dict("asr_engine.vad.REGISTRY", {"scripted": ScriptedVAD}):
        return ASREngine(
            config,
            audio_source=source,
            on_speech_utterance=recorder.on_utterance,
            on_speech_segment=recorder.on_segment,
        )


async def _feed_paced(source: HandFedSource, clock, data: bytes, chunk_ms: int) -> None:
    size = chunk_ms * BYTES_PER_MS
    for i in range(0, len(data), size):
        chunk = data[i : i + size]
        clock.advance_ms(len(chunk) / BYTES_PER_MS)
        source.queue.put_nowait(chunk)
        await settle()


def _events(span) -> list[tuple[str, int]]:
    return [(event.name, event.timestamp) for event in span.events]


# ---------------------------------------------------------------------------
# With a VAD gate
# ---------------------------------------------------------------------------


async def test_a_final_segment_carries_its_speech_and_one_span_starts_at_its_end(
    clock, exporter
):
    recorder, source = Recorder(), HandFedSource()
    engine = _vad_engine(recorder, source, end_s=0.15)

    async def slow_utterance_consumer(utterance: SpeechUtterance) -> None:
        if utterance.is_final:
            clock.advance_ms(3)  # the segment is produced after this returns

    engine.on_speech_utterance = slow_utterance_consumer
    await engine.start()
    await _feed_paced(source, clock, _SPEECH, chunk_ms=100)
    await engine.stop()

    # The final came with the chunk received at 400 ms, the one the gate closed in.
    (segment,) = recorder.finals
    assert segment.transcript == "hello there"
    assert segment.speech_started_at == to_datetime(clock.at_ms(200))
    assert segment.speech_ended_at == to_datetime(clock.at_ms(300))
    assert segment.end_decided_at == to_datetime(clock.at_ms(400))
    assert segment.end_decided_by == "vad_gate"
    assert segment.finalized_at == to_datetime(clock.at_ms(403))

    (span,) = exporter.get_finished_spans()
    assert span.name == "asr_engine.segment"
    assert span.instrumentation_scope.name == "asr_engine"
    assert span.start_time == clock.at_ms(300)  # the last word, not the gate closing
    assert _events(span) == [
        ("end_decided", clock.at_ms(400)),
        ("finalized", clock.at_ms(403)),
    ]
    assert dict(span.attributes) == {
        "asr_engine.module": "fake",
        "asr_engine.end_reason": "utterance",
        "asr_engine.speech_end_source": "vad",
        "asr_engine.end_decided_by": "vad_gate",
        "asr_engine.vad.min_silence_duration_ms": 50,
        "asr_engine.speech_started_unix_ns": clock.at_ms(200),
    }


async def test_the_span_is_current_in_the_callback_of_a_final_segment_only(
    clock, exporter
):
    recorder, source = Recorder(), HandFedSource()
    engine = _vad_engine(recorder, source, end_s=0.15)
    await engine.start()
    await _feed_paced(source, clock, _SPEECH, chunk_ms=100)
    await engine.stop()

    assert [s.is_final for s in recorder.segments] == [False, True]
    (span,) = exporter.get_finished_spans()  # the interim opened none
    assert recorder.span_ids == [0, span.context.span_id]
    # An interim segment carries no timing either.
    interim = recorder.segments[0]
    assert (interim.speech_ended_at, interim.finalized_at) == (None, None)


async def test_a_module_that_finalises_before_the_gate_closes_decided_the_end(
    clock, exporter
):
    """The final arrives 10 ms into the hangover: the speech ended at its last
    frame, and it is the module, not the gate, that decided so."""
    recorder, source = Recorder(), HandFedSource()
    engine = _vad_engine(recorder, source, end_s=0.13)  # 180 ms + 130 ms = 310 ms
    await engine.start()
    await _feed_paced(source, clock, _SPEECH[: 320 * BYTES_PER_MS], chunk_ms=10)
    await engine.stop()

    (segment,) = recorder.finals
    assert segment.speech_ended_at == to_datetime(clock.at_ms(300))
    assert segment.end_decided_at == to_datetime(clock.at_ms(310))
    assert segment.end_decided_by == "module"
    (span,) = exporter.get_finished_spans()
    assert span.start_time == clock.at_ms(300)
    assert span.attributes["asr_engine.end_decided_by"] == "module"


async def test_the_final_utterance_carries_the_same_stamps(clock):
    recorder, source = Recorder(), HandFedSource()
    engine = _vad_engine(recorder, source, end_s=0.15)
    await engine.start()
    await _feed_paced(source, clock, _SPEECH, chunk_ms=100)
    await engine.stop()

    interim, final = recorder.utterances
    assert (interim.speech_ended_at, interim.end_decided_at) == (None, None)
    assert (
        final.speech_started_at,
        final.speech_ended_at,
        final.end_decided_at,
        final.end_decided_by,
    ) == (
        to_datetime(clock.at_ms(200)),
        to_datetime(clock.at_ms(300)),
        to_datetime(clock.at_ms(400)),
        "vad_gate",
    )


async def test_a_gate_that_failed_open_gives_no_end_of_speech(clock, exporter):
    recorder, source = Recorder(), HandFedSource()
    engine = _vad_engine(recorder, source, end_s=0.15, vad_extra={"fail_at_frame": 0})
    await engine.start()
    await _feed_paced(source, clock, _SPEECH, chunk_ms=100)  # all of it is forwarded
    await engine.stop()

    (segment,) = recorder.finals
    assert (segment.speech_started_at, segment.speech_ended_at) == (None, None)
    assert segment.end_decided_by == "module"
    assert segment.finalized_at == to_datetime(clock.at_ms(200))
    (span,) = exporter.get_finished_spans()
    assert span.start_time == clock.at_ms(200)  # no end of speech: finalized_at
    assert span.attributes["asr_engine.speech_end_source"] == "none"
    assert "asr_engine.speech_started_unix_ns" not in span.attributes


# ---------------------------------------------------------------------------
# Without a VAD
# ---------------------------------------------------------------------------

_SCRIPT = [{"text": "hello there", "start_s": 0.0, "end_s": 0.2}]


async def test_without_a_vad_the_span_starts_when_the_segment_is_finalized(
    fake_engine_factory, clock, exporter
):
    recorder = Recorder()
    engine = fake_engine_factory(_SCRIPT, on_speech_segment=recorder.on_segment)
    clock.advance_ms(1000)
    await engine.start()
    await asyncio.wait_for(recorder.final.wait(), 5)
    await engine.stop()

    (segment,) = recorder.finals
    assert (segment.speech_started_at, segment.speech_ended_at) == (None, None)
    # Still known without a gate: the module decided, when its final arrived.
    assert segment.end_decided_at == to_datetime(clock.at_ms(1000))
    assert segment.end_decided_by == "module"
    assert segment.finalized_at == to_datetime(clock.at_ms(1000))

    (span,) = exporter.get_finished_spans()
    assert span.start_time == clock.at_ms(1000)
    assert _events(span) == [
        ("end_decided", clock.at_ms(1000)),
        ("finalized", clock.at_ms(1000)),
    ]
    assert dict(span.attributes) == {
        "asr_engine.module": "fake",
        "asr_engine.end_reason": "utterance",
        "asr_engine.speech_end_source": "none",
        "asr_engine.end_decided_by": "module",
    }


async def test_with_no_sdk_installed_the_stamps_are_still_on_the_segment(
    fake_engine_factory,
):
    """No tracer provider: the span is a no-op, the plain data is not."""
    recorder = Recorder()
    engine = fake_engine_factory(_SCRIPT, on_speech_segment=recorder.on_segment)
    before = datetime.now().astimezone()
    await engine.start()
    await asyncio.wait_for(recorder.final.wait(), 5)
    await engine.stop()

    (segment,) = recorder.finals
    assert segment.finalized_at is not None and segment.end_decided_at is not None
    assert before <= segment.end_decided_at <= segment.finalized_at
    assert segment.finalized_at.utcoffset() == timedelta(0)  # UTC, aware
    assert recorder.span_ids[-1] == 0  # no recording span was current


# ---------------------------------------------------------------------------
# The span's parent
# ---------------------------------------------------------------------------


async def test_an_always_on_segment_span_is_a_root_whatever_was_current_at_start(
    fake_engine_factory, exporter
):
    """The task delivering segments is created inside start() and inherits its
    context: the span must not become a child of a span that ended long ago."""
    recorder = Recorder()
    engine = fake_engine_factory(_SCRIPT, on_speech_segment=recorder.on_segment)
    with trace.get_tracer("application").start_as_current_span("startup"):
        await engine.start()
    await asyncio.wait_for(recorder.final.wait(), 5)
    await engine.stop()

    (span,) = [s for s in exporter.get_finished_spans() if s.name != "startup"]
    assert span.parent is None


async def test_what_the_callback_does_is_a_child_of_the_segment_span(
    fake_engine_factory, exporter
):
    done = asyncio.Event()

    async def on_segment(segment: SpeechSegment) -> None:
        if segment.is_final:
            with trace.get_tracer("application").start_as_current_span("reaction"):
                done.set()

    engine = fake_engine_factory(_SCRIPT, on_speech_segment=on_segment)
    await engine.start()
    await asyncio.wait_for(done.wait(), 5)
    await engine.stop()

    spans = {s.name: s for s in exporter.get_finished_spans()}
    reaction, segment_span_ = spans["reaction"], spans["asr_engine.segment"]
    assert reaction.parent.span_id == segment_span_.context.span_id
    assert reaction.context.trace_id == segment_span_.context.trace_id
    # The span lasts until the callback returns.
    assert segment_span_.end_time >= reaction.end_time


async def test_a_listen_segment_span_is_a_child_of_the_span_listen_was_awaited_in(
    fake_engine_factory, exporter
):
    engine = fake_engine_factory(_SCRIPT)
    with trace.get_tracer("application").start_as_current_span("command") as command:
        segment = await engine.listen("utterance")

    assert segment.transcript == "hello there"
    (span,) = [s for s in exporter.get_finished_spans() if s.name != "command"]
    assert span.parent.span_id == command.get_span_context().span_id

    # The parent does not outlive the listen: the next always-on span is a root.
    recorder = Recorder()
    engine.on_speech_segment = recorder.on_segment
    exporter.clear()
    await engine.start()
    await asyncio.wait_for(recorder.final.wait(), 5)
    await engine.stop()
    (span,) = exporter.get_finished_spans()
    assert span.parent is None


def test_a_callback_that_raises_ends_the_span_as_an_error(exporter):
    segment = SpeechSegment(
        transcript="hello",
        is_final=True,
        end_reason="utterance",
        finalized_at=to_datetime(1_800_000_000_000_000_000),
    )
    with pytest.raises(RuntimeError, match="consumer failed"):
        with segment_span(
            segment, module="fake", vad_min_silence_duration_ms=None, parent=None
        ):
            raise RuntimeError("consumer failed")

    (span,) = exporter.get_finished_spans()
    assert span.status.status_code is StatusCode.ERROR
    assert [event.name for event in span.events] == ["finalized", "exception"]
    assert not trace.get_current_span().get_span_context().is_valid  # detached


# ---------------------------------------------------------------------------
# Segments of several utterances (the Segmenter)
# ---------------------------------------------------------------------------


def _stamped(text: str, clock, start_ms: int, end_ms: int, by: str) -> SpeechUtterance:
    """A final utterance as the engine hands it to the segmenter."""
    return SpeechUtterance(
        transcript=text,
        is_final=True,
        confidence=None,
        speech_started_at=to_datetime(clock.at_ms(start_ms)),
        speech_ended_at=to_datetime(clock.at_ms(end_ms)),
        end_decided_at=to_datetime(clock.at_ms(end_ms + 50)),
        end_decided_by=by,
    )


def _segmenter(mode: str, segments: list[SpeechSegment], **timeouts) -> Segmenter:
    async def emit(segment: SpeechSegment) -> None:
        segments.append(segment)

    return Segmenter(
        mode=mode,
        trigger_words=["submit"],
        initial_silence_timeout_s=timeouts.get("initial_silence_timeout_s", 10.0),
        end_of_speech_timeout_s=timeouts.get("end_of_speech_timeout_s", 5.0),
        emit=emit,
    )


async def test_a_trigger_word_segment_spans_its_first_speech_to_the_trigger_word(
    clock,
):
    segments: list[SpeechSegment] = []
    segmenter = _segmenter("trigger_word", segments)
    await segmenter.start()
    await segmenter.on_utterance(_stamped("the sky", clock, 100, 600, "vad_gate"))
    await segmenter.on_utterance(_stamped("is blue", clock, 900, 1400, "vad_gate"))
    clock.advance_ms(2100)
    await segmenter.on_utterance(_stamped("submit", clock, 1700, 2000, "module"))

    final = segments[-1]
    assert (final.transcript, final.end_reason) == ("the sky is blue", "trigger_word")
    assert final.speech_started_at == to_datetime(clock.at_ms(100))
    # The trigger word is not in the text, but the speech ended with it.
    assert final.speech_ended_at == to_datetime(clock.at_ms(2000))
    assert final.end_decided_at == to_datetime(clock.at_ms(2050))
    assert final.end_decided_by == "module"
    assert final.finalized_at == to_datetime(clock.at_ms(2100))
    assert all(s.finalized_at is None for s in segments[:-1])  # the open updates


async def test_a_timeout_segment_ends_with_its_last_utterance_and_is_finalized_later(
    clock,
):
    segments: list[SpeechSegment] = []
    segmenter = _segmenter("timeout", segments, end_of_speech_timeout_s=0.05)
    await segmenter.start()
    await segmenter.on_utterance(_stamped("one", clock, 100, 400, "vad_gate"))
    await segmenter.on_utterance(_stamped("two", clock, 700, 900, "vad_gate"))
    clock.advance_ms(6000)  # the silence timer's wait
    await asyncio.sleep(0.1)
    await segmenter.stop()

    final = segments[-1]
    assert (final.transcript, final.end_reason) == ("one two", "end_of_speech_timeout")
    assert final.speech_started_at == to_datetime(clock.at_ms(100))
    assert final.speech_ended_at == to_datetime(clock.at_ms(900))
    # What ended the last utterance; the timer's wait is finalized − end_decided.
    assert final.end_decided_at == to_datetime(clock.at_ms(950))
    assert final.end_decided_by == "vad_gate"
    assert final.finalized_at == to_datetime(clock.at_ms(6000))


async def test_a_segment_closed_on_initial_silence_has_only_its_finalized_stamp(
    clock, exporter
):
    segments: list[SpeechSegment] = []
    segmenter = _segmenter("timeout", segments, initial_silence_timeout_s=0.05)
    await segmenter.start()
    await asyncio.sleep(0.1)
    await segmenter.stop()

    (final,) = segments
    assert final.end_reason == "initial_silence_timeout"
    assert final.finalized_at == to_datetime(clock.BASE)
    assert (
        final.speech_started_at,
        final.speech_ended_at,
        final.end_decided_at,
        final.end_decided_by,
    ) == (None, None, None, None)

    # Its span has no end_decided event and no decider.
    with segment_span(
        final, module="fake", vad_min_silence_duration_ms=None, parent=None
    ):
        pass
    (span,) = exporter.get_finished_spans()
    assert [event.name for event in span.events] == ["finalized"]
    assert "asr_engine.end_decided_by" not in span.attributes


def test_datetimes_and_nanoseconds_convert_exactly_to_the_microsecond():
    ns = 1_800_000_000_123_456_789
    moment = to_datetime(ns)
    assert moment.utcoffset() == timedelta(0)
    assert to_ns(moment) == 1_800_000_000_123_456_000
