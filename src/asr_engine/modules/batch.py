"""``BatchASRModule``: the shared base for batch (non-streaming) ASR models.

A batch model transcribes a complete stretch of audio and has no endpointing of
its own, so the VAD gate's end-of-speech marker ends each utterance
(``VAD_GATING = "required"``). The base class buffers the utterance, decodes it as
one final on the marker, shows interims by re-decoding the growing buffer, and
runs every model call on one resident worker thread. A subclass only implements
``load()`` and ``transcribe()``. See specs/batch-asr-module.md.
"""

from __future__ import annotations

import asyncio
import logging
from abc import abstractmethod
from concurrent.futures import ThreadPoolExecutor
from typing import ClassVar

import numpy as np

from asr_engine.audio import DEFAULT_AUDIO_FORMAT, AudioFormat, EndOfSpeech
from asr_engine.modules.base import (
    ASRModule,
    AudioQueue,
    ConnectedCallback,
    SpeechUtterance,
    UtteranceCallback,
)

log = logging.getLogger(__name__)


async def _cancel(*tasks: asyncio.Future) -> None:
    for task in tasks:
        task.cancel()
    for task in tasks:
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


class BatchASRModule(ASRModule):
    """Base class for batch models: decode each VAD-delimited utterance as a whole."""

    SUPPORTED_SAMPLE_RATES = frozenset({16000})
    SUPPORTED_CHANNELS = frozenset({1})
    SUPPORTED_ENCODINGS = frozenset({"linear16"})
    DEFAULT_SAMPLE_RATE = 16000
    DEFAULT_CHANNELS = 1
    DEFAULT_ENCODING = "linear16"
    VAD_GATING = "required"

    LABEL: ClassVar[str] = "batch"  # the registry key, for messages
    # Default for the interim_interval_s field. A subclass whose decodes cost
    # money (an HTTP API billed per request) defaults to 0: no interims.
    DEFAULT_INTERIM_INTERVAL_S: ClassVar[float] = 0.5

    _BACKOFF_S: tuple[float, ...] = (1.0, 2.0, 4.0, 8.0)

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self._interim_interval_s = self._number(
            config,
            "interim_interval_s",
            self.DEFAULT_INTERIM_INTERVAL_S,
            allow_zero=True,
        )
        self._max_utterance_s = self._number(config, "max_utterance_s", 30.0)
        self._stop_timeout_s = self._number(config, "stop_timeout_s", 10.0)

        self._executor: ThreadPoolExecutor | None = None
        self._load_future: asyncio.Future[None] | None = None
        self._stop_event = asyncio.Event()
        self._stopped = asyncio.Event()
        self._running = False
        self._on_utterance: UtteranceCallback | None = None
        self._bytes_per_s = 16000 * 2

        # The open utterance. ``_generation`` changes whenever it closes, so an
        # interim decoded for a closed utterance is recognized and dropped.
        self._buffer = bytearray()
        self._generation = 0
        self._interim_mark = 0  # buffer length at the last scheduled interim
        self._interim_busy = False
        self._last_final: asyncio.Task[None] | None = None
        self._pending: set[asyncio.Task[None]] = set()

    def _number(
        self, config: dict, key: str, default: float, *, allow_zero: bool = False
    ) -> float:
        value = config.get(key, default)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or value < 0
            or (value == 0 and not allow_zero)
        ):
            bound = "a non-negative" if allow_zero else "a positive"
            raise ValueError(f"{self.LABEL} module: '{key}' must be {bound} number")
        return float(value)

    # ------------------------------------------------------- subclass interface

    @abstractmethod
    def load(self) -> None:
        """Load and warm the model. Blocking; runs on the worker thread."""

    @abstractmethod
    def transcribe(self, audio: np.ndarray) -> str:
        """Transcribe one utterance (float32 mono in [-1, 1] at the pipeline rate).
        Blocking; runs on the worker thread."""

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
        self._stopped.clear()
        self._running = True
        self._on_utterance = on_utterance
        self._bytes_per_s = (
            audio_format.sample_rate
            * audio_format.channels
            * audio_format.bytes_per_sample
        )
        self._close_utterance()
        connected = False
        try:
            if not await self._ensure_loaded(audio_queue):
                return  # stopped while loading
            connected = True
            if on_connected is not None:
                on_connected(True)
            while True:
                item = await self._next_chunk(audio_queue)
                if item is None:
                    break
                if isinstance(item, EndOfSpeech):
                    await self.finalize()
                else:
                    self._on_audio(item)
            self._schedule_final()  # flush what was said before stop
            await self._wait_pending()
        finally:
            if connected and on_connected is not None:
                on_connected(False)
            self._running = False
            self._stopped.set()

    async def stop(self) -> None:
        """Stop, flushing the open utterance as a final, and return once the last
        decodes have been emitted (bounded by ``stop_timeout_s``). The model stays
        loaded for the next ``start()``."""
        self._stop_event.set()
        if not self._running:
            return
        try:
            await asyncio.wait_for(self._stopped.wait(), self._stop_timeout_s + 1)
        except asyncio.TimeoutError:
            log.warning("%s: did not stop in time", self.LABEL)

    async def finalize(self) -> None:
        """End of speech: decode the utterance as a final. Returns at once."""
        self._schedule_final()

    # ------------------------------------------------------------------- loading

    async def _ensure_loaded(self, audio_queue: AudioQueue) -> bool:
        """Load the model once (resident), draining the queue meanwhile; retry a
        failed load with backoff. ``False`` when stopped first."""
        loop = asyncio.get_running_loop()
        if self._executor is None:
            self._executor = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix=f"{self.LABEL}-worker"
            )
        attempt = 0
        while not self._stop_event.is_set():
            if self._load_future is None:
                self._load_future = loop.run_in_executor(self._executor, self.load)
            future = self._load_future
            await self._discard_until(audio_queue, future=future)
            if not future.done():
                return False  # stopped; the load carries on for the next start()
            exc = future.exception()
            if exc is None:
                return True
            self._load_future = None
            delay = self._BACKOFF_S[min(attempt, len(self._BACKOFF_S) - 1)]
            attempt += 1
            log.error(
                "%s: model load failed (attempt %d, retrying in %.0fs): %s",
                self.LABEL,
                attempt,
                delay,
                exc,
            )
            await self._discard_until(audio_queue, timeout=delay)
        return False

    async def _discard_until(
        self,
        audio_queue: AudioQueue,
        *,
        future: asyncio.Future[None] | None = None,
        timeout: float | None = None,
    ) -> None:
        """Discard queue items until *future* is done, *timeout* passes, or stop."""
        loop = asyncio.get_running_loop()
        deadline = None if timeout is None else loop.time() + timeout
        while not self._stop_event.is_set() and not (
            future is not None and future.done()
        ):
            remaining = None if deadline is None else deadline - loop.time()
            if remaining is not None and remaining <= 0:
                return
            get_task = asyncio.create_task(audio_queue.get())
            stop_task = asyncio.create_task(self._stop_event.wait())
            waits: set[asyncio.Future] = {get_task, stop_task}
            if future is not None:
                waits.add(future)
            try:
                await asyncio.wait(
                    waits, timeout=remaining, return_when=asyncio.FIRST_COMPLETED
                )
            finally:
                await _cancel(*(t for t in (get_task, stop_task) if not t.done()))

    # ------------------------------------------------------------- utterances

    def _on_audio(self, chunk: bytes) -> None:
        self._buffer += chunk
        size = len(self._buffer)
        if size >= self._max_utterance_s * self._bytes_per_s:
            log.info(
                "%s: utterance reached %.0fs; cutting it",
                self.LABEL,
                self._max_utterance_s,
            )
            self._schedule_final()
            return
        interval = self._interim_interval_s * self._bytes_per_s
        if (
            interval > 0
            and not self._interim_busy
            and size - self._interim_mark >= interval
        ):
            self._interim_mark = size
            self._schedule_interim()

    def _close_utterance(self) -> bytes:
        audio = bytes(self._buffer)
        self._buffer.clear()
        self._generation += 1
        self._interim_mark = 0
        return audio

    def _schedule_final(self) -> None:
        audio = self._close_utterance()
        if not audio or self._executor is None:
            return
        decode = self._decode(audio)
        previous = self._last_final
        task = asyncio.create_task(self._emit_final(decode, previous))
        self._last_final = task
        self._track(task)

    def _schedule_interim(self) -> None:
        self._interim_busy = True
        decode = self._decode(bytes(self._buffer))
        task = asyncio.create_task(
            self._emit_interim(decode, self._generation, self._last_final)
        )
        self._track(task)

    def _decode(self, audio: bytes) -> asyncio.Future[str | None]:
        assert self._executor is not None
        samples = np.frombuffer(audio[: len(audio) // 2 * 2], dtype="<i2")
        pcm = samples.astype(np.float32) / 32768.0
        return asyncio.get_running_loop().run_in_executor(
            self._executor, self._safe_transcribe, pcm
        )

    def _safe_transcribe(self, audio: np.ndarray) -> str | None:
        try:
            return self.transcribe(audio).strip()
        except Exception:
            log.exception("%s: transcription failed", self.LABEL)
            return None

    async def _emit_final(
        self, decode: asyncio.Future[str | None], previous: asyncio.Task[None] | None
    ) -> None:
        text = await decode
        if previous is not None:
            await asyncio.wait({previous})  # finals leave in order
        if text:
            await self._emit(text, is_final=True)

    async def _emit_interim(
        self,
        decode: asyncio.Future[str | None],
        generation: int,
        previous: asyncio.Task[None] | None,
    ) -> None:
        try:
            text = await decode
        finally:
            self._interim_busy = False
        if previous is not None:
            await asyncio.wait({previous})  # never ahead of an earlier final
        if text and generation == self._generation:
            await self._emit(text, is_final=False)

    def _track(self, task: asyncio.Task[None]) -> None:
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def _wait_pending(self) -> None:
        tasks = set(self._pending)
        if not tasks:
            return
        _, late = await asyncio.wait(tasks, timeout=self._stop_timeout_s)
        if late:
            log.warning(
                "%s: %d decode(s) still running after %.0fs; dropping them",
                self.LABEL,
                len(late),
                self._stop_timeout_s,
            )
            await _cancel(*late)

    async def _emit(self, transcript: str, *, is_final: bool) -> None:
        if self._on_utterance is None:
            return
        try:
            await self._on_utterance(
                SpeechUtterance(
                    transcript=transcript, is_final=is_final, confidence=None
                )
            )
        except Exception:
            log.exception("%s: utterance callback failed", self.LABEL)

    # -------------------------------------------------------------------- helpers

    async def _next_chunk(self, audio_queue: AudioQueue) -> bytes | EndOfSpeech | None:
        """The next queue item, or ``None`` on stop."""
        if self._stop_event.is_set():
            return None
        get_task = asyncio.create_task(audio_queue.get())
        stop_task = asyncio.create_task(self._stop_event.wait())
        try:
            await asyncio.wait(
                {get_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            await _cancel(*(t for t in (get_task, stop_task) if not t.done()))
        # A get that completed alongside the stop still holds a dequeued chunk.
        if get_task.done() and not get_task.cancelled():
            return get_task.result()
        return None
