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
class SpeechTiming:
    """What the VAD gate knows about the speech a final utterance transcribes.

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
    parent: otel_context.Context | None,
) -> Iterator[None]:
    """The span of one final *segment*, current inside the ``with`` block.

    It starts at the end of the speech when the segment knows it, else when the
    segment was finalized, and ends when the block exits. *parent* is the
    context the span is a child of; ``None`` makes it the root of a new trace.
    The events carry the times they name, not the time they are added.
    """
    finalized = (
        to_ns(segment.finalized_at) if segment.finalized_at is not None else now_ns()
    )
    ended = (
        to_ns(segment.speech_ended_at) if segment.speech_ended_at is not None else None
    )
    attributes: dict[str, AttributeValue] = {
        ATTR_MODULE: module,
        ATTR_SPEECH_END_SOURCE: "none" if ended is None else "vad",
    }
    if segment.end_reason is not None:
        attributes[ATTR_END_REASON] = segment.end_reason
    if segment.end_decided_by is not None:
        attributes[ATTR_END_DECIDED_BY] = segment.end_decided_by
    if vad_min_silence_duration_ms is not None:
        attributes[ATTR_VAD_MIN_SILENCE_DURATION_MS] = vad_min_silence_duration_ms
    if segment.speech_started_at is not None:
        attributes[ATTR_SPEECH_STARTED_UNIX_NS] = to_ns(segment.speech_started_at)

    # An empty context, not the current one: the task that delivers segments
    # was created inside start(), and carries whatever was current there.
    base = otel_context.Context() if parent is None else parent
    span = trace.get_tracer(TRACER_NAME).start_span(
        SPAN_NAME,
        context=base,
        start_time=finalized if ended is None else ended,
        attributes=attributes,
    )
    if segment.end_decided_at is not None:
        span.add_event(EVENT_END_DECIDED, timestamp=to_ns(segment.end_decided_at))
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
