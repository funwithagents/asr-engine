"""``KyutaiModule`` — the backend-independent half of the ``kyutai`` module.

The model runs in-process: no network, no API key. This file holds every shared
behaviour (re-blocking the audio into 80 ms frames, the worker thread, text
accumulation, finalization, step-budget recycling, backpressure, lifecycle); the
model maths lives behind the ``KyutaiBackend`` protocol (``backend.py``), with an
MLX implementation (``mlx_backend.py``, Apple Silicon) and a PyTorch one
(``torch_backend.py``). This file imports neither ``moshi_mlx`` nor ``moshi`` —
the backend is resolved by name in ``__init__`` and imported on first ``start()``.

See specs/modules/kyutai.md.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import logging
import math
import platform
import queue
import sys
import threading
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from asr_engine.audio import DEFAULT_AUDIO_FORMAT, AudioFormat, EndOfSpeech
from asr_engine.errors import ConfigError
from asr_engine.modules.base import (
    ASRModule,
    AudioQueue,
    ConnectedCallback,
    ProgressCallback,
    SpeechUtterance,
    UtteranceCallback,
)
from asr_engine.modules.fields import Field
from asr_engine.modules.kyutai import download
from asr_engine.modules.kyutai.backend import KyutaiBackend, StepResult

__all__ = ["KyutaiBackend", "KyutaiModule", "StepResult"]

log = logging.getLogger(__name__)

SAMPLE_RATE = 24000
FRAME_SAMPLES = 1920  # one Mimi frame
FRAME_BYTES = FRAME_SAMPLES * 2  # s16 mono
# One model step always consumes exactly one frame, so step counts are an
# audio-time clock: every timer below counts steps, never wall-clock seconds.
STEP_S = FRAME_SAMPLES / SAMPLE_RATE  # 0.08

_BACKENDS = ("mlx", "torch")
_DEVICES = ("auto", "cuda", "mps", "cpu")
# backend name -> (package probed for availability, extra, implementation)
_BACKEND_IMPLS = {
    "mlx": (
        "moshi_mlx",
        "kyutai-mlx",
        "asr_engine.modules.kyutai.mlx_backend:MlxKyutaiBackend",
    ),
    "torch": (
        "moshi",
        "kyutai-torch",
        "asr_engine.modules.kyutai.torch_backend:TorchKyutaiBackend",
    ),
}


BackendFactory = Callable[[], KyutaiBackend]


def _is_importable(package: str) -> bool:
    """Probe for *package* without importing it (nothing heavy at construction)."""
    return importlib.util.find_spec(package) is not None


def _on_apple_silicon() -> bool:
    return sys.platform == "darwin" and platform.machine() == "arm64"


def _install_hint(name: str) -> str:
    _, extra, _ = _BACKEND_IMPLS[name]
    return (
        f"Kyutai module with backend '{name}' requires the optional "
        f"dependency '{extra}' which is not installed. Install it with: "
        f"pip install 'asr-engine[{extra}]' (from source: uv sync --extra {extra}), "
        f"in an environment without mlx-audio or [all]: the two cannot be "
        f'installed together (see specs/project.md "Exclusive extras")'
    )


def _resolve_backend_name(requested: str) -> str:
    """Resolve ``"auto"`` to a concrete backend and check the choice is installed."""
    if requested == "auto":
        # On Apple Silicon, MLX unless only the torch backend is installed — so a
        # Mac with neither gets the kyutai-mlx install hint, not kyutai-torch's.
        name = (
            "mlx"
            if _on_apple_silicon()
            and (_is_importable("moshi_mlx") or not _is_importable("moshi"))
            else "torch"
        )
    else:
        name = requested
    package, _, _ = _BACKEND_IMPLS[name]
    if not _is_importable(package):
        raise ImportError(_install_hint(name))
    return name


class _Recycled:
    """Worker → emit loop: the step budget ran out and the session was reset."""


_RECYCLED = _Recycled()


class _Flushed:
    """Worker → emit loop: every frame of a VAD-gated utterance has been stepped."""


_FLUSHED = _Flushed()
_FLUSH = object()  # feed loop → worker: echo _FLUSHED once the frames before it ran
_RESET = object()  # emit loop → worker: reset the session at this point
_STOP = object()  # module → worker: end the session


class KyutaiModule(ASRModule):
    """Local streaming STT over a ``KyutaiBackend`` (see the module docstring)."""

    SUPPORTED_SAMPLE_RATES = frozenset({SAMPLE_RATE})
    SUPPORTED_CHANNELS = frozenset({1})
    SUPPORTED_ENCODINGS = frozenset({"linear16"})
    DEFAULT_SAMPLE_RATE = SAMPLE_RATE
    DEFAULT_CHANNELS = 1
    DEFAULT_ENCODING = "linear16"
    VAD_GATING = "supported"

    # Retry delays after a failed load/session (specs/asr-module-interface.md
    # "Reconnection Contract"); the last value repeats.
    _BACKOFF_S: tuple[float, ...] = (1.0, 2.0, 4.0, 8.0)
    _LAG_WARN_INTERVAL_S = 5.0
    _STOP_TIMEOUT_S = 10.0

    kind = "local"
    title = "Kyutai"
    # The extra follows the backend and the platform: `required_extras` picks it.
    extra = None
    fields = (
        Field(
            "backend",
            "string",
            "What runs the model: 'mlx' (Apple Silicon), 'torch', or 'auto' to "
            "pick the one for this machine.",
            default="auto",
            choices=("auto", *_BACKENDS),
        ),
        Field(
            "hf_repo",
            "string",
            "The Hugging Face repository of the model.",
            default=download.DEFAULT_REPO,
        ),
        Field(
            "vad",
            "boolean",
            "End each utterance on the model's own end-of-turn detection.",
            default=True,
        ),
        Field(
            "vad_threshold",
            "number",
            "How sure the model must be that a turn ended, strictly between 0 and 1.",
            default=0.5,
            exclusive_minimum=0,
            maximum=1,
            advanced=True,
            message="Kyutai module 'vad_threshold' must be a number between 0 and 1 "
            "(exclusive)",
        ),
        Field(
            "finalize_after_silence_s",
            "number",
            "The final sent after this much silence, in seconds of audio.",
            default=2.0,
            minimum=0,
            advanced=True,
        ),
        Field(
            "max_steps",
            "integer",
            "The model steps (80 ms each) after which the session is reset.",
            default=4096,
            minimum=1,
            advanced=True,
        ),
        Field(
            "device",
            "string",
            "Where the torch backend runs the model.",
            default="auto",
            choices=_DEVICES,
            advanced=True,
        ),
        Field(
            "lag_warn_s",
            "number",
            "Warn when the model runs this far behind the audio, in seconds.",
            default=2.0,
            exclusive_minimum=0,
            advanced=True,
        ),
        Field(
            "lag_drop_s",
            "number",
            "Drop audio when the model runs this far behind, in seconds; above "
            "'lag_warn_s'.",
            default=10.0,
            exclusive_minimum=0,
            advanced=True,
        ),
    )

    @classmethod
    def config_rules(cls, values: dict, config: dict) -> None:
        if values["vad_threshold"] >= 1:
            raise ConfigError(
                "Kyutai module 'vad_threshold' must be a number between 0 and 1 "
                "(exclusive)"
            )
        if values["lag_drop_s"] <= values["lag_warn_s"]:
            raise ConfigError(
                f"Kyutai module 'lag_drop_s' ({values['lag_drop_s']}) must be "
                f"greater than 'lag_warn_s' ({values['lag_warn_s']})"
            )

    @staticmethod
    def _backend_for_extras(config: dict) -> str:
        backend = config.get("backend", "auto")
        if backend in _BACKENDS:
            return backend
        return "mlx" if _on_apple_silicon() else "torch"

    @classmethod
    def required_extras(cls, config: dict) -> list[str]:
        """``kyutai-mlx`` for the MLX backend, ``kyutai-torch`` for torch; with
        ``auto``, the one for this platform (MLX on Apple Silicon)."""
        _, extra, _ = _BACKEND_IMPLS[cls._backend_for_extras(config)]
        return [f"asr-engine[{extra}]"]

    @classmethod
    def imports(cls, config: dict) -> None:
        name = _resolve_backend_name(config.get("backend", "auto"))
        package, _, _ = _BACKEND_IMPLS[name]
        try:
            importlib.import_module(package)
        except ImportError as exc:
            raise ImportError(_install_hint(name)) from exc

    @classmethod
    def check_files(cls, config: dict) -> list[dict]:
        repo = config.get("hf_repo", download.DEFAULT_REPO)
        ready = download.is_cached(repo)
        return [
            {
                "id": f"kyutai:{repo}",
                "title": f"Model {repo}",
                "status": "ready" if ready else "missing",
                "detail": (
                    "In the Hugging Face cache."
                    if ready
                    else "Not downloaded yet: Prepare fetches it."
                ),
                "preparable": not ready,
            }
        ]

    @classmethod
    def prepare(cls, config: dict, on_progress: ProgressCallback | None = None) -> None:
        repo = config.get("hf_repo", download.DEFAULT_REPO)
        if download.is_cached(repo):
            return
        item = f"kyutai:{repo}"
        if on_progress is not None:
            on_progress(
                {"id": item, "fraction": None, "message": f"Downloading {repo}"}
            )
        download.download(repo)
        if on_progress is not None:
            on_progress({"id": item, "fraction": 1.0, "message": f"{repo} is ready"})

    @classmethod
    def probe(cls, config: dict, secrets: Mapping[str, str] | None = None) -> str:
        module = cls(config)
        backend = module._backend_factory()
        try:
            backend.load()
        finally:
            backend.close()
        return f"Loaded the model {module._hf_repo} and released it."

    def __init__(
        self, config: dict, *, backend_factory: BackendFactory | None = None
    ) -> None:
        super().__init__(config)
        values = self.validated(config)
        backend = values["backend"]
        self._hf_repo: str = values["hf_repo"]
        self._vad: bool = values["vad"]
        self._vad_threshold: float = values["vad_threshold"]
        self._finalize_after_silence_s: float = values["finalize_after_silence_s"]
        max_steps: int = values["max_steps"]
        self._max_steps = max_steps
        self._device: str = values["device"]
        self._lag_warn_s: float = values["lag_warn_s"]
        self._lag_drop_s: float = values["lag_drop_s"]

        if backend_factory is None:
            backend_factory = self._factory_for(_resolve_backend_name(backend))
        self._backend_factory = backend_factory

        # Timers, in steps (see STEP_S).
        self._silence_steps = math.ceil(self._finalize_after_silence_s / STEP_S - 1e-9)
        # Recycle the session at a finalization boundary once the budget is this
        # low, so a recycle does not land mid-utterance.
        self._recycle_margin_steps = max(2 * self._silence_steps, max_steps // 10)

        # The backend and its thread outlive stop(): listen() start/stops per call
        # and reloading the model each time would make it unusable.
        self._backend: KyutaiBackend | None = None
        self._executor: ThreadPoolExecutor | None = None
        self._load_future: asyncio.Future[None] | None = None

        # VAD gating: the running session's frame queue, partial-frame buffer and
        # flush length, for finalize().
        self._frames: queue.Queue | None = None
        self._buffer = bytearray()
        self._flush_frames = 0

        self._stop_event = asyncio.Event()
        self._idle = asyncio.Event()  # set whenever start() is not running
        self._idle.set()

    def _factory_for(self, name: str) -> BackendFactory:
        _, _, import_path = _BACKEND_IMPLS[name]

        def factory() -> KyutaiBackend:
            module_name, _, class_name = import_path.partition(":")
            backend_cls = getattr(importlib.import_module(module_name), class_name)
            kwargs: dict = {
                "hf_repo": self._hf_repo,
                "vad": self._vad,
                "max_steps": self._max_steps,
            }
            if name == "torch":
                kwargs["device"] = self._device
            return backend_cls(**kwargs)

        return factory

    # ------------------------------------------------------------------ lifecycle

    async def start(
        self,
        audio_queue: AudioQueue,
        on_utterance: UtteranceCallback,
        on_connected: ConnectedCallback | None = None,
        *,
        audio_format: AudioFormat = DEFAULT_AUDIO_FORMAT,
    ) -> None:
        self._stop_event.clear()
        self._idle.clear()
        attempt = 0
        try:
            while not self._stop_event.is_set():
                try:
                    backend = await self._ensure_loaded(audio_queue)
                    if backend is None:  # stopped while loading
                        break
                    attempt = 0
                    if on_connected:
                        on_connected(True)
                    try:
                        await self._run_session(backend, audio_queue, on_utterance)
                    finally:
                        if on_connected:
                            on_connected(False)
                except Exception as e:
                    if self._stop_event.is_set():
                        break
                    log.error("Kyutai backend error (attempt %d): %s", attempt + 1, e)
                    # The analogue of a dropped socket: discard the backend so the
                    # retry reloads it from scratch.
                    await self._discard_backend()
                    delay = self._BACKOFF_S[min(attempt, len(self._BACKOFF_S) - 1)]
                    attempt += 1
                    await self._drain_queue_for(audio_queue, delay)
        finally:
            self._idle.set()

    async def stop(self) -> None:
        """Stop the session; the loaded model stays resident for the next start()."""
        self._stop_event.set()
        # The engine cancels the start() task right after this returns, so wait
        # for start() to finish its own teardown (worker joined, session reset).
        try:
            await asyncio.wait_for(self._idle.wait(), timeout=self._STOP_TIMEOUT_S)
        except asyncio.TimeoutError:
            log.error("Kyutai module did not stop within %.0f s", self._STOP_TIMEOUT_S)

    async def close(self) -> None:
        """Stop and release the model. The module can be start()ed again (reloads)."""
        await self.stop()
        await self._discard_backend()

    async def _ensure_loaded(self, audio_queue: AudioQueue) -> KyutaiBackend | None:
        """Return the loaded backend, loading it on first use; None if stopped.

        The load runs on the backend thread while ``audio_queue`` is drained, so
        audio captured during a long first-run download is not transcribed late.
        """
        loop = asyncio.get_running_loop()
        if self._backend is None:
            self._backend = self._backend_factory()
            self._executor = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="kyutai"
            )
            self._load_future = None
        backend = self._backend
        if self._load_future is None:
            assert self._executor is not None
            log.info("Loading Kyutai model %s …", self._hf_repo)
            self._load_future = loop.run_in_executor(self._executor, backend.load)
        load_future = self._load_future
        if not load_future.done():
            drain_task = asyncio.create_task(self._drain_forever(audio_queue))
            stop_task = asyncio.create_task(self._stop_event.wait())
            try:
                # wait() never cancels load_future: a load interrupted by stop()
                # or cancellation carries on, and the next start() awaits it.
                await asyncio.wait(
                    {load_future, stop_task}, return_when=asyncio.FIRST_COMPLETED
                )
            finally:
                await _cancel(drain_task, stop_task)
            if not load_future.done():
                return None
        load_future.result()  # raises if the load failed
        if self._vad and not backend.has_vad:
            log.warning(
                "kyutai module: 'vad' is enabled but %s has no VAD heads; "
                "finalizing on the silence timer only",
                self._hf_repo,
            )
        return backend

    async def _discard_backend(self) -> None:
        backend, executor = self._backend, self._executor
        self._backend = self._executor = self._load_future = None
        if backend is None or executor is None:
            return
        try:
            await asyncio.get_running_loop().run_in_executor(executor, backend.close)
        except Exception:
            log.exception("Kyutai backend close() failed")
        executor.shutdown(wait=False)

    # -------------------------------------------------------------------- session

    async def _run_session(
        self,
        backend: KyutaiBackend,
        audio_queue: AudioQueue,
        on_utterance: UtteranceCallback,
    ) -> None:
        """Run feed loop + worker thread + emit loop until stop() or a failure."""
        loop = asyncio.get_running_loop()
        assert self._executor is not None
        frames: queue.Queue = queue.Queue()
        results: asyncio.Queue = asyncio.Queue()
        worker_stop = threading.Event()
        self._frames = frames
        self._buffer = bytearray()
        # The text runs audio_delay_s behind the audio: this many silent frames
        # push the last words of a gated utterance out of the model.
        self._flush_frames = math.ceil(backend.audio_delay_s / STEP_S - 1e-9) + 2

        def deliver(item: object) -> None:
            try:
                loop.call_soon_threadsafe(results.put_nowait, item)
            except RuntimeError:  # event loop closed under us
                worker_stop.set()

        worker = loop.run_in_executor(
            self._executor, self._worker, backend, frames, worker_stop, deliver
        )
        feed_task = asyncio.create_task(self._feed_loop(audio_queue, frames))
        emit_task = asyncio.create_task(
            self._emit_loop(backend, results, frames, on_utterance)
        )
        try:
            done, _ = await asyncio.wait(
                {feed_task, emit_task}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                task.result()  # re-raise a session failure
        finally:
            self._frames = None
            await _cancel(feed_task, emit_task)
            worker_stop.set()
            frames.put(_STOP)
            try:
                await asyncio.wait_for(
                    asyncio.shield(worker), timeout=self._STOP_TIMEOUT_S
                )
            except asyncio.TimeoutError:
                log.error("Kyutai worker thread did not stop in time")
            except Exception:
                pass  # already reported through the results queue

    def _worker(
        self,
        backend: KyutaiBackend,
        frames: queue.Queue,
        stop: threading.Event,
        deliver: Callable[[object], None],
    ) -> None:
        """Backend thread: frames in, ``(StepResult, steps_remaining)`` out.

        Leaves the backend reset, so every session starts fresh.
        """
        prefix_frames = round(backend.silence_prefix_s / STEP_S)
        silence = np.zeros(FRAME_SAMPLES, dtype=np.float32)

        def prime() -> None:
            for _ in range(prefix_frames):
                backend.step(silence)

        try:
            prime()
            while not stop.is_set():
                try:
                    item = frames.get(timeout=0.1)
                except queue.Empty:
                    continue
                if item is _STOP:
                    break
                if item is _RESET:
                    backend.reset()
                    prime()
                    continue
                if item is _FLUSH:
                    deliver(_FLUSHED)
                    continue
                if backend.steps_remaining == 0:
                    # Budget exhausted with an utterance still open: tell the emit
                    # loop to force-finalize, then carry on in a fresh session.
                    backend.reset()
                    prime()
                    deliver(_RECYCLED)
                result = backend.step(item)
                deliver((result, backend.steps_remaining))
        except Exception as exc:
            deliver(exc)
        finally:
            try:
                backend.reset()
            except Exception:
                log.exception("Kyutai backend reset() failed")

    async def _feed_loop(self, audio_queue: AudioQueue, frames: queue.Queue) -> None:
        """Re-block s16 chunks into float32 model frames; apply backpressure."""
        loop = asyncio.get_running_loop()
        buffer = self._buffer
        last_warn = -math.inf
        while True:
            chunk = await self._get_or_stop(audio_queue)
            if chunk is None:
                return
            if isinstance(chunk, EndOfSpeech):
                await self.finalize()
                continue
            buffer.extend(chunk)
            while len(buffer) >= FRAME_BYTES:
                pcm = np.frombuffer(bytes(buffer[:FRAME_BYTES]), dtype="<i2")
                del buffer[:FRAME_BYTES]
                lag_s = frames.qsize() * STEP_S
                if lag_s > self._lag_drop_s:
                    dropped = self._drop_oldest(frames)
                    log.error(
                        "Kyutai model is %.1f s behind real time; dropped %.1f s "
                        "of audio",
                        lag_s,
                        dropped * STEP_S,
                    )
                elif lag_s > self._lag_warn_s:
                    now = loop.time()
                    if now - last_warn >= self._LAG_WARN_INTERVAL_S:
                        last_warn = now
                        log.warning("Kyutai model is %.1f s behind real time", lag_s)
                frames.put(pcm.astype(np.float32) / 32768.0)

    async def finalize(self) -> None:
        """End of a VAD-gated utterance: flush the model and force a final.

        The module's timers count model steps, and a closed gate means no audio,
        hence no steps. So: pad the partial frame with zeros, feed enough silent
        frames to push out the text still behind the model's delay, then a marker
        the worker echoes to the emit loop, which finalizes what has accumulated.
        See specs/vad.md "Per-module finalize()".
        """
        frames = self._frames
        if frames is None:
            return
        if self._buffer:
            self._buffer.extend(bytes(FRAME_BYTES - len(self._buffer)))
            pcm = np.frombuffer(bytes(self._buffer), dtype="<i2")
            self._buffer.clear()
            frames.put(pcm.astype(np.float32) / 32768.0)
        silence = np.zeros(FRAME_SAMPLES, dtype=np.float32)
        for _ in range(self._flush_frames):
            frames.put(silence)
        frames.put(_FLUSH)

    def _drop_oldest(self, frames: queue.Queue) -> int:
        """Drop the oldest queued frames until lag is back to ``lag_warn_s``."""
        dropped = 0
        control: list[object] = []
        while frames.qsize() * STEP_S > self._lag_warn_s:
            try:
                item = frames.get_nowait()
            except queue.Empty:
                break
            if isinstance(item, np.ndarray):
                dropped += 1
            else:
                control.append(item)
        for item in control:
            frames.put(item)
        return dropped

    async def _emit_loop(
        self,
        backend: KyutaiBackend,
        results: asyncio.Queue,
        frames: queue.Queue,
        on_utterance: UtteranceCallback,
    ) -> None:
        """Accumulate step results into interim/final utterances."""
        use_vad = self._vad and backend.has_vad
        # The VAD head tracks the audio while the text runs audio_delay_s behind
        # it, so a final is emitted this many steps after the rising edge — once
        # the words spoken before the edge have all arrived.
        delay_steps = math.ceil(backend.audio_delay_s / STEP_S - 1e-9)

        # With nothing accumulated, this long without text counts as idle.
        idle_steps = max(self._silence_steps, delay_steps, 1)

        text = ""
        steps_since_text = 0
        vad_high = False
        final_in_steps: int | None = None  # countdown armed by a VAD rising edge
        final_confidence: float | None = None
        reset_requested = False

        async def emit(is_final: bool, confidence: float | None) -> None:
            transcript = text.strip()
            if not transcript:
                return
            try:
                await on_utterance(SpeechUtterance(transcript, is_final, confidence))
            except Exception:
                log.exception("Kyutai on_utterance callback failed")

        while True:
            item = await self._get_or_stop(results)
            if item is None:
                return
            if isinstance(item, Exception):
                raise item
            if item is _RECYCLED:
                if text.strip():
                    log.warning(
                        "Kyutai step budget exhausted mid-utterance; "
                        "force-finalizing and recycling the session"
                    )
                await emit(True, None)
                text, steps_since_text = "", 0
                vad_high, final_in_steps, final_confidence = False, None, None
                reset_requested = False
                continue
            if item is _FLUSHED:
                # A VAD-gated utterance ended: finalize what has accumulated. A
                # final the semantic VAD emitted in the meantime left nothing here.
                await emit(True, None)
                text, steps_since_text = "", 0
                vad_high, final_in_steps, final_confidence = False, None, None
                continue

            result, steps_remaining = item
            if result.text is not None:
                text += result.text
                steps_since_text = 0
                await emit(False, None)
            else:
                steps_since_text += 1

            finalize = False
            if use_vad and result.end_of_turn_p is not None:
                if result.end_of_turn_p > self._vad_threshold:
                    if not vad_high:  # rising edge only
                        vad_high = True
                        if final_in_steps is None:
                            final_in_steps = delay_steps
                            final_confidence = result.end_of_turn_p
                else:
                    vad_high = False
            if final_in_steps is not None:
                if final_in_steps <= 0:
                    finalize = True
                else:
                    final_in_steps -= 1
            confidence = final_confidence if finalize else None
            if not finalize and text and steps_since_text >= self._silence_steps:
                finalize = True

            if finalize:
                await emit(True, confidence)
                text, steps_since_text = "", 0
                final_in_steps, final_confidence = None, None
            elif text or steps_since_text < idle_steps:
                continue  # mid-utterance: not a safe point to recycle

            # At a boundary (just finalized, or idle): recycle a nearly-spent
            # session here so the reset never lands mid-utterance.
            if steps_remaining is None:
                continue
            if steps_remaining > self._recycle_margin_steps:
                reset_requested = False
            elif not reset_requested:
                log.info("Kyutai step budget low; recycling the session")
                reset_requested = True
                frames.put(_RESET)

    # -------------------------------------------------------------------- helpers

    async def _get_or_stop(self, source: asyncio.Queue):
        """Wait for the next item, or return None once stop() is called."""
        get_task = asyncio.ensure_future(source.get())
        stop_task = asyncio.ensure_future(self._stop_event.wait())
        try:
            await asyncio.wait(
                {get_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            for task in (get_task, stop_task):
                if not task.done():
                    task.cancel()
        if self._stop_event.is_set():
            return None
        if get_task.done() and not get_task.cancelled():
            return get_task.result()
        return None

    @staticmethod
    async def _drain_forever(audio_queue: AudioQueue) -> None:
        while True:
            await audio_queue.get()

    async def _drain_queue_for(self, audio_queue: AudioQueue, duration: float) -> None:
        """Drain audio_queue for *duration* seconds (or until stop()).

        End-of-speech markers are discarded with the audio they follow."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + duration
        while not self._stop_event.is_set():
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            try:
                await asyncio.wait_for(audio_queue.get(), timeout=min(remaining, 0.1))
            except asyncio.TimeoutError:
                pass


async def _cancel(*tasks: asyncio.Task) -> None:
    for task in tasks:
        task.cancel()
    for task in tasks:
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
