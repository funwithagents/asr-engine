"""Local batch speech-to-text on Apple Silicon through the mlx-audio runner.

Registry key ``mlx-audio``; needs the ``mlx-audio`` extra (Apple Silicon; it
cannot share an environment with ``kyutai-mlx`` / ``kyutai-torch``). A generic runner: any of
mlx-audio's STT model families is a ``model`` value. A batch module
(``BatchASRModule``): the VAD gate ends each utterance. This file imports nothing
third-party; ``runner.py`` does, when the model loads.
See specs/modules/mlx-audio.md.
"""

from __future__ import annotations

import importlib.util
import platform
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable, Protocol

import numpy as np

from asr_engine.errors import ConfigError
from asr_engine.modules.base import ProgressCallback
from asr_engine.modules.batch import BatchASRModule, batch_fields
from asr_engine.modules.fields import Field
from asr_engine.modules.mlx_audio.download import DEFAULT_REPO as DEFAULT_MODEL
from asr_engine.modules.mlx_audio.download import download, is_cached

_LABEL = "mlx-audio"
_NOT_TOGETHER = (
    ". It is for Apple Silicon only, and cannot share an environment with "
    "kyutai-mlx / kyutai-torch."
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


def _is_path(model: str) -> bool:
    """Whether *model* names a local directory rather than a Hub repository."""
    path = Path(model).expanduser()
    return model.startswith(("~", ".")) or path.is_absolute() or path.exists()


class MlxAudioModule(BatchASRModule):
    """Any mlx-audio STT model, decoding each VAD-delimited utterance."""

    LABEL = _LABEL

    kind = "local"
    title = "mlx-audio"
    extra = "mlx-audio"
    fields = (
        Field(
            "model",
            "string",
            "The mlx-audio speech-to-text model: a Hugging Face repository or a "
            "local directory.",
            default=DEFAULT_MODEL,
        ),
        *batch_fields(BatchASRModule.DEFAULT_INTERIM_INTERVAL_S),
    )
    schema_extras = {
        "generate_kwargs": {
            "type": "object",
            "additionalProperties": True,
            "description": "Keyword arguments passed to the model's generate().",
            "x-advanced": True,
        },
    }

    @classmethod
    def config_rules(cls, values: dict[str, Any], config: dict) -> None:
        generate_kwargs = config.get("generate_kwargs", {})
        if not isinstance(generate_kwargs, dict) or not all(
            isinstance(k, str) for k in generate_kwargs
        ):
            raise ConfigError(
                f"{cls.label()}: 'generate_kwargs' must be an object with string keys"
            )

    @classmethod
    def imports(cls, config: dict) -> None:
        hint = cls.install_hint("mlx-audio", note=_NOT_TOGETHER)
        if not _on_apple_silicon() or not _mlx_audio_installed():
            raise ImportError(hint)
        try:
            import mlx_audio  # noqa: F401  # pyright: ignore[reportMissingImports]
        except ImportError as exc:
            raise ImportError(hint) from exc

    @classmethod
    def check_files(cls, config: dict) -> list[dict[str, Any]]:
        model = config.get("model", DEFAULT_MODEL)
        local = _is_path(model)
        ready = Path(model).expanduser().exists() if local else is_cached(model)
        if ready:
            detail = "On this machine." if local else "In the Hugging Face cache."
        elif local:
            detail = f"No model at {model}."
        else:
            detail = "Not downloaded yet: Prepare fetches it, or the first start does."
        return [
            {
                "id": f"mlx-audio:{model}",
                "title": f"Model {model}",
                "status": "ready" if ready else "missing",
                "detail": detail,
                # A repository is downloaded by mlx-audio's loader when the
                # session loads it; a local path only the user can provide.
                "preparable": not local,
                "required": local,
            }
        ]

    @classmethod
    def preparable_items(cls, config: dict) -> list[str]:
        model = config.get("model", DEFAULT_MODEL)
        return [] if _is_path(model) else [f"mlx-audio:{model}"]

    @classmethod
    def prepare(
        cls, config: dict, item: str, on_progress: ProgressCallback | None = None
    ) -> None:
        if item not in cls.preparable_items(config):
            return super().prepare(config, item, on_progress)
        model = config.get("model", DEFAULT_MODEL)
        if not is_cached(model):
            download(model, on_progress=on_progress)
        elif on_progress is not None:
            on_progress({"fraction": 1.0, "message": f"{model} is ready"})

    @classmethod
    def probe(cls, config: dict, secrets: Mapping[str, str] | None = None) -> str:
        values = cls.validated(config)
        cls.imports(config)
        runner = _load_runner(values["model"], dict(config.get("generate_kwargs", {})))
        del runner
        return f"Loaded the model {values['model']} and released it."

    def __init__(self, config: dict, *, load_model: Loader | None = None) -> None:
        super().__init__(config)
        self._model: str = self._values["model"]
        self._generate_kwargs: dict[str, Any] = dict(config.get("generate_kwargs", {}))
        if load_model is None:
            self.imports(config)
            load_model = _load_runner
        self._load_model: Loader = load_model
        self._runner: Transcriber | None = None

    def load(self) -> None:
        self._runner = self._load_model(self._model, dict(self._generate_kwargs))

    def transcribe(self, audio: np.ndarray) -> str:
        assert self._runner is not None, "transcribe() before load()"
        return self._runner.transcribe(audio)
