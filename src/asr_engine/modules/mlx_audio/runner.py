"""The only code that touches mlx-audio (the ``mlx-audio`` extra, Apple Silicon).

Only ``module.py`` imports this file, and only from the worker thread when the
model loads. See specs/modules/mlx-audio.md "Runner".
"""

# mlx / mlx-audio publish no Linux or Windows wheels, so these imports don't
# resolve there (CI's static gate runs on Linux); pyright checks the rest.
# pyright: reportMissingImports=false, reportAttributeAccessIssue=false

from __future__ import annotations

from typing import Any

import mlx.core as mx
import numpy as np
from mlx_audio.stt.utils import load_model

_WARMUP_S = 1.0
_SAMPLE_RATE = 16000


class MlxAudioRunner:
    """One loaded mlx-audio STT model. Not thread-safe: the module calls it from
    its single worker thread only."""

    def __init__(self, model: Any, generate_kwargs: dict[str, Any]) -> None:
        self._model = model
        self._generate_kwargs = generate_kwargs

    @classmethod
    def load(cls, model: str, generate_kwargs: dict[str, Any]) -> MlxAudioRunner:
        """Load *model* and warm it up: the first decode compiles the graph
        (measured 2.7 s for Parakeet), so it is paid here rather than on the
        first utterance. A wrong ``generate_kwargs`` key fails here too."""
        runner = cls(load_model(model), generate_kwargs)
        runner.transcribe(np.zeros(int(_SAMPLE_RATE * _WARMUP_S), dtype=np.float32))
        return runner

    def transcribe(self, audio: np.ndarray) -> str:
        # mx.array is the one input type every family accepts (Parakeet rejects
        # NumPy arrays).
        result = self._model.generate(mx.array(audio), **self._generate_kwargs)
        text = getattr(result, "text", "")
        return text.strip() if isinstance(text, str) else ""
