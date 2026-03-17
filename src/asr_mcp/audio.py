from __future__ import annotations

import asyncio

import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16000
CHANNELS = 1
DTYPE = "int16"
CHUNK_SAMPLES = 1600  # ~100 ms at 16 kHz


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

    def pause(self) -> None:
        """Pause the audio stream without closing it (queue reference stays valid)."""
        if self._stream is not None:
            self._stream.stop()

    def resume(self) -> None:
        """Resume a paused audio stream."""
        if self._stream is not None:
            self._stream.start()

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
