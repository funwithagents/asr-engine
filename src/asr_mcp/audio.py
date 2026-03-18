from __future__ import annotations

import asyncio
import wave
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
        chunk_duration = self._chunk_samples / SAMPLE_RATE
        with wave.open(str(self._path), "rb") as wf:
            assert wf.getframerate() == SAMPLE_RATE, (
                f"Expected sample rate {SAMPLE_RATE}, got {wf.getframerate()}"
            )
            assert wf.getnchannels() == CHANNELS, (
                f"Expected {CHANNELS} channel(s), got {wf.getnchannels()}"
            )
            assert wf.getsampwidth() == 2, (
                f"Expected 2-byte samples (s16), got {wf.getsampwidth()}"
            )
            while True:
                data = wf.readframes(self._chunk_samples)
                if not data:
                    break
                await self._queue.put(data)
                await asyncio.sleep(chunk_duration)

        silence_chunk = b"\x00" * (self._chunk_samples * 2)
        silence_chunks = int(self._trailing_silence_s / chunk_duration)
        for _ in range(silence_chunks):
            await self._queue.put(silence_chunk)
            await asyncio.sleep(chunk_duration)

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None


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

        self._queue: asyncio.Queue[bytes] = asyncio.Queue()

        def _callback(indata: np.ndarray, frames: int, time, status) -> None:  # noqa: ARG001
            chunk = bytes(indata)
            self._loop.call_soon_threadsafe(self._queue.put_nowait, chunk)

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
