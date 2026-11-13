"""Local batch speech-to-text on Apple Silicon through the mlx-audio runner.

Registry key ``mlx_audio``; needs the ``mlx-audio`` extra, which cannot share an
environment with ``kyutai-mlx`` / ``kyutai-torch``. A generic runner: any of
mlx-audio's STT model families is a ``model`` value. A batch module
(``BatchASRModule``): the VAD gate ends each utterance. This file imports nothing
third-party; ``runner.py`` does, when the model loads.
See specs/modules/mlx-audio.md.
"""

from __future__ import annotations

import importlib.util
import platform
import sys
from typing import Any, Callable, Protocol

import numpy as np

from asr_engine.modules.batch import BatchASRModule

_LABEL = "mlx_audio"
DEFAULT_MODEL = "mlx-community/parakeet-tdt-0.6b-v3"
_INSTALL_HINT = (
    f"ASR module '{_LABEL}' requires the optional dependency 'mlx-audio' (Apple "
    f"Silicon only), which is not installed. It cannot share an environment with "
    f"kyutai-mlx / kyutai-torch: install it with pip install 'asr-engine[mlx-audio]' "
    f"in its own environment (see specs/modules/mlx-audio.md)."
)


class Transcriber(Protocol):
    def transcribe(self, audio: np.ndarray) -> str: ...


Loader = Callable[[str, dict[str, Any]], Transcriber]


def _on_apple_silicon() -> bool:
    return sys.platform == "darwin" and platform.machine() == "arm64"


def _mlx_audio_installed() -> bool:
    return importlib.util.find_spec("mlx_audio") is not None


def _load_runner(model: str, generate_kwargs: dict[str, Any]) -> Transcriber:
    from asr_engine.modules.mlx_audio.runner import MlxAudioRunner

    return MlxAudioRunner.load(model, generate_kwargs)


class MlxAudioModule(BatchASRModule):
    """Any mlx-audio STT model, decoding each VAD-delimited utterance."""

    LABEL = _LABEL

    def __init__(self, config: dict, *, load_model: Loader | None = None) -> None:
        super().__init__(config)
        self._model = config.get("model", DEFAULT_MODEL)
        if not isinstance(self._model, str) or not self._model:
            raise ValueError(f"{_LABEL} module: 'model' must be a non-empty string")
        generate_kwargs = config.get("generate_kwargs", {})
        if not isinstance(generate_kwargs, dict) or not all(
            isinstance(k, str) for k in generate_kwargs
        ):
            raise ValueError(
                f"{_LABEL} module: 'generate_kwargs' must be an object with string keys"
            )
        self._generate_kwargs: dict[str, Any] = dict(generate_kwargs)
        if load_model is None:
            if not _on_apple_silicon() or not _mlx_audio_installed():
                raise ImportError(_INSTALL_HINT)
            load_model = _load_runner
        self._load_model: Loader = load_model
        self._runner: Transcriber | None = None

    def load(self) -> None:
        self._runner = self._load_model(self._model, dict(self._generate_kwargs))

    def transcribe(self, audio: np.ndarray) -> str:
        assert self._runner is not None, "transcribe() before load()"
        return self._runner.transcribe(audio)
