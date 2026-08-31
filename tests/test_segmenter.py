"""Unit tests for Segmenter."""

from __future__ import annotations

import asyncio

import pytest

from asr_engine.modules.base import SpeechUtterance
from asr_engine.segmenter import Segmenter, SpeechSegment


def _collector():
    segments: list[SpeechSegment] = []

    async def emit(seg: SpeechSegment) -> None:
        segments.append(seg)

    return segments, emit


def _interim(text: str) -> SpeechUtterance:
    return SpeechUtterance(transcript=text, is_final=False, confidence=None)


def _final(text: str) -> SpeechUtterance:
    return SpeechUtterance(transcript=text, is_final=True, confidence=None)


def _make(mode: str, emit, **kw) -> Segmenter:
    return Segmenter(
        mode=mode,
        trigger_words=kw.get("trigger_words", ["submit"]),
        initial_silence_timeout_s=kw.get("initial_silence_timeout_s", 10.0),
        end_of_speech_timeout_s=kw.get("end_of_speech_timeout_s", 5.0),
        emit=emit,
    )


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_invalid_mode_raises():
    _, emit = _collector()
    with pytest.raises(ValueError, match="Invalid segment mode"):
        _make("bogus", emit)


# ---------------------------------------------------------------------------
# utterance mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_utterance_mode_each_final_is_a_segment():
    segments, emit = _collector()
    seg = _make("utterance", emit)
    await seg.start()

    await seg.on_utterance(_interim("hel"))
    await seg.on_utterance(_final("hello"))
    await seg.on_utterance(_final("world"))

    # interim (open), hello (closed), world (closed)
    assert [(s.transcript, s.is_final, s.end_reason) for s in segments] == [
        ("hel", False, None),
        ("hello", True, "utterance"),
        ("world", True, "utterance"),
    ]
    assert segments[1].utterances == [_final("hello")]


# ---------------------------------------------------------------------------
# trigger_word mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trigger_word_mode_accumulates_then_closes():
    segments, emit = _collector()
    seg = _make("trigger_word", emit, trigger_words=["submit"])
    await seg.start()

    await seg.on_utterance(_interim("the sky"))
    await seg.on_utterance(_final("the sky"))
    await seg.on_utterance(_interim("is blue"))
    await seg.on_utterance(_final("is blue"))
    await seg.on_utterance(_final("submit please"))

    closed = [s for s in segments if s.is_final]
    assert len(closed) == 1
    assert closed[0].transcript == "the sky is blue"
    assert closed[0].end_reason == "trigger_word"
    # The trigger utterance is excluded from the committed utterances.
    assert [u.transcript for u in closed[0].utterances] == ["the sky", "is blue"]


@pytest.mark.asyncio
async def test_trigger_word_open_segment_shows_committed_plus_interim():
    segments, emit = _collector()
    seg = _make("trigger_word", emit, trigger_words=["submit"])
    await seg.start()

    await seg.on_utterance(_final("the sky"))
    await seg.on_utterance(_interim("is"))

    assert segments[-1].transcript == "the sky is"
    assert segments[-1].is_final is False


@pytest.mark.asyncio
async def test_trigger_word_mode_resets_after_close():
    segments, emit = _collector()
    seg = _make("trigger_word", emit, trigger_words=["submit"])
    await seg.start()

    await seg.on_utterance(_final("first"))
    await seg.on_utterance(_final("submit"))
    await seg.on_utterance(_final("second"))
    await seg.on_utterance(_final("submit"))

    closed = [s for s in segments if s.is_final]
    assert [s.transcript for s in closed] == ["first", "second"]


# ---------------------------------------------------------------------------
# timeout mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_timeout_end_of_speech_closes_segment():
    segments, emit = _collector()
    seg = _make(
        "timeout", emit, initial_silence_timeout_s=10.0, end_of_speech_timeout_s=0.05
    )
    await seg.start()

    await seg.on_utterance(_final("hello world"))
    await asyncio.sleep(0.12)  # let the end-of-speech timer fire

    closed = [s for s in segments if s.is_final]
    assert len(closed) == 1
    assert closed[0].transcript == "hello world"
    assert closed[0].end_reason == "end_of_speech_timeout"
    await seg.stop()


@pytest.mark.asyncio
async def test_timeout_initial_silence_closes_empty_segment():
    segments, emit = _collector()
    seg = _make(
        "timeout", emit, initial_silence_timeout_s=0.05, end_of_speech_timeout_s=10.0
    )
    await seg.start()

    await asyncio.sleep(0.12)  # no speech at all

    closed = [s for s in segments if s.is_final]
    assert len(closed) >= 1
    assert closed[0].transcript == ""
    assert closed[0].end_reason == "initial_silence_timeout"
    await seg.stop()


@pytest.mark.asyncio
async def test_timeout_interim_resets_eos_timer():
    segments, emit = _collector()
    seg = _make(
        "timeout", emit, initial_silence_timeout_s=10.0, end_of_speech_timeout_s=0.1
    )
    await seg.start()

    await seg.on_utterance(_final("hello"))
    await asyncio.sleep(0.06)
    await seg.on_utterance(_interim("hello there"))  # resets the eos timer
    await asyncio.sleep(0.06)
    # Still open — the reset pushed the deadline out.
    assert not any(s.is_final for s in segments)

    await asyncio.sleep(0.08)  # now let it fire
    assert any(s.is_final for s in segments)
    await seg.stop()


@pytest.mark.asyncio
async def test_stop_cancels_timers():
    segments, emit = _collector()
    seg = _make(
        "timeout", emit, initial_silence_timeout_s=0.05, end_of_speech_timeout_s=0.05
    )
    await seg.start()
    await seg.stop()

    await asyncio.sleep(0.12)
    assert not any(s.is_final for s in segments)
