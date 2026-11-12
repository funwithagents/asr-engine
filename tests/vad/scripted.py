"""Test doubles for the VAD tests: a deterministic ``VADModule`` and a hand-fed
``AudioSource``. Shared by ``tests/vad/`` and ``tests/test_engine.py``."""

from __future__ import annotations

import asyncio

import numpy as np

from asr_engine.audio import AudioFormat
from asr_engine.vad.base import VADModule

FORMAT_16K = AudioFormat(sample_rate=16000)
FRAME_MS = 10  # the scripted VAD's frame: 10 ms at any rate, so durations are round


class ScriptedVAD(VADModule):
    """Scores a frame 1.0 when it is loud (peak above 0.1), else 0.0.

    Deterministic and content-driven, so a test decides where the speech is by
    what it feeds. Records the frames it was given and how often it was loaded
    and reset. Config: ``rates`` (supported analysis rates, default 16 kHz only),
    ``fail_at_frame`` (raise on that ``process_frame`` call, 0-based).
    """

    SUPPORTED_SAMPLE_RATES = frozenset({8000, 16000})
    DEFAULT_SAMPLE_RATE = 16000

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        if "boom" in config:
            raise ValueError("scripted VAD: 'boom' is not a valid field")
        self.fail_at_frame: int | None = config.get("fail_at_frame")
        self.frames: list[np.ndarray] = []
        self.rates: list[int] = []
        self.loads = 0
        self.resets = 0

    def frame_samples(self, sample_rate: int) -> int:
        return sample_rate * FRAME_MS // 1000

    def load(self) -> None:
        self.loads += 1

    def process_frame(self, frame: np.ndarray, sample_rate: int) -> float:
        if self.fail_at_frame == len(self.frames):
            raise RuntimeError("scripted VAD failure")
        self.frames.append(frame.copy())
        self.rates.append(sample_rate)
        return 1.0 if float(np.abs(frame).max()) > 0.1 else 0.0

    def reset(self) -> None:
        self.resets += 1


class Scripted16kVAD(ScriptedVAD):
    """The same, supporting 16 kHz only — forces the gate to resample."""

    SUPPORTED_SAMPLE_RATES = frozenset({16000})


class HandFedSource:
    """An ``AudioSource`` whose queue the test fills by hand."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue[bytes] = asyncio.Queue()
        self.starts = 0
        self.stops = 0

    def start(self) -> asyncio.Queue[bytes]:
        self.starts += 1
        self.queue = asyncio.Queue()
        return self.queue

    def stop(self) -> None:
        self.stops += 1


def loud(ms: float, rate: int = 16000, seed: int = 1) -> np.ndarray:
    """*ms* of loud noise as int16 samples (every 10 ms frame peaks above 0.1)."""
    rng = np.random.default_rng(seed)
    return rng.integers(-16000, 16000, round(rate * ms / 1000)).astype("<i2")


def quiet(ms: float, rate: int = 16000, seed: int = 2) -> np.ndarray:
    """*ms* of faint noise as int16 samples: silence to the VAD, but every byte
    distinct, so a test can tell exactly which stretch was forwarded."""
    rng = np.random.default_rng(seed)
    return rng.integers(-200, 200, round(rate * ms / 1000)).astype("<i2")


def pcm(*parts: np.ndarray) -> bytes:
    return np.concatenate(parts).astype("<i2").tobytes()


async def settle() -> None:
    for _ in range(20):
        await asyncio.sleep(0)
