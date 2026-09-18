"""The backend seam of the ``kyutai`` module: ``KyutaiBackend`` and ``StepResult``.

It is the MLX/PyTorch split, the place a scripted fake backend is injected for
the fast test tier, and the boundary past which ``moshi*`` may be imported. This
file itself imports no model package. See specs/modules/kyutai.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Protocol

import numpy as np


@dataclass(frozen=True)
class StepResult:
    """What one model step produced."""

    text: str | None  # decoded piece; None for padding tokens (0, 3)
    end_of_turn_p: float | None  # VAD head probability; None without VAD heads


class KyutaiBackend(Protocol):
    """One loaded model instance. Synchronous and blocking by design — the module
    owns the worker thread, the backend owns only the maths. The module makes
    every call from that one thread, so a backend needs no locking."""

    SAMPLE_RATE: ClassVar[int]  # 24000
    FRAME_SAMPLES: ClassVar[int]  # 1920 (80 ms)

    @property
    def has_vad(self) -> bool:
        """Whether ``step()`` reports ``end_of_turn_p``. Valid after ``load()``."""
        ...

    @property
    def steps_remaining(self) -> int | None:
        """Steps left before the session must be ``reset()``; None = unbounded."""
        ...

    @property
    def audio_delay_s(self) -> float:
        """How far the text runs behind the audio (``stt_config``). After ``load()``."""
        ...

    @property
    def silence_prefix_s(self) -> float:
        """Silence the model wants before real audio (``stt_config``). After ``load()``."""
        ...

    def load(self) -> None:
        """Download (if needed), load weights, warm up. Seconds to minutes."""
        ...

    def step(self, frame: np.ndarray) -> StepResult:
        """One FRAME_SAMPLES float32 frame in, one StepResult out."""
        ...

    def reset(self) -> None:
        """Start a fresh streaming session; keeps the weights resident."""
        ...

    def close(self) -> None: ...
