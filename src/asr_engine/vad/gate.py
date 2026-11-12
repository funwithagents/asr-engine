"""``VadGate`` — the speech gate between an audio source and the ASR module.

Wraps any ``AudioSource``: forwards speech, drops silence, and puts an
``END_OF_SPEECH`` marker on its output queue when an utterance ends. It decides on
an *analysis copy* of the audio (mono float32 at a rate the VAD module supports)
and forwards the source's own bytes untouched. Internal: the engine builds it.

See specs/vad.md ("VadGate", "Gating state machine", "Audio formats").
"""

from __future__ import annotations

import asyncio
import logging
import math
from collections import deque

import numpy as np

from asr_engine.audio import (
    END_OF_SPEECH,
    AudioFormat,
    AudioSource,
    EndOfSpeech,
    mulaw_to_linear16,
)
from asr_engine.vad.base import VADModule

log = logging.getLogger(__name__)


def analysis_sample_rate(vad_cls: type[VADModule], pipeline_rate: int) -> int:
    """The rate the gate analyses at: the pipeline rate when the VAD supports it
    (no resampling), otherwise the VAD's default rate."""
    if pipeline_rate in vad_cls.SUPPORTED_SAMPLE_RATES:
        return pipeline_rate
    return vad_cls.DEFAULT_SAMPLE_RATE


class VadGate:
    """An ``AudioSource``-shaped wrapper that forwards only speech.

    ``start()`` returns a fresh unbounded queue carrying the inner source's bytes
    for the speech regions, each followed by ``END_OF_SPEECH`` (when
    ``emit_end_of_speech``). Restartable. A failure while analysing makes the gate
    fail open: it forwards everything from then on and emits no more markers.
    """

    def __init__(
        self,
        inner: AudioSource,
        module: VADModule,  # already loaded
        audio_format: AudioFormat,  # the pipeline format
        *,
        threshold: float,
        min_speech_duration_ms: int,
        min_silence_duration_ms: int,
        speech_pad_ms: int,
        emit_end_of_speech: bool,
    ) -> None:
        self._inner = inner
        self._module = module
        self._format = audio_format
        self._threshold = threshold
        self._emit_end_of_speech = emit_end_of_speech

        self._pipeline_rate = audio_format.sample_rate
        self._analysis_rate = analysis_sample_rate(type(module), self._pipeline_rate)
        self._frame_samples = module.frame_samples(self._analysis_rate)
        # Bytes of one pipeline sample across all channels: every cut lands on one.
        self._sample_bytes = audio_format.channels * audio_format.bytes_per_sample

        frame_ms = 1000 * self._frame_samples / self._analysis_rate
        # Durations in whole frames, rounded up (the epsilon absorbs float noise).
        self._open_frames = max(1, math.ceil(min_speech_duration_ms / frame_ms - 1e-9))
        self._close_frames = max(
            1, math.ceil(min_silence_duration_ms / frame_ms - 1e-9)
        )
        pad_frames = math.ceil(speech_pad_ms / frame_ms - 1e-9)
        # The onset is confirmed _open_frames after it happened, so the ring holds
        # the pre-roll *and* the frames that confirmed it.
        self._ring: deque[bytes] = deque(maxlen=pad_frames + self._open_frames)

        self._queue: asyncio.Queue[bytes | EndOfSpeech] | None = None
        self._task: asyncio.Task | None = None
        self._reset_state()

    def _reset_state(self) -> None:
        self._ring.clear()
        self._speaking = False
        self._speech_run = 0
        self._silence_run = 0
        self._failed = False
        self._carry = b""  # bytes of an incomplete pipeline sample
        self._pipeline = bytearray()  # pipeline bytes not yet assigned to a frame
        self._pipeline_pos = 0  # pipeline sample index of _pipeline[0]
        self._analysis = np.zeros(0, dtype=np.float32)  # samples awaiting a frame
        self._frames_done = 0
        self._out = bytearray()  # forwarded bytes awaiting the end of this chunk
        self._resampler = None
        if self._analysis_rate != self._pipeline_rate:
            import soxr  # only when resampling is actually needed

            self._resampler = soxr.ResampleStream(
                self._pipeline_rate, self._analysis_rate, 1, dtype="float32"
            )

    # ------------------------------------------------------------------ lifecycle

    def start(self) -> asyncio.Queue[bytes | EndOfSpeech]:
        """Start the inner source and return the gated queue."""
        self._module.reset()
        self._reset_state()
        inner_queue = self._inner.start()
        self._queue = asyncio.Queue()
        self._task = asyncio.create_task(self._run(inner_queue, self._queue))
        return self._queue

    def stop(self) -> None:
        """Stop gating and stop the inner source. An open utterance gets no marker."""
        if self._task is not None:
            self._task.cancel()
            self._task = None
        self._inner.stop()

    async def _run(
        self,
        inner_queue: asyncio.Queue[bytes],
        out_queue: asyncio.Queue[bytes | EndOfSpeech],
    ) -> None:
        while True:
            chunk = await inner_queue.get()
            if self._failed:
                out_queue.put_nowait(chunk)
                continue
            try:
                self._process(chunk, out_queue)
            except Exception:
                log.exception(
                    "VAD gate failed; forwarding all audio ungated from now on"
                )
                self._failed = True
                # Nothing already forwarded is repeated: _out and _pipeline hold
                # only bytes that have not reached the queue (this chunk included).
                leftover = bytes(self._out) + bytes(self._pipeline)
                self._out.clear()
                self._pipeline.clear()
                if leftover:
                    out_queue.put_nowait(leftover)

    # ----------------------------------------------------------------- processing

    def _process(
        self, chunk: bytes, out_queue: asyncio.Queue[bytes | EndOfSpeech]
    ) -> None:
        self._pipeline.extend(chunk)
        self._analysis = np.concatenate([self._analysis, self._analyse(chunk)])

        offset = 0
        n = self._frame_samples
        while len(self._analysis) - offset >= n:
            frame = self._analysis[offset : offset + n]
            # Scored before its bytes are cut, so a failure here loses nothing.
            probability = self._module.process_frame(frame, self._analysis_rate)
            offset += n
            self._frames_done += 1
            # The pipeline sample nearest this frame's end: each frame owns one
            # contiguous slice of the original bytes, cut on a whole sample.
            boundary = round(
                self._frames_done * n * self._pipeline_rate / self._analysis_rate
            )
            size = (boundary - self._pipeline_pos) * self._sample_bytes
            size = max(0, min(size, len(self._pipeline)))
            piece = bytes(self._pipeline[:size])
            del self._pipeline[:size]
            self._pipeline_pos += size // self._sample_bytes

            self._step(probability >= self._threshold, piece, out_queue)
        self._analysis = self._analysis[offset:]
        self._flush(out_queue)

    def _analyse(self, chunk: bytes) -> np.ndarray:
        """The analysis copy of *chunk*: mono float32 at the analysis rate."""
        data = self._carry + chunk
        usable = len(data) - len(data) % self._sample_bytes
        self._carry = data[usable:]
        data = data[:usable]
        if self._format.encoding == "mulaw":
            data = mulaw_to_linear16(data)
        samples = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0
        if self._format.channels > 1:
            samples = samples.reshape(-1, self._format.channels).mean(axis=1)
        if self._resampler is not None:
            samples = self._resampler.resample_chunk(samples)
        return samples.astype(np.float32, copy=False)

    def _step(
        self,
        is_speech: bool,
        piece: bytes,
        out_queue: asyncio.Queue[bytes | EndOfSpeech],
    ) -> None:
        """Advance the two-state machine by one analysed frame."""
        if not self._speaking:
            self._ring.append(piece)
            self._speech_run = self._speech_run + 1 if is_speech else 0
            if self._speech_run >= self._open_frames:
                self._speaking = True
                self._silence_run = 0
                for kept in self._ring:
                    self._out.extend(kept)
                self._ring.clear()
            return

        self._out.extend(piece)  # pauses included: the hangover is the tail
        self._silence_run = 0 if is_speech else self._silence_run + 1
        if self._silence_run >= self._close_frames:
            self._flush(out_queue)
            if self._emit_end_of_speech:
                out_queue.put_nowait(END_OF_SPEECH)
            self._module.reset()
            self._speaking = False
            self._speech_run = 0

    def _flush(self, out_queue: asyncio.Queue[bytes | EndOfSpeech]) -> None:
        """Put the pending forwarded bytes on the queue, in ~CHUNK_MS chunks.

        Called once per incoming chunk and before a marker, so nothing is held
        back. A long run (the pre-roll at an onset) is split; the remainder joins
        the last chunk rather than trailing as a sliver.
        """
        if not self._out:
            return
        data = bytes(self._out)
        self._out.clear()
        size = self._format.chunk_bytes
        start = 0
        while len(data) - start >= 2 * size:
            out_queue.put_nowait(data[start : start + size])
            start += size
        out_queue.put_nowait(data[start:])
