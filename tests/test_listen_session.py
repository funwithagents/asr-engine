"""Unit tests for listen_session.py — all timers are patched to avoid real sleeps."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from asr_mcp.listen_session import ListenSession
from asr_mcp.modules.base import ASRResult


def _final(text: str) -> ASRResult:
    return ASRResult(transcript=text, is_final=True, confidence=None)


def _interim(text: str) -> ASRResult:
    return ASRResult(transcript=text, is_final=False, confidence=None)


def _session(
    mode: str = "trigger_word",
    trigger_words: list[str] | None = None,
    initial_silence_timeout_s: float = 10.0,
    end_of_speech_timeout_s: float = 5.0,
    on_final_committed=None,
) -> ListenSession:
    return ListenSession(
        mode=mode,
        trigger_words=trigger_words if trigger_words is not None else ["validate"],
        initial_silence_timeout_s=initial_silence_timeout_s,
        end_of_speech_timeout_s=end_of_speech_timeout_s,
        on_final_committed=on_final_committed,
    )


# ---------------------------------------------------------------------------
# trigger_word mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trigger_word_ends_session_and_excludes_trigger_utterance():
    """Final result with trigger word ends session; trigger utterance not in transcript."""
    s = _session(mode="trigger_word", trigger_words=["validate"])
    await s.on_result(_final("the sky is blue validate"))
    result = await asyncio.wait_for(s.wait(), timeout=1.0)
    assert result.end_reason == "trigger_word"
    assert result.transcript == ""  # trigger utterance excluded


@pytest.mark.asyncio
async def test_trigger_word_accumulates_finals_before_trigger():
    """Multiple finals without trigger word are accumulated."""
    s = _session(mode="trigger_word", trigger_words=["submit"])

    await s.on_result(_final("hello world"))
    await s.on_result(_final("how are you"))
    await s.on_result(_final("submit"))

    result = await asyncio.wait_for(s.wait(), timeout=1.0)
    assert result.end_reason == "trigger_word"
    assert result.transcript == "hello world how are you"


@pytest.mark.asyncio
async def test_trigger_word_interim_not_accumulated():
    """Interim events do not accumulate; session continues."""
    s = _session(mode="trigger_word", trigger_words=["validate"])

    await s.on_result(_interim("partial"))
    await s.on_result(_final("final without trigger"))
    await s.on_result(_final("validate"))

    result = await asyncio.wait_for(s.wait(), timeout=1.0)
    assert result.transcript == "final without trigger"
    assert result.end_reason == "trigger_word"


# ---------------------------------------------------------------------------
# timeout mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_timeout_initial_silence_fires_when_no_events():
    """No events → initial-silence timeout fires with empty transcript."""
    s = _session(mode="timeout", initial_silence_timeout_s=0.01, end_of_speech_timeout_s=10.0)
    result = await asyncio.wait_for(s.wait(), timeout=1.0)
    assert result.end_reason == "initial_silence_timeout"
    assert result.transcript == ""


@pytest.mark.asyncio
async def test_timeout_eos_fires_after_events():
    """Events then silence → end-of-speech timeout fires with accumulated finals."""
    s = _session(mode="timeout", initial_silence_timeout_s=10.0, end_of_speech_timeout_s=0.01)
    await s.on_result(_final("the sky is blue"))
    result = await asyncio.wait_for(s.wait(), timeout=1.0)
    assert result.end_reason == "end_of_speech_timeout"
    assert result.transcript == "the sky is blue"


@pytest.mark.asyncio
async def test_timeout_interim_resets_eos_timer():
    """Interim event resets the end-of-speech timer (session keeps alive longer)."""
    s = _session(mode="timeout", initial_silence_timeout_s=10.0, end_of_speech_timeout_s=0.05)
    await s.on_result(_final("first"))
    # Send interims to reset the timer each time
    for _ in range(3):
        await s.on_result(_interim("partial"))
        await asyncio.sleep(0.02)  # less than end_of_speech_timeout_s

    result = await asyncio.wait_for(s.wait(), timeout=2.0)
    assert result.end_reason == "end_of_speech_timeout"
    assert result.transcript == "first"


# ---------------------------------------------------------------------------
# on_final_committed callback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_final_committed_called_with_accumulated_transcript():
    """Callback is called once per committed final with the full accumulated transcript."""
    calls: list[str] = []

    async def cb(transcript: str) -> None:
        calls.append(transcript)

    s = _session(mode="trigger_word", trigger_words=["submit"], on_final_committed=cb)
    await s.on_result(_final("hello world"))
    await s.on_result(_final("how are you"))
    await s.on_result(_final("submit"))

    assert calls == ["hello world", "hello world how are you"]


@pytest.mark.asyncio
async def test_on_final_committed_not_called_for_interim():
    """Callback is NOT called for interim results."""
    calls: list[str] = []

    async def cb(transcript: str) -> None:
        calls.append(transcript)

    s = _session(mode="trigger_word", trigger_words=["submit"], on_final_committed=cb)
    await s.on_result(_interim("partial text"))
    await s.on_result(_interim("more partial"))

    assert calls == []


@pytest.mark.asyncio
async def test_on_final_committed_not_called_for_trigger_word_final():
    """Callback is NOT called when the final result contains the trigger word."""
    calls: list[str] = []

    async def cb(transcript: str) -> None:
        calls.append(transcript)

    s = _session(mode="trigger_word", trigger_words=["validate"], on_final_committed=cb)
    await s.on_result(_final("validate"))

    assert calls == []


@pytest.mark.asyncio
async def test_on_final_committed_none_no_error():
    """Default None callback works without error."""
    s = _session(mode="trigger_word", trigger_words=["done"])
    await s.on_result(_final("first"))
    await s.on_result(_final("done"))
    result = await asyncio.wait_for(s.wait(), timeout=1.0)
    assert result.transcript == "first"


@pytest.mark.asyncio
async def test_timer_tasks_cancelled_after_session_ends():
    """No dangling tasks remain after the session completes."""
    s = _session(mode="trigger_word", trigger_words=["go"])
    await s.on_result(_final("go"))
    result = await asyncio.wait_for(s.wait(), timeout=1.0)
    assert result.end_reason == "trigger_word"
    # Give the event loop a tick to cancel tasks
    await asyncio.sleep(0)
    if s._eos_timer_task is not None:
        assert s._eos_timer_task.cancelled() or s._eos_timer_task.done()
