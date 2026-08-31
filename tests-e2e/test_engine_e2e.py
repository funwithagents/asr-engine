"""End-to-end tests for ASREngine directly (no MCP server).

Drives FileAudioSource → ASREngine → live Deepgram, consuming utterances and
segments straight off the engine's callbacks / `listen` primitive — the way a
program that `import asr_engine` uses it. Needs a Deepgram API key (see helpers).
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest
from helpers import build_engine, build_file_engine, load_api_key

from asr_engine import ScriptableAudioSource, SpeechSegment, SpeechUtterance

_FIXTURE_WAV = Path(__file__).parent / "fixtures" / "sample.wav"
_FIXTURE_SUBMIT_WAV = Path(__file__).parent / "fixtures" / "sample_submit.wav"


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()


async def _wait_until(predicate, timeout: float = 30.0) -> None:
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.1)
    raise TimeoutError("condition not met within timeout")


@pytest.mark.asyncio
async def test_e2e_engine_streams_utterances_and_segments() -> None:
    """Always-on engine (utterance mode) emits interim+final utterances and a closed segment."""
    api_key = load_api_key()
    utterances: list[SpeechUtterance] = []
    segments: list[SpeechSegment] = []

    async def on_utt(u: SpeechUtterance) -> None:
        utterances.append(u)

    async def on_seg(s: SpeechSegment) -> None:
        segments.append(s)

    engine = build_file_engine(
        _FIXTURE_WAV,
        "deepgram_v1",
        {"api_key": api_key, "model": "nova-3"},
        segmentation_mode="utterance",
        trailing_silence_s=3.0,
        on_speech_utterance=on_utt,
        on_speech_segment=on_seg,
    )
    try:
        await engine.start()
        # Wait for a final segment (a final utterance closes one in utterance mode).
        await _wait_until(lambda: any(s.is_final for s in segments))
    finally:
        await engine.stop()

    # Utterances: at least one interim and one final flowed through.
    assert any(not u.is_final for u in utterances)
    finals = [u for u in utterances if u.is_final]
    assert finals, "expected at least one final utterance"
    assert "the sky is blue" in _normalize(" ".join(u.transcript for u in finals))

    # Segment: a closed segment with end_reason 'utterance'.
    closed = [s for s in segments if s.is_final]
    assert closed
    assert closed[0].end_reason == "utterance"
    assert "the sky is blue" in _normalize(closed[0].transcript)


@pytest.mark.asyncio
async def test_e2e_engine_listen_trigger_word() -> None:
    """engine.listen(trigger_word) returns the closed segment, excluding the trigger."""
    api_key = load_api_key()
    engine = build_file_engine(
        _FIXTURE_SUBMIT_WAV,
        "deepgram_v1",
        {"api_key": api_key, "model": "nova-3"},
        trigger_words=["validate"],
        trailing_silence_s=2.0,
    )
    segment = await asyncio.wait_for(engine.listen(mode="trigger_word"), timeout=30.0)

    assert segment.is_final is True
    assert segment.end_reason == "trigger_word"
    assert "validate" not in _normalize(segment.transcript)
    assert engine.status()["running"] is False


@pytest.mark.asyncio
async def test_e2e_engine_listen_timeout() -> None:
    """engine.listen(timeout) closes on end-of-speech silence with the full transcript."""
    api_key = load_api_key()
    engine = build_file_engine(
        _FIXTURE_WAV,
        "deepgram_v1",
        {"api_key": api_key, "model": "nova-3"},
        end_of_speech_timeout_s=2.0,
        trailing_silence_s=3.0,
    )
    segment = await asyncio.wait_for(engine.listen(mode="timeout"), timeout=30.0)

    assert segment.is_final is True
    assert segment.end_reason == "end_of_speech_timeout"
    assert "the sky is blue" in _normalize(segment.transcript)


@pytest.mark.asyncio
async def test_e2e_scriptable_source_sequences_two_utterances() -> None:
    """ScriptableAudioSource plays two files on demand; each yields its own final segment."""
    api_key = load_api_key()
    finals: list[SpeechSegment] = []

    async def on_seg(s: SpeechSegment) -> None:
        if s.is_final:
            finals.append(s)

    source = ScriptableAudioSource()
    engine = build_engine(
        source,
        "deepgram_v1",
        {"api_key": api_key, "model": "nova-3"},
        segmentation_mode="utterance",
        on_speech_segment=on_seg,
    )
    try:
        await engine.start()

        # First utterance: play, then wait for the engine to close its segment.
        await source.play(_FIXTURE_WAV, trailing_silence_s=2.0)
        await _wait_until(lambda: len(finals) >= 1)
        after_first = len(finals)

        # Second utterance, sequenced after the first — a fresh segment closes.
        await source.play(_FIXTURE_SUBMIT_WAV, trailing_silence_s=2.0)
        await _wait_until(lambda: len(finals) > after_first)
    finally:
        await engine.stop()

    assert "the sky is blue" in _normalize(finals[0].transcript)
    # The second fixture adds the word "validate"; it lands in a later segment.
    assert "validate" in _normalize(
        " ".join(s.transcript for s in finals[after_first:])
    )
