"""Tests for the scripted fake ASR module (specs/modules/fake.md)."""

from __future__ import annotations

import asyncio

import pytest

from asr_engine.audio import AudioFormat
from asr_engine.modules.base import SpeechUtterance
from asr_engine.modules.fake import FakeASRModule

LINEAR16_16K = AudioFormat(sample_rate=16000, encoding="linear16")  # 32000 B/s


async def _settle() -> None:
    """Let the module task consume what was fed and emit what is due."""
    for _ in range(20):
        await asyncio.sleep(0)


class _Harness:
    """Runs a FakeASRModule on a hand-fed queue, recording what it emits.

    Each event is recorded with the audio time fed so far, so assertions pin when
    (on the audio clock) an utterance came out.
    """

    def __init__(self, config: dict, audio_format: AudioFormat = LINEAR16_16K) -> None:
        self.module = FakeASRModule(config)
        self.audio_format = audio_format
        self.queue: asyncio.Queue[bytes] = asyncio.Queue()
        self.fed_s = 0.0
        self.events: list[tuple[float, str, bool, float | None]] = []
        self.connected: list[bool] = []
        self.task: asyncio.Task | None = None

    async def _on_utterance(self, u: SpeechUtterance) -> None:
        self.events.append(
            (round(self.fed_s, 6), u.transcript, u.is_final, u.confidence)
        )

    async def start(self) -> None:
        self.fed_s = 0.0
        self.task = asyncio.create_task(
            self.module.start(
                self.queue,
                self._on_utterance,
                self.connected.append,
                audio_format=self.audio_format,
            )
        )
        await _settle()

    async def feed(self, seconds: float) -> None:
        fmt = self.audio_format
        n_bytes = round(seconds * fmt.sample_rate) * fmt.channels * fmt.bytes_per_sample
        self.fed_s += seconds
        await self.queue.put(b"\x00" * n_bytes)
        await _settle()

    async def stop(self) -> None:
        await self.module.stop()
        assert self.task is not None
        await asyncio.wait_for(self.task, timeout=1.0)


async def test_emits_word_by_word_interims_then_final_on_schedule():
    h = _Harness(
        {
            "utterances": [
                {
                    "text": "the sky is blue",
                    "start_s": 1.0,
                    "end_s": 2.0,
                    "confidence": 0.9,
                },
                {"text": "Hello!", "start_s": 2.5, "end_s": 3.0},
            ]
        }
    )
    await h.start()
    for _ in range(12):  # 0.25 s steps up to 3.0 s
        await h.feed(0.25)
    await h.stop()

    assert h.events == [
        (1.25, "the", False, 0.9),
        (1.5, "the sky", False, 0.9),
        (1.75, "the sky is", False, 0.9),
        (2.0, "the sky is blue", True, 0.9),
        # A one-word utterance emits only its final, verbatim.
        (3.0, "Hello!", True, None),
    ]


async def test_events_due_within_one_chunk_emit_in_order_and_never_early():
    h = _Harness({"utterances": [{"text": "a b c d", "start_s": 1.0, "end_s": 2.0}]})
    await h.start()
    await h.feed(1.2)  # only "a" (due at 1.25) is not yet reached
    assert h.events == []
    await h.feed(0.8)  # one chunk crosses every remaining event
    await h.stop()

    assert [(e[1], e[2]) for e in h.events] == [
        ("a", False),
        ("a b", False),
        ("a b c", False),
        ("a b c d", True),
    ]


@pytest.mark.parametrize("encoding", ["linear16", "mulaw"])
async def test_clock_counts_audio_time_for_any_encoding(encoding):
    h = _Harness(
        {"utterances": [{"text": "one two", "start_s": 0.0, "end_s": 1.0}]},
        audio_format=AudioFormat(sample_rate=8000, encoding=encoding),
    )
    await h.start()
    await h.feed(0.5)
    await h.stop()

    assert h.events == [(0.5, "one", False, None)]


async def test_restart_replays_script_and_reports_connection_state():
    h = _Harness({"utterances": [{"text": "hi", "start_s": 0.0, "end_s": 0.1}]})
    await h.start()
    assert h.connected == [True]
    await h.feed(0.1)
    await h.stop()  # returns promptly while blocked on an empty queue
    assert h.connected == [True, False]

    await h.start()
    await h.feed(0.1)
    await h.stop()

    assert [e[1] for e in h.events] == ["hi", "hi"]
    assert h.connected == [True, False, True, False]


async def test_empty_script_emits_nothing():
    h = _Harness({})
    await h.start()
    await h.feed(5.0)
    await h.stop()
    assert h.events == []


@pytest.mark.parametrize(
    ("utterances", "message"),
    [
        ("nope", "'utterances' must be a list"),
        (["nope"], r"utterances\[0\] must be an object"),
        ([{"start_s": 0, "end_s": 1}], r"utterances\[0\]\.text is required"),
        (
            [{"text": "  ", "start_s": 0, "end_s": 1}],
            r"utterances\[0\]\.text is required",
        ),
        ([{"text": "a", "end_s": 1}], r"utterances\[0\]\.start_s is required"),
        (
            [{"text": "a", "start_s": 0, "end_s": "1"}],
            r"utterances\[0\]\.end_s is required",
        ),
        ([{"text": "a", "start_s": -0.1, "end_s": 1}], "start_s must be >= 0"),
        ([{"text": "a", "start_s": 1, "end_s": 1}], "must be greater than start_s"),
        (
            [
                {"text": "a", "start_s": 0, "end_s": 1},
                {"text": "b", "start_s": 0.5, "end_s": 2},
            ],
            r"utterances\[1\] starts at 0.5, before the previous utterance ends",
        ),
    ],
    ids=[
        "not-a-list",
        "entry-not-object",
        "missing-text",
        "blank-text",
        "missing-start",
        "non-numeric-end",
        "negative-start",
        "empty-window",
        "overlap",
    ],
)
def test_invalid_script_rejected(utterances, message):
    with pytest.raises(ValueError, match=message):
        FakeASRModule({"utterances": utterances})


def test_touching_utterances_are_allowed():
    FakeASRModule(
        {
            "utterances": [
                {"text": "a", "start_s": 0, "end_s": 1},
                {"text": "b", "start_s": 1, "end_s": 2},
            ]
        }
    )
