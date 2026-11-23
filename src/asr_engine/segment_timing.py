"""Segment timing: when the speech of a final segment started and ended, when
its end was decided, and one OpenTelemetry span per final segment.

The names below are a public contract (specs/segment-timing.md): a trace reader
finds a segment by its tracer and span name, and reads its steps from the event
names. This module also owns the clock every stamp is read from.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal

from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
from opentelemetry.util.types import AttributeValue

if TYPE_CHECKING:
    from asr_engine.segmenter import SpeechSegment

TRACER_NAME = "asr_engine"
SPAN_NAME = "asr_engine.segment"

EVENT_END_DECIDED = "end_decided"
EVENT_FINALIZED = "finalized"

ATTR_MODULE = "asr_engine.module"
ATTR_END_REASON = "asr_engine.end_reason"
ATTR_SPEECH_END_SOURCE = "asr_engine.speech_end_source"
ATTR_END_DECIDED_BY = "asr_engine.end_decided_by"
ATTR_VAD_MIN_SILENCE_DURATION_MS = "asr_engine.vad.min_silence_duration_ms"
ATTR_SPEECH_STARTED_UNIX_NS = "asr_engine.speech_started_unix_ns"
ATTR_SEGMENTATION_MODE = "asr_engine.segmentation_mode"
ATTR_END_OF_SPEECH_TIMEOUT_S = "asr_engine.segmentation.end_of_speech_timeout_s"

# The segmentation mode that closes a segment for each reason: read from the
# segment itself, so it is the mode the segment was produced under.
_MODE_OF_END_REASON = {
    "utterance": "utterance",
    "trigger_word": "trigger_word",
    "end_of_speech_timeout": "timeout",
    "initial_silence_timeout": "timeout",
}

# What decided that the person had finished: the VAD gate closing, or the ASR
# module's own end of turn.
EndDecidedBy = Literal["vad_gate", "module"]

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_MICROSECOND = timedelta(microseconds=1)


def now_ns() -> int:
    """The wall clock, in nanoseconds since the epoch.

    Every stamp of the package is read here, so they compare with each other and
    with the stamps of other packages in the same trace.
    """
    return time.time_ns()


def to_datetime(ns: int) -> datetime:
    """*ns* since the epoch as a timezone-aware UTC datetime (microseconds)."""
    return _EPOCH + timedelta(microseconds=ns // 1000)


def to_ns(moment: datetime) -> int:
    """The inverse of ``to_datetime``, in integer arithmetic."""
    return ((moment - _EPOCH) // _MICROSECOND) * 1000


@dataclass(frozen=True)
class UtteranceTiming:
    """When the speech of one final utterance happened, as UTC datetimes.

    The two speech stamps come from the VAD gate: they are ``None`` with no VAD,
    once the gate has failed open, and when it detected no speech.
    ``speech_ended_at`` is also ``None`` while the person has not paused yet.
    """

    speech_started_at: datetime | None  # the onset the gate detected
    speech_ended_at: datetime | None  # the end of the last speech silence followed
    end_decided_at: datetime  # when the utterance's end was decided
    end_decided_by: EndDecidedBy


@dataclass(frozen=True)
class SegmentTiming:
    """When the speech of one final segment happened, as UTC datetimes.

    A stamp that could not be taken is ``None``, never guessed. A segment closed
    with no utterance has ``finalized_at`` only.
    """

    speech_started_at: datetime | None  # the start of its first speech
    speech_ended_at: datetime | None  # the end of its last speech
    end_decided_at: datetime | None  # when its last utterance's end was decided
    end_decided_by: EndDecidedBy | None
    finalized_at: datetime  # when the final segment was produced


@dataclass(frozen=True)
class GateSpeechTiming:
    """What the VAD gate knows about the speech a final utterance transcribes,
    in nanoseconds since the epoch. Internal: the engine turns it into an
    ``UtteranceTiming``.

    ``started_ns``: the start of the first frame scored as speech.
    ``ended_ns``: the end of the last speech frame that silence followed, or
    ``None`` while the person has not paused yet.
    ``closed_ns``: when the gate closed on that speech, or ``None`` if it was
    still open when the final arrived.
    """

    started_ns: int
    ended_ns: int | None
    closed_ns: int | None


@contextmanager
def segment_span(
    segment: SpeechSegment,
    *,
    module: str,
    vad_min_silence_duration_ms: int | None,
    end_of_speech_timeout_s: float,
    parent: otel_context.Context | None,
) -> Iterator[None]:
    """The span of one final *segment*, current inside the ``with`` block.

    It starts at the end of the speech when the segment knows it, else when the
    segment was finalized, and ends when the block exits. *parent* is the
    context the span is a child of; ``None`` makes it the root of a new trace.
    The events carry the times they name, not the time they are added.
    """
    timing = segment.timing
    finalized = to_ns(timing.finalized_at) if timing is not None else now_ns()
    ended = (
        to_ns(timing.speech_ended_at)
        if timing is not None and timing.speech_ended_at is not None
        else None
    )
    attributes: dict[str, AttributeValue] = {
        ATTR_MODULE: module,
        ATTR_SPEECH_END_SOURCE: "none" if ended is None else "vad",
    }
    if segment.end_reason is not None:
        attributes[ATTR_END_REASON] = segment.end_reason
        mode = _MODE_OF_END_REASON.get(segment.end_reason)
        if mode is not None:
            attributes[ATTR_SEGMENTATION_MODE] = mode
        if segment.end_reason == "end_of_speech_timeout":
            # The configured wait inside finalized − end_decided.
            attributes[ATTR_END_OF_SPEECH_TIMEOUT_S] = float(end_of_speech_timeout_s)
    if vad_min_silence_duration_ms is not None:
        attributes[ATTR_VAD_MIN_SILENCE_DURATION_MS] = vad_min_silence_duration_ms
    if timing is not None and timing.end_decided_by is not None:
        attributes[ATTR_END_DECIDED_BY] = timing.end_decided_by
    if timing is not None and timing.speech_started_at is not None:
        attributes[ATTR_SPEECH_STARTED_UNIX_NS] = to_ns(timing.speech_started_at)

    # An empty context, not the current one: the task that delivers segments
    # was created inside start(), and carries whatever was current there.
    base = otel_context.Context() if parent is None else parent
    span = trace.get_tracer(TRACER_NAME).start_span(
        SPAN_NAME,
        context=base,
        start_time=finalized if ended is None else ended,
        attributes=attributes,
    )
    if timing is not None and timing.end_decided_at is not None:
        span.add_event(EVENT_END_DECIDED, timestamp=to_ns(timing.end_decided_at))
    span.add_event(EVENT_FINALIZED, timestamp=finalized)

    token = otel_context.attach(trace.set_span_in_context(span, base))
    try:
        yield
    except asyncio.CancelledError:
        raise  # a decision of the caller, not an error of the callback
    except Exception as exc:
        span.record_exception(exc)
        span.set_status(Status(StatusCode.ERROR, str(exc)))
        raise
    finally:
        otel_context.detach(token)
        span.end()
