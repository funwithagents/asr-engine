from __future__ import annotations

import asyncio
import logging
import wave
from importlib.resources import files

import numpy as np
import sounddevice as sd

log = logging.getLogger(__name__)

_SOUNDS_DIR = files("asr_engine") / "sounds"


def _play_wav(path, device) -> None:
    with wave.open(str(path), "rb") as wf:
        n_channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        framerate = wf.getframerate()
        frames = wf.readframes(wf.getnframes())
    dtype_map = {1: np.int8, 2: np.int16, 4: np.int32}
    data = np.frombuffer(frames, dtype=dtype_map[sampwidth])
    if n_channels > 1:
        data = data.reshape(-1, n_channels)
    sd.play(data, framerate, device=device)
    sd.wait()


class SoundFeedback:
    def __init__(self, output_device: str | int | None = None) -> None:
        self._output_device = output_device

    async def play_start(self) -> None:
        path = _SOUNDS_DIR / "feedback_asr_start.wav"
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, _play_wav, path, self._output_device)
        except Exception:
            log.error("Sound feedback (start) failed", exc_info=True)

    async def play_stop(self) -> None:
        path = _SOUNDS_DIR / "feedback_asr_stop.wav"
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, _play_wav, path, self._output_device)
        except Exception:
            log.error("Sound feedback (stop) failed", exc_info=True)


class NoOpSoundFeedback:
    async def play_start(self) -> None:
        pass

    async def play_stop(self) -> None:
        pass
