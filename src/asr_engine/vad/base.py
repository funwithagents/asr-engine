"""``VADModule`` — the interface a voice-activity-detection backend implements.

A VAD module only scores one frame ("how likely is this speech?"). Reframing,
format conversion, hysteresis, pre-roll and the end-of-speech marker live once in
the shared ``VadGate`` (``gate.py``). See specs/vad.md.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

import numpy as np

_REQUIRED_ATTRS = ("SUPPORTED_SAMPLE_RATES", "DEFAULT_SAMPLE_RATE")
_ABSTRACT_METHODS = ("frame_samples", "load", "process_frame", "reset")


def _is_concrete(cls: type) -> bool:
    """True unless one of the interface's methods is still abstract on *cls*.

    Read off the methods directly: ``__abstractmethods__`` is not populated until
    after ``__init_subclass__`` runs.
    """
    return not any(
        getattr(getattr(cls, name, None), "__isabstractmethod__", False)
        for name in _ABSTRACT_METHODS
    )


class VADModule(ABC):
    # Analysis rates the model accepts, and the one the gate resamples to when the
    # pipeline rate is not among them. Every concrete module declares both
    # (enforced below); the gate only ever hands a module mono float32 at one of
    # its own rates, so there is no encoding or channel capability to declare.
    SUPPORTED_SAMPLE_RATES: ClassVar[frozenset[int]]
    DEFAULT_SAMPLE_RATE: ClassVar[int]

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if not _is_concrete(cls):
            return  # abstract intermediate base; its concrete subclasses are checked
        missing = [a for a in _REQUIRED_ATTRS if not hasattr(cls, a)]
        if missing:
            raise TypeError(f"{cls.__name__} must declare: {', '.join(missing)}")
        if cls.DEFAULT_SAMPLE_RATE not in cls.SUPPORTED_SAMPLE_RATES:
            raise TypeError(
                f"{cls.__name__}.DEFAULT_SAMPLE_RATE={cls.DEFAULT_SAMPLE_RATE!r} is "
                f"not in SUPPORTED_SAMPLE_RATES={sorted(cls.SUPPORTED_SAMPLE_RATES)!r}"
            )

    def __init__(self, config: dict) -> None:
        self.config = config  # the engine.vad block minus the gate's own fields

    @abstractmethod
    def frame_samples(self, sample_rate: int) -> int:
        """Samples per ``process_frame`` call at this analysis rate."""

    @abstractmethod
    def load(self) -> None:
        """Load the model. Blocking and idempotent; the model stays resident."""

    @abstractmethod
    def process_frame(self, frame: np.ndarray, sample_rate: int) -> float:
        """Speech probability in [0, 1] for one mono float32 frame of exactly
        ``frame_samples(sample_rate)`` samples in [-1, 1]. A VAD with a yes/no
        answer returns 0.0 or 1.0.

        Synchronous, on the event loop: it must cost well under the frame's
        duration.
        """

    @abstractmethod
    def reset(self) -> None:
        """Clear streaming state (e.g. a recurrent state)."""
