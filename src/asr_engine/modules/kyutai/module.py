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
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from asr_engine.audio import DEFAULT_AUDIO_FORMAT, AudioFormat
from asr_engine.modules.base import (
    ASRModule,
    ConnectedCallback,
    SpeechUtterance,
    UtteranceCallback,
)
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


def _resolve_backend_name(requested: str) -> str:
    """Resolve ``"auto"`` to a concrete backend and check the choice is installed."""
    if requested == "auto":
        on_apple_silicon = sys.platform == "darwin" and platform.machine() == "arm64"
        name = "mlx" if on_apple_silicon and _is_importable("moshi_mlx") else "torch"
    else:
        name = requested
    package, extra, _ = _BACKEND_IMPLS[name]
    if not _is_importable(package):
        raise ImportError(
            f"ASR module 'kyutai' with backend '{name}' requires the optional "
            f"dependency '{extra}' which is not installed. Install it with: "
            f"pip install 'asr-engine[{extra}]' (from source: uv sync --extra {extra})"
        )
    return name


def _number(config: dict, key: str, default: float) -> float:
    value = config.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"kyutai module: '{key}' must be a number, got {value!r}")
    return float(value)


class _Recycled:
    """Worker → emit loop: the step budget ran out and the session was reset."""


_RECYCLED = _Recycled()
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

    # Retry delays after a failed load/session (specs/asr-module-interface.md
    # "Reconnection Contract"); the last value repeats.
    _BACKOFF_S: tuple[float, ...] = (1.0, 2.0, 4.0, 8.0)
    _LAG_WARN_INTERVAL_S = 5.0
    _STOP_TIMEOUT_S = 10.0

    def __init__(
        self, config: dict, *, backend_factory: BackendFactory | None = None
    ) -> None:
        super().__init__(config)
        backend = config.get("backend", "auto")
        if backend != "auto" and backend not in _BACKENDS:
            raise ValueError(
                f"kyutai module: unknown backend {backend!r}; "
                f"expected 'auto', 'mlx' or 'torch'"
            )
        self._hf_repo = config.get("hf_repo", "kyutai/stt-1b-en_fr-candle")
        if not isinstance(self._hf_repo, str) or not self._hf_repo:
            raise ValueError("kyutai module: 'hf_repo' must be a non-empty string")
        self._vad = config.get("vad", True)
        if not isinstance(self._vad, bool):
            raise ValueError("kyutai module: 'vad' must be a boolean")
        self._vad_threshold = _number(config, "vad_threshold", 0.5)
        if not 0.0 < self._vad_threshold < 1.0:
            raise ValueError(
                f"kyutai module: 'vad_threshold' must be between 0 and 1 "
                f"(exclusive), got {self._vad_threshold}"
            )
        self._finalize_after_silence_s = _number(
            config, "finalize_after_silence_s", 2.0
        )
        if self._finalize_after_silence_s < 0:
            raise ValueError(
                f"kyutai module: 'finalize_after_silence_s' must be >= 0, "
                f"got {self._finalize_after_silence_s}"
            )
        max_steps = config.get("max_steps", 4096)
        if isinstance(max_steps, bool) or not isinstance(max_steps, int):
            raise ValueError(
                f"kyutai module: 'max_steps' must be an integer, got {max_steps!r}"
            )
        if max_steps <= 0:
            raise ValueError(f"kyutai module: 'max_steps' must be > 0, got {max_steps}")
        self._max_steps = max_steps
        self._device = config.get("device", "auto")
        if self._device not in _DEVICES:
            raise ValueError(
                f"kyutai module: unknown device {self._device!r}; "
                f"expected one of {', '.join(_DEVICES)}"
            )
        self._lag_warn_s = _number(config, "lag_warn_s", 2.0)
        self._lag_drop_s = _number(config, "lag_drop_s", 10.0)
        if self._lag_warn_s <= 0:
            raise ValueError(
                f"kyutai module: 'lag_warn_s' must be > 0, got {self._lag_warn_s}"
            )
        if self._lag_drop_s <= self._lag_warn_s:
            raise ValueError(
                f"kyutai module: 'lag_drop_s' ({self._lag_drop_s}) must be greater "
                f"than 'lag_warn_s' ({self._lag_warn_s})"
            )

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
        audio_queue: asyncio.Queue[bytes],
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

    async def _ensure_loaded(
        self, audio_queue: asyncio.Queue[bytes]
    ) -> KyutaiBackend | None:
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
        audio_queue: asyncio.Queue[bytes],
        on_utterance: UtteranceCallback,
    ) -> None:
        """Run feed loop + worker thread + emit loop until stop() or a failure."""
        loop = asyncio.get_running_loop()
        assert self._executor is not None
        frames: queue.Queue = queue.Queue()
        results: asyncio.Queue = asyncio.Queue()
        worker_stop = threading.Event()

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

    async def _feed_loop(
        self, audio_queue: asyncio.Queue[bytes], frames: queue.Queue
    ) -> None:
        """Re-block s16 chunks into float32 model frames; apply backpressure."""
        loop = asyncio.get_running_loop()
        buffer = bytearray()
        last_warn = -math.inf
        while True:
            chunk = await self._get_or_stop(audio_queue)
            if chunk is None:
                return
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
    async def _drain_forever(audio_queue: asyncio.Queue[bytes]) -> None:
        while True:
            await audio_queue.get()

    async def _drain_queue_for(
        self, audio_queue: asyncio.Queue[bytes], duration: float
    ) -> None:
        """Drain audio_queue for *duration* seconds (or until stop())."""
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
