"""``SileroVAD`` — Silero VAD run directly on ``onnxruntime``.

The ``silero-vad`` PyPI package depends on ``torch``; this module runs upstream's
ONNX model with ``onnxruntime`` and numpy instead, porting the state handling of
upstream's ``OnnxWrapper`` (``silero_vad/utils_vad.py``): a recurrent state tensor
carried between calls, and the last samples of the previous frame prepended to
each input as context.

The model file is not shipped with the package. ``load()`` downloads it once from
a pinned upstream tag into the user cache directory and checks its SHA-256;
``engine.vad.model_path`` points at a local copy instead (offline machines).

Needs the ``silero`` extra. See specs/vad.md "Bundled module: silero".
"""

from __future__ import annotations

import hashlib
import logging
import os
import sys
import tempfile
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from asr_engine.errors import ConfigError
from asr_engine.modules.base import ProgressCallback
from asr_engine.modules.downloads import ByteProgress
from asr_engine.modules.fields import Field
from asr_engine.vad.base import VADModule

if TYPE_CHECKING:
    import onnxruntime

log = logging.getLogger(__name__)

# The model, pinned: upstream tag, file, and the hash of that exact file. To
# update, change all three together and check upstream's OnnxWrapper for a change
# in the model's inputs, frame size or context handling, which this module ports.
MODEL_TAG = "v6.2.3"
MODEL_URL = (
    "https://raw.githubusercontent.com/snakers4/silero-vad/"
    f"{MODEL_TAG}/src/silero_vad/data/silero_vad.onnx"
)
MODEL_SHA256 = "1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3"
MODEL_FILENAME = f"silero_vad-{MODEL_TAG}.onnx"
CACHE_DIR_ENV = "ASR_ENGINE_CACHE_DIR"
_DOWNLOAD_TIMEOUT_S = 30.0

# Per analysis rate: (samples per frame, context samples carried from the last frame).
_FRAMING = {16000: (512, 64), 8000: (256, 32)}
_STATE_SHAPE = (2, 1, 128)
_CONFIG_FIELDS = ("model_path",)
_MODEL_ITEM = f"silero:{MODEL_FILENAME}"


def cache_dir() -> Path:
    """Where downloaded models are kept: ``$ASR_ENGINE_CACHE_DIR`` when set, else
    the user cache directory (``%LOCALAPPDATA%`` on Windows, ``$XDG_CACHE_HOME``
    or ``~/.cache`` elsewhere) under ``asr-engine``."""
    override = os.environ.get(CACHE_DIR_ENV)
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    else:
        base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "asr-engine"


def cached_model() -> Path | None:
    """The cached model file when it is there and its hash matches, else None."""
    target = cache_dir() / MODEL_FILENAME
    if target.is_file() and _sha256(target) == MODEL_SHA256:
        return target
    return None


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ensure_model(on_progress: ProgressCallback | None = None) -> Path:
    """Return the cached model file, downloading and verifying it when needed,
    the bytes received reported to *on_progress* against the size the server
    announces (specs/setup.md "Progress").

    A cached file whose hash does not match (truncated, tampered with) is
    downloaded again. The download goes to a temporary file and is moved into
    place only once its hash is verified, so a failed or interrupted download
    never leaves a bad file behind. Raises ``RuntimeError`` naming the
    ``model_path`` way out when the model cannot be obtained.
    """
    target = cache_dir() / MODEL_FILENAME
    if target.is_file() and _sha256(target) == MODEL_SHA256:
        return target

    log.info("Downloading the Silero VAD model (%s) to %s", MODEL_TAG, target)
    hint = (
        f"Download {MODEL_URL} by hand and set engine.vad.model_path to the file "
        f"to run without network access."
    )
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=target.parent, suffix=".part")
        tmp = Path(tmp_name)
        try:
            with (
                os.fdopen(fd, "wb") as out,
                urllib.request.urlopen(MODEL_URL, timeout=_DOWNLOAD_TIMEOUT_S) as r,
            ):
                length = r.headers.get("Content-Length")
                progress = ByteProgress(
                    on_progress,
                    "the Silero VAD model",
                    int(length) if length and length.isdigit() else None,
                )
                progress.start()
                while block := r.read(1 << 16):
                    out.write(block)
                    progress.add(len(block))
            digest = _sha256(tmp)
            if digest != MODEL_SHA256:
                raise RuntimeError(
                    f"silero VAD: the downloaded model does not match the expected "
                    f"SHA-256 (got {digest}); it was not installed. {hint}"
                )
            os.replace(tmp, target)
            progress.finish()
        finally:
            tmp.unlink(missing_ok=True)
    except OSError as exc:  # no network, HTTP error, unwritable cache directory
        raise RuntimeError(
            f"silero VAD: could not download the model to {target}: {exc}. {hint}"
        ) from exc
    return target


class SileroVAD(VADModule):
    SUPPORTED_SAMPLE_RATES = frozenset(_FRAMING)
    DEFAULT_SAMPLE_RATE = 16000

    kind = "local"
    title = "Silero"
    extra = "silero"
    fields = (
        Field(
            "model_path",
            "string",
            "A local copy of the Silero model file (silero_vad.onnx), for a machine "
            "without network. Absent, it is downloaded once into the cache.",
            advanced=True,
        ),
    )

    @classmethod
    def config_rules(cls, values: dict[str, Any], config: dict) -> None:
        unknown = sorted(set(config) - set(_CONFIG_FIELDS))
        if unknown:
            raise ConfigError(
                f"Silero VAD: unknown engine.vad field(s) {unknown}; beyond type, "
                f"threshold, min_speech_duration_ms, min_silence_duration_ms and "
                f"speech_pad_ms it takes only model_path"
            )

    @classmethod
    def imports(cls, config: dict) -> None:
        try:
            import onnxruntime  # noqa: F401
        except ImportError as exc:
            raise ImportError(cls.install_hint("silero")) from exc

    @classmethod
    def check_files(cls, config: dict) -> list[dict[str, Any]]:
        model_path = config.get("model_path")
        if model_path:
            path = Path(model_path).expanduser()
            present = path.is_file()
            return [
                {
                    "id": f"silero:{model_path}",
                    "title": "Silero VAD model",
                    "status": "ready" if present else "missing",
                    "detail": "On this machine." if present else f"No file at {path}.",
                    "preparable": False,
                    "required": True,
                }
            ]
        cached = cached_model()
        return [
            {
                "id": _MODEL_ITEM,
                "title": "Silero VAD model",
                "status": "ready" if cached else "missing",
                "detail": (
                    f"In {cached}."
                    if cached
                    else "Not downloaded yet (about 2 MB): Prepare fetches it, or "
                    "the first start does."
                ),
                # `load()` downloads it when the session starts.
                "preparable": True,
                "required": False,
            }
        ]

    @classmethod
    def preparable_items(cls, config: dict) -> list[str]:
        return [] if config.get("model_path") else [_MODEL_ITEM]

    @classmethod
    def prepare(
        cls, config: dict, item: str, on_progress: ProgressCallback | None = None
    ) -> None:
        if item not in cls.preparable_items(config):
            return super().prepare(config, item, on_progress)
        if cached_model() is None:
            ensure_model(on_progress)
        elif on_progress is not None:
            on_progress({"fraction": 1.0, "message": "the Silero VAD model is ready"})

    @classmethod
    def probe(cls, config: dict, secrets: Mapping[str, str] | None = None) -> str:
        vad = cls(config)
        vad.load()
        vad._session = None
        return "Loaded the Silero VAD model and released it."

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        values = self.validated(config)
        self.imports(config)
        model_path = values["model_path"]
        self._model_path = Path(model_path).expanduser() if model_path else None
        self._session: onnxruntime.InferenceSession | None = None
        self._state = np.zeros(_STATE_SHAPE, dtype=np.float32)
        self._context: np.ndarray | None = None
        self._context_rate = 0

    def frame_samples(self, sample_rate: int) -> int:
        return _FRAMING[sample_rate][0]

    def load(self) -> None:
        import onnxruntime

        if self._session is not None:
            return
        if self._model_path is not None:
            if not self._model_path.is_file():
                raise FileNotFoundError(
                    f"silero VAD: engine.vad.model_path does not exist: "
                    f"{self._model_path}"
                )
            path = self._model_path
        else:
            path = ensure_model()
        options = onnxruntime.SessionOptions()
        # The model is tiny and runs on the event loop's thread: no thread pools.
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        self._session = onnxruntime.InferenceSession(
            str(path), sess_options=options, providers=["CPUExecutionProvider"]
        )

    def process_frame(self, frame: np.ndarray, sample_rate: int) -> float:
        if self._session is None:
            raise RuntimeError("SileroVAD.process_frame() called before load()")
        samples, context_samples = _FRAMING[sample_rate]
        if frame.shape != (samples,):
            raise ValueError(
                f"silero VAD: expected a frame of {samples} samples at "
                f"{sample_rate} Hz, got shape {frame.shape}"
            )
        if self._context is None or self._context_rate != sample_rate:
            # First frame, or the rate changed: start from a clean state.
            self._state = np.zeros(_STATE_SHAPE, dtype=np.float32)
            self._context = np.zeros(context_samples, dtype=np.float32)
            self._context_rate = sample_rate
        window = np.concatenate([self._context, frame.astype(np.float32, copy=False)])
        output, state = self._session.run(
            None,
            {
                "input": window[np.newaxis, :],
                "state": self._state,
                "sr": np.array(sample_rate, dtype=np.int64),
            },
        )
        self._state = np.asarray(state, dtype=np.float32)
        self._context = window[-context_samples:]
        return float(np.asarray(output).reshape(-1)[0])

    def reset(self) -> None:
        self._state = np.zeros(_STATE_SHAPE, dtype=np.float32)
        self._context = None
