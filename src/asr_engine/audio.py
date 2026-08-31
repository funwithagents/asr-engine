from __future__ import annotations

import asyncio
import wave
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16000
CHANNELS = 1
DTYPE = "int16"
CHUNK_SAMPLES = 1600  # ~100 ms at 16 kHz


class AudioSource(Protocol):
    """Shared interface for microphone and file-based audio sources."""

    def start(self) -> asyncio.Queue[bytes]:
        """Open the source and return a queue that receives PCM chunks."""
        ...

    def stop(self) -> None:
        """Stop and close the source."""
        ...


def _silence_chunk(chunk_samples: int) -> bytes:
    """One chunk of digital silence (all-zero s16 samples)."""
    return b"\x00" * (chunk_samples * 2)


def _validate_wav_format(wf: wave.Wave_read, path: Path) -> None:
    """Assert a WAV matches the pipeline audio contract, raising ValueError if not."""
    if wf.getframerate() != SAMPLE_RATE:
        raise ValueError(
            f"{path}: expected sample rate {SAMPLE_RATE}, got {wf.getframerate()}"
        )
    if wf.getnchannels() != CHANNELS:
        raise ValueError(
            f"{path}: expected {CHANNELS} channel(s), got {wf.getnchannels()}"
        )
    if wf.getsampwidth() != 2:
        raise ValueError(
            f"{path}: expected 2-byte samples (s16), got {wf.getsampwidth()}"
        )


def _iter_wav_chunks(path: Path | str, chunk_samples: int) -> Iterator[bytes]:
    """Yield a validated WAV's PCM frames in ``chunk_samples``-sized chunks.

    Validates the format up front (raising ValueError on a mismatch), then yields
    each chunk of raw bytes; the final chunk may be shorter than a full chunk.
    """
    path = Path(path)
    with wave.open(str(path), "rb") as wf:
        _validate_wav_format(wf, path)
        while True:
            data = wf.readframes(chunk_samples)
            if not data:
                break
            yield data


class FileAudioSource:
    """Reads a WAV file and feeds PCM chunks into an asyncio queue at real-time pace."""

    def __init__(
        self,
        path: Path | str,
        chunk_samples: int = CHUNK_SAMPLES,
        trailing_silence_s: float = 0.0,
    ) -> None:
        self._path = Path(path)
        self._chunk_samples = chunk_samples
        self._trailing_silence_s = trailing_silence_s
        self._queue: asyncio.Queue[bytes] | None = None
        self._task: asyncio.Task | None = None

    def start(self) -> asyncio.Queue[bytes]:
        self._queue = asyncio.Queue()
        self._task = asyncio.create_task(self._feed())
        return self._queue

    async def _feed(self) -> None:
        assert self._queue is not None  # set by start() before this task runs
        chunk_duration = self._chunk_samples / SAMPLE_RATE
        for data in _iter_wav_chunks(self._path, self._chunk_samples):
            await self._queue.put(data)
            await asyncio.sleep(chunk_duration)

        silence_chunk = _silence_chunk(self._chunk_samples)
        silence_chunks = int(self._trailing_silence_s / chunk_duration)
        for _ in range(silence_chunks):
            await self._queue.put(silence_chunk)
            await asyncio.sleep(chunk_duration)

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None


@dataclass
class _PlayRequest:
    """One queued ``ScriptableAudioSource.play`` call."""

    chunks: list[bytes]
    trailing_silence_chunks: int
    done: asyncio.Future[None]


class ScriptableAudioSource:
    """An ``AudioSource`` a test drives by hand: continuous silence, files on demand.

    Implements the same ``start()`` / ``stop()`` interface as ``AudioCapture`` and
    ``FileAudioSource``, so it drops straight into
    ``ASREngine(config, audio_source=...)``. Once started it feeds digital silence
    into the queue at real-time cadence — keeping a streaming ASR backend connected
    and letting it finalize utterances on the gaps. Call ``play(path)`` to feed a
    WAV file's audio; ``play`` resolves once the file (plus any
    ``trailing_silence_s``) has been fed onto the queue, after which silence
    resumes. Concurrent/queued ``play`` calls run in order.

    This lets a test decide exactly when each utterance is spoken and sequence
    several, one after the other::

        source = ScriptableAudioSource()
        engine = ASREngine(config, audio_source=source, on_speech_segment=collect)
        await engine.start()
        await source.play("hello.wav", trailing_silence_s=2.0)
        # ...await the engine's utterances/segments, then assert...
        await source.play("world.wav", trailing_silence_s=2.0)
        await engine.stop()

    ``play`` resolves when the audio has been *fed*, not when the backend has
    transcribed it — the source cannot see the ASR's state — so wait on the
    engine's own output for that. The trailing silence is what prompts a streaming
    backend to finalize the just-played utterance.

    Set ``real_time=False`` to feed as fast as the consumer drains (no per-chunk
    sleep) for fast, deterministic unit tests.
    """

    def __init__(
        self,
        chunk_samples: int = CHUNK_SAMPLES,
        *,
        real_time: bool = True,
        maxsize: int = 50,
    ) -> None:
        self._chunk_samples = chunk_samples
        self._chunk_duration = chunk_samples / SAMPLE_RATE
        self._real_time = real_time
        self._maxsize = maxsize
        self._silence = _silence_chunk(chunk_samples)
        self._queue: asyncio.Queue[bytes] | None = None
        self._requests: asyncio.Queue[_PlayRequest] | None = None
        self._task: asyncio.Task | None = None

    def start(self) -> asyncio.Queue[bytes]:
        """Start feeding silence and return the queue that receives PCM chunks."""
        self._queue = asyncio.Queue(maxsize=self._maxsize)
        self._requests = asyncio.Queue()
        self._task = asyncio.create_task(self._run())
        return self._queue

    async def play(self, path: Path | str, *, trailing_silence_s: float = 0.0) -> None:
        """Feed one WAV file's audio, then ``trailing_silence_s`` of silence.

        Resolves once every chunk has been put on the queue (not when the backend
        has transcribed it). Raises ValueError if the file does not match the audio
        contract (16 kHz mono s16), and RuntimeError if called before ``start()``.
        """
        if self._requests is None:
            raise RuntimeError("ScriptableAudioSource.play() called before start()")
        # list(...) validates the format now, so a bad file raises here at the call
        # site rather than later inside the background task.
        chunks = list(_iter_wav_chunks(path, self._chunk_samples))
        trailing = int(trailing_silence_s / self._chunk_duration)
        done: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        await self._requests.put(_PlayRequest(chunks, trailing, done))
        await done

    async def _run(self) -> None:
        assert self._queue is not None and self._requests is not None
        while True:
            try:
                request = self._requests.get_nowait()
            except asyncio.QueueEmpty:
                await self._emit(self._silence)
                continue
            try:
                for chunk in request.chunks:
                    await self._emit(chunk)
                for _ in range(request.trailing_silence_chunks):
                    await self._emit(self._silence)
            finally:
                if not request.done.done():
                    request.done.set_result(None)

    async def _emit(self, chunk: bytes) -> None:
        assert self._queue is not None
        await self._queue.put(chunk)
        await asyncio.sleep(self._chunk_duration if self._real_time else 0)

    def stop(self) -> None:
        """Stop feeding and resolve any still-pending ``play`` calls."""
        if self._task is not None:
            self._task.cancel()
            self._task = None
        # Unblock any queued play() awaiters so they don't hang after stop().
        if self._requests is not None:
            while True:
                try:
                    request = self._requests.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if not request.done.done():
                    request.done.set_result(None)


class AudioCapture:
    """Captures audio from a system input device and pushes PCM chunks into an asyncio queue."""

    def __init__(self, device: str | None, loop: asyncio.AbstractEventLoop) -> None:
        self._device = device
        self._loop = loop
        self._stream: sd.InputStream | None = None
        self._queue: asyncio.Queue[bytes] | None = None

    def start(self) -> asyncio.Queue[bytes]:
        """Open and start the audio stream. Returns the queue for consumers."""
        if self._device is not None:
            available = self.list_devices()
            if self._device not in available:
                raise ValueError(
                    f"Audio device '{self._device}' not found. "
                    f"Available devices: {', '.join(available) or '(none)'}"
                )

        self._queue = asyncio.Queue()
        queue = self._queue

        def _callback(indata: np.ndarray, frames: int, time, status) -> None:  # noqa: ARG001
            chunk = bytes(indata)
            self._loop.call_soon_threadsafe(queue.put_nowait, chunk)

        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype=DTYPE,
            blocksize=CHUNK_SAMPLES,
            device=self._device,
            callback=_callback,
        )
        self._stream.start()
        return self._queue

    def stop(self) -> None:
        """Stop and close the audio stream."""
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    @staticmethod
    def list_devices() -> list[str]:
        """Return names of available audio input devices."""
        devices = sd.query_devices()
        return [d["name"] for d in devices if d["max_input_channels"] > 0]
