"""Unit tests for AsrTools — the transport-agnostic tools layer."""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, cast

import pytest

from asr_engine.engine import ASREngine
from asr_engine.segmenter import SpeechSegment
from asr_engine.tools import AsrTools

ListenImpl = Callable[[Any, Any], Awaitable[SpeechSegment]]


class FakeEngine:
    """Minimal ASREngine stand-in for driving AsrTools."""

    def __init__(self) -> None:
        self._running = False
        self._connected = False
        self.started = False
        self.stopped = False
        self.listen_mode: Any = "unset"
        self._listen_impl: ListenImpl | None = None
        self.dictating = False
        self.segmentation_mode = "utterance"
        self.dictation_started_with: Any = None
        self.dictation_default: Any = None
        self.listen_default: Any = None

    def status(self) -> dict:
        return {"running": self._running, "connected": self._connected}

    async def start_dictation(
        self, end_on_final_segment: bool = True, segmentation_mode=None
    ) -> None:
        self.dictation_started_with = (end_on_final_segment, segmentation_mode)
        self.dictating = True
        self.segmentation_mode = segmentation_mode or "trigger_word"

    async def stop_dictation(self) -> None:
        self.dictating = False
        self.segmentation_mode = "utterance"

    def set_dictation_default_segmentation_mode(self, mode: str) -> None:
        self.dictation_default = mode

    def set_listen_default_segmentation_mode(self, mode: str) -> None:
        self.listen_default = mode

    async def start(self) -> None:
        self.started = True
        self._running = True

    async def stop(self) -> None:
        self.stopped = True
        self._running = False

    async def listen(self, mode=None, *, on_update=None):
        self.listen_mode = mode
        if self._listen_impl is not None:
            return await self._listen_impl(mode, on_update)
        return SpeechSegment("hello", True, "trigger_word", utterances=[])


def _tools(engine: FakeEngine) -> AsrTools:
    """Build AsrTools over a FakeEngine (cast for the type checker)."""
    return AsrTools(cast(ASREngine, engine))


def _seg(text, is_final, end_reason=None, utterances=None):
    return SpeechSegment(text, is_final, end_reason, utterances or [])


# ---------------------------------------------------------------------------
# start / stop / is_running
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_returns_running_and_starts_engine():
    engine = FakeEngine()
    tools = _tools(engine)
    assert await tools.start() == {"status": "running"}
    assert engine.started is True


@pytest.mark.asyncio
async def test_stop_returns_stopped_and_stops_engine():
    engine = FakeEngine()
    tools = _tools(engine)
    assert await tools.stop() == {"status": "stopped"}
    assert engine.stopped is True


def test_is_running_reflects_engine_status():
    engine = FakeEngine()
    engine._running = True
    engine._connected = True
    tools = _tools(engine)
    assert tools.is_running() == {"running": True, "connected": True}


# ---------------------------------------------------------------------------
# listen
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_listen_returns_transcript_and_reason():
    engine = FakeEngine()
    tools = _tools(engine)
    result = await tools.listen(mode="trigger_word")
    assert result == {"transcript": "hello", "end_reason": "trigger_word"}
    assert engine.listen_mode == "trigger_word"


@pytest.mark.asyncio
async def test_listen_passes_none_mode_through():
    engine = FakeEngine()
    tools = _tools(engine)
    await tools.listen()
    assert engine.listen_mode is None


@pytest.mark.asyncio
async def test_listen_reports_progress_once_per_committed_final():
    engine = FakeEngine()

    async def impl(mode, on_update):
        await on_update(_seg("one", False, utterances=["a"]))
        await on_update(_seg("one two", False, utterances=["a", "b"]))
        # interim growth with no new committed final → no progress
        await on_update(_seg("one two thr", False, utterances=["a", "b"]))
        await on_update(_seg("one two", True, "trigger_word", utterances=["a", "b"]))
        return _seg("one two", True, "trigger_word", utterances=["a", "b"])

    engine._listen_impl = impl
    tools = _tools(engine)

    calls: list[tuple] = []

    async def on_progress(progress, total, message):
        calls.append((progress, total, message))

    await tools.listen(mode="trigger_word", on_progress=on_progress)

    assert calls == [(1, None, "one"), (2, None, "one two")]


@pytest.mark.asyncio
async def test_listen_no_progress_when_no_finals_committed():
    engine = FakeEngine()

    async def impl(mode, on_update):
        await on_update(_seg("", True, "trigger_word", utterances=[]))
        return _seg("", True, "trigger_word", utterances=[])

    engine._listen_impl = impl
    tools = _tools(engine)

    calls: list[tuple] = []

    async def on_progress(progress, total, message):
        calls.append((progress, total, message))

    await tools.listen(on_progress=on_progress)
    assert calls == []


@pytest.mark.asyncio
async def test_listen_rejects_when_engine_running():
    engine = FakeEngine()
    engine._running = True
    tools = _tools(engine)
    with pytest.raises(ValueError, match="already running"):
        await tools.listen(mode="trigger_word")


@pytest.mark.asyncio
async def test_listen_rejects_concurrent_calls():
    engine = FakeEngine()
    release = asyncio.Event()

    async def impl(mode, on_update):
        await release.wait()
        return _seg("done", True, "trigger_word", utterances=[])

    engine._listen_impl = impl
    tools = _tools(engine)

    first = asyncio.create_task(tools.listen(mode="trigger_word"))
    await asyncio.sleep(0)  # let first grab the lock

    with pytest.raises(ValueError, match="already in progress"):
        await tools.listen(mode="trigger_word")

    release.set()
    assert (await first)["transcript"] == "done"


# ---------------------------------------------------------------------------
# dictation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_dictation_returns_status_mode_and_flag():
    engine = FakeEngine()
    tools = _tools(engine)
    result = await tools.start_dictation(
        end_on_final_segment=False, segmentation_mode="timeout"
    )
    assert result == {
        "status": "dictating",
        "mode": "timeout",
        "end_on_final_segment": False,
    }
    assert engine.dictation_started_with == (False, "timeout")
    assert engine.dictating is True


@pytest.mark.asyncio
async def test_stop_dictation_returns_status():
    engine = FakeEngine()
    engine.dictating = True
    tools = _tools(engine)
    assert await tools.stop_dictation() == {"status": "dictation_stopped"}
    assert engine.dictating is False


def test_is_dictation_running_reports_state_and_mode():
    engine = FakeEngine()
    engine.dictating = True
    engine.segmentation_mode = "trigger_word"
    tools = _tools(engine)
    assert tools.is_dictation_running() == {
        "dictating": True,
        "segmentation_mode": "trigger_word",
    }


def test_set_dictation_default_returns_and_applies_mode():
    engine = FakeEngine()
    tools = _tools(engine)
    assert tools.set_dictation_default_segmentation_mode("timeout") == {
        "dictation_default_segmentation_mode": "timeout"
    }
    assert engine.dictation_default == "timeout"


def test_set_listen_default_returns_and_applies_mode():
    engine = FakeEngine()
    tools = _tools(engine)
    assert tools.set_listen_default_segmentation_mode("utterance") == {
        "listen_default_segmentation_mode": "utterance"
    }
    assert engine.listen_default == "utterance"
