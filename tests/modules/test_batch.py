"""Functional tests for ``BatchASRModule`` (specs/batch-asr-module.md).

A scripted subclass "transcribes" audio deterministically: each 100 ms chunk is
filled with one int16 value *k*, and the transcript names the runs of non-zero
values in order ("w1 w2"), so every final and interim shows exactly which audio
it was decoded from. A ``threading.Event`` can hold a decode in flight.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any, Callable
from unittest.mock import patch

import numpy as np
import pytest

from asr_engine.audio import END_OF_SPEECH, AudioFormat
from asr_engine.config import (
    ASREngineConfig,
    AudioConfig,
    ModuleConfig,
    SoundFeedbackConfig,
)
from asr_engine.engine import ASREngine
from asr_engine.modules.base import AudioQueue, SpeechUtterance
from asr_engine.modules.batch import BatchASRModule

CHUNK_SAMPLES = 1600  # 100 ms at 16 kHz


def chunk(k: int) -> bytes:
    return np.full(CHUNK_SAMPLES, k, dtype="<i2").tobytes()


class ScriptedBatch(BatchASRModule):
    LABEL = "scripted_batch"

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self.loads = 0
        self.load_failures = 0  # how many load() calls fail first
        self.load_gate = threading.Event()
        self.load_gate.set()
        self.decode_gate = threading.Event()
        self.decode_gate.set()
        self.decodes: list[str] = []

    def load(self) -> None:
        self.load_gate.wait(5)
        self.loads += 1
        if self.load_failures:
            self.load_failures -= 1
            raise RuntimeError("weights not found")

    def transcribe(self, audio: np.ndarray) -> str:
        self.decode_gate.wait(5)
        values = np.round(audio * 32768).astype(int)
        words: list[str] = []
        previous = None
        for v in values:
            if v != previous and v != 0:
                words.append(f"w{v}")
            previous = v
        if 9 in values:
            raise RuntimeError("bad audio")
        text = " ".join(words)
        self.decodes.append(text)
        return text


async def _until(predicate: Callable[[], bool], timeout: float = 3.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not met within timeout")
        await asyncio.sleep(0.002)


class Harness:
    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.module = ScriptedBatch({"interim_interval_s": 0, **(config or {})})
        self.module._BACKOFF_S = (0.01,)
        self.queue: AudioQueue = asyncio.Queue()
        self.utterances: list[SpeechUtterance] = []
        self.connected: list[bool] = []
        self.task: asyncio.Task | None = None

    async def start(self, *, wait_connected: bool = True) -> None:
        async def on_utterance(u: SpeechUtterance) -> None:
            self.utterances.append(u)

        self.task = asyncio.create_task(
            self.module.start(
                self.queue,
                on_utterance,
                self.connected.append,
                audio_format=AudioFormat(sample_rate=16000),
            )
        )
        if wait_connected:
            await _until(lambda: self.connected[-1:] == [True])

    async def stop(self) -> None:
        await self.module.stop()
        assert self.task is not None
        await asyncio.wait_for(self.task, 2.0)

    def put(self, *items: Any) -> None:
        for item in items:
            self.queue.put_nowait(item)

    def finals(self) -> list[str]:
        return [u.transcript for u in self.utterances if u.is_final]

    def interims(self) -> list[str]:
        return [u.transcript for u in self.utterances if not u.is_final]


@pytest.fixture
async def harnesses():
    made: list[Harness] = []

    def make(config: dict[str, Any] | None = None) -> Harness:
        h = Harness(config)
        made.append(h)
        return h

    yield make
    for h in made:
        h.module.decode_gate.set()
        h.module.load_gate.set()
        if h.task is not None and not h.task.done():
            h.task.cancel()
            try:
                await h.task
            except (asyncio.CancelledError, Exception):
                pass


# ---------------------------------------------------------------------------- load


async def test_connected_only_once_the_model_is_loaded(harnesses) -> None:
    h = harnesses()
    h.module.load_gate.clear()
    await h.start(wait_connected=False)
    await asyncio.sleep(0.02)
    assert h.connected == []

    h.module.load_gate.set()
    await _until(lambda: h.connected == [True])
    assert h.module.loads == 1


async def test_the_model_stays_loaded_across_stop_and_start(harnesses) -> None:
    h = harnesses()
    await h.start()
    await h.stop()
    await h.start()
    await h.stop()

    assert h.module.loads == 1
    assert h.connected == [True, False, True, False]


async def test_a_failed_load_retries_and_drops_the_audio_meanwhile(harnesses) -> None:
    h = harnesses()
    h.module.load_failures = 1
    h.put(chunk(5), END_OF_SPEECH)
    await h.start()
    h.put(chunk(1), END_OF_SPEECH)
    await _until(lambda: h.finals() == ["w1"])

    assert h.module.loads == 2
    assert h.module.decodes == ["w1"]


async def test_a_load_interrupted_by_stop_is_reused_by_the_next_start(
    harnesses,
) -> None:
    h = harnesses()
    h.module.load_gate.clear()
    await h.start(wait_connected=False)
    await _until(lambda: h.module._load_future is not None)
    await h.stop()
    assert h.connected == []

    await h.start(wait_connected=False)
    h.module.load_gate.set()
    await _until(lambda: h.connected == [True])
    assert h.module.loads == 1


# -------------------------------------------------------------------------- finals


async def test_a_marker_decodes_exactly_the_utterance(harnesses) -> None:
    h = harnesses()
    await h.start()
    h.put(chunk(1), chunk(2), END_OF_SPEECH, chunk(3), END_OF_SPEECH)
    await _until(lambda: len(h.finals()) == 2)

    assert h.finals() == ["w1 w2", "w3"]
    assert h.interims() == []


async def test_finals_leave_in_order_when_the_first_decode_is_slow(harnesses) -> None:
    h = harnesses()
    await h.start()
    h.module.decode_gate.clear()
    h.put(chunk(1), END_OF_SPEECH, chunk(2), END_OF_SPEECH)
    await asyncio.sleep(0.02)
    assert h.finals() == []

    h.module.decode_gate.set()
    await _until(lambda: len(h.finals()) == 2)
    assert h.finals() == ["w1", "w2"]


async def test_an_empty_transcript_emits_nothing(harnesses) -> None:
    h = harnesses()
    await h.start()
    h.put(chunk(0), END_OF_SPEECH, chunk(4), END_OF_SPEECH)
    await _until(lambda: h.finals() == ["w4"])

    assert h.module.decodes == ["", "w4"]


async def test_a_marker_on_an_empty_buffer_decodes_nothing(harnesses) -> None:
    h = harnesses()
    await h.start()
    h.put(END_OF_SPEECH, END_OF_SPEECH, chunk(1), END_OF_SPEECH)
    await _until(lambda: h.finals() == ["w1"])

    assert h.module.decodes == ["w1"]


async def test_a_failed_decode_is_logged_and_the_next_utterance_works(
    harnesses, caplog
) -> None:
    h = harnesses()
    await h.start()
    with caplog.at_level(logging.ERROR):
        h.put(chunk(9), END_OF_SPEECH, chunk(2), END_OF_SPEECH)
        await _until(lambda: h.finals() == ["w2"])

    assert "transcription failed" in caplog.text


async def test_a_long_utterance_is_cut_at_max_utterance_s(harnesses) -> None:
    h = harnesses({"max_utterance_s": 0.2})
    await h.start()
    h.put(chunk(1), chunk(2), chunk(3), END_OF_SPEECH)
    await _until(lambda: len(h.finals()) == 2)

    assert h.finals() == ["w1 w2", "w3"]


# ------------------------------------------------------------------------ interims


async def test_interims_redecode_the_growing_utterance(harnesses) -> None:
    h = harnesses({"interim_interval_s": 0.1})
    await h.start()
    for k in (1, 2, 3):
        h.put(chunk(k))
        await _until(lambda k=k: len(h.interims()) == k)
    h.put(END_OF_SPEECH)
    await _until(lambda: len(h.finals()) == 1)

    assert h.interims() == ["w1", "w1 w2", "w1 w2 w3"]
    assert h.finals() == ["w1 w2 w3"]
    assert h.utterances[-1].is_final


async def test_only_one_interim_is_in_flight(harnesses) -> None:
    h = harnesses({"interim_interval_s": 0.1})
    await h.start()
    h.module.decode_gate.clear()
    h.put(chunk(1), chunk(2), chunk(3))
    await asyncio.sleep(0.02)
    h.module.decode_gate.set()
    await _until(lambda: h.interims() == ["w1"])
    h.put(chunk(4))
    await _until(lambda: h.interims() == ["w1", "w1 w2 w3 w4"])

    assert h.module.decodes == ["w1", "w1 w2 w3 w4"]


async def test_no_interim_follows_its_own_final(harnesses) -> None:
    h = harnesses({"interim_interval_s": 0.1})
    await h.start()
    h.module.decode_gate.clear()
    h.put(chunk(1), END_OF_SPEECH)
    await asyncio.sleep(0.02)
    h.module.decode_gate.set()
    await _until(lambda: h.finals() == ["w1"])
    await asyncio.sleep(0.02)

    assert h.interims() == []
    assert h.module.decodes == ["w1", "w1"]  # the interim was decoded, then dropped


# ---------------------------------------------------------------------------- stop


async def test_stop_flushes_the_open_utterance(harnesses) -> None:
    h = harnesses()
    await h.start()
    h.put(chunk(1), chunk(2))
    await _until(lambda: h.queue.empty())

    await h.stop()

    assert h.finals() == ["w1 w2"]
    assert h.connected == [True, False]


# -------------------------------------------------------------------------- engine


def test_the_engine_rejects_a_batch_module_without_vad() -> None:
    config = ASREngineConfig(
        sound_feedback=SoundFeedbackConfig(enabled=False),
        audio=AudioConfig(sample_rate=16000),
        module=ModuleConfig(type="scripted_batch", extra={}),
    )
    with patch.dict("asr_engine.modules.REGISTRY", {"scripted_batch": ScriptedBatch}):
        with pytest.raises(ValueError, match="vad"):
            ASREngine(config)


# ---------------------------------------------------------------------- validation


@pytest.mark.parametrize(
    "config, field",
    [
        ({"interim_interval_s": -1}, "interim_interval_s"),
        ({"interim_interval_s": True}, "interim_interval_s"),
        ({"max_utterance_s": 0}, "max_utterance_s"),
        ({"max_utterance_s": "30"}, "max_utterance_s"),
        ({"stop_timeout_s": 0}, "stop_timeout_s"),
    ],
)
def test_invalid_config_is_rejected(config: dict[str, Any], field: str) -> None:
    with pytest.raises(ValueError, match=field):
        ScriptedBatch(config)
