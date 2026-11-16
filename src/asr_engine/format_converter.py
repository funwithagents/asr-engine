"""Audio format conversion between the audio source and the ASR module.

The source produces one ``AudioFormat`` (``engine.audio_format``) and the module
is fed another (``engine.module_audio_format``). When they differ the engine wraps
the source in a ``ConvertedAudioSource``, which resamples, remixes and re-encodes
every chunk on its way to the module. When they are equal nothing here is built.

See specs/format-conversion.md.
"""

from __future__ import annotations

import asyncio
import logging

import numpy as np

from asr_engine.audio import (
    AudioFormat,
    AudioSource,
    linear16_to_mulaw,
    mulaw_to_linear16,
)

log = logging.getLogger(__name__)


def check_convertible(source: AudioFormat, target: AudioFormat) -> None:
    """Raise ``ValueError`` if *source* cannot be converted to *target*.

    Any rate and encoding pair converts. Channels convert when one side is mono
    (a downmix or a duplication); mapping N channels onto M others is undefined.
    """
    if source.channels != target.channels and 1 not in (
        source.channels,
        target.channels,
    ):
        raise ValueError(
            f"Cannot convert {source.channels} audio channels to "
            f"{target.channels}: only a downmix to mono or a duplication of mono "
            f"is supported."
        )


class AudioFormatConverter:
    """Converts a stream of chunks from one ``AudioFormat`` to another.

    Stateful and synchronous: feed it the stream's chunks in order through
    ``convert()``. It carries an incomplete sample from one chunk to the next and
    keeps the resampler's filter state, so the output is one continuous signal
    whatever the chunk sizes.

    ``frames_in`` and ``frames_out`` count the frames (one sample per channel)
    read and written so far, which maps a position in the converted stream back to
    the source's: ``frames_out / target.sample_rate`` seconds of output cover the
    first ``frames_in / source.sample_rate`` seconds of input, less what the
    resampler still holds (tens of milliseconds, see the spec).
    """

    def __init__(self, source: AudioFormat, target: AudioFormat) -> None:
        check_convertible(source, target)
        self._source = source
        self._target = target
        self._frame_bytes = source.channels * source.bytes_per_sample
        self._carry = b""  # bytes of an incomplete source frame
        self.frames_in = 0
        self.frames_out = 0
        self._resampler = None
        if source.sample_rate != target.sample_rate:
            import soxr  # only when resampling is actually needed

            self._resampler = soxr.ResampleStream(
                source.sample_rate,
                target.sample_rate,
                target.channels,
                dtype="int16",
            )

    def convert(self, chunk: bytes) -> bytes:
        """The next chunk of the stream, in the target format.

        May return fewer frames than the chunk's duration (the resampler holds
        some back until the next call), and ``b""`` for a chunk too short to hold a
        whole frame.
        """
        data = self._carry + chunk
        usable = len(data) - len(data) % self._frame_bytes
        self._carry = data[usable:]
        data = data[:usable]
        if not data:
            return b""
        if self._source.encoding == "mulaw":
            data = mulaw_to_linear16(data)
        frames = np.frombuffer(data, dtype="<i2").reshape(-1, self._source.channels)
        self.frames_in += len(frames)

        if self._source.channels != self._target.channels:
            if self._target.channels == 1:
                # int32 so the sum of the channels cannot overflow.
                mono = np.rint(frames.astype(np.int32).mean(axis=1))
                frames = mono.astype(np.int16).reshape(-1, 1)
            else:
                frames = np.repeat(frames, self._target.channels, axis=1)
        if self._resampler is not None:
            frames = self._resampler.resample_chunk(
                np.ascontiguousarray(frames, dtype=np.int16)
            ).reshape(-1, self._target.channels)
        self.frames_out += len(frames)

        pcm = np.ascontiguousarray(frames, dtype="<i2").tobytes()
        if self._target.encoding == "mulaw":
            pcm = linear16_to_mulaw(pcm)
        return pcm


class ConvertedAudioSource:
    """An ``AudioSource``-shaped wrapper delivering *inner*'s audio in another format.

    ``start()`` starts the inner source and returns a fresh unbounded queue carrying
    its chunks converted from *source_format* to *target_format*. ``stop()`` stops
    the inner source. Restartable: each start builds a new converter, so no audio
    of one run leaks into the next.

    A chunk that fails to convert is dropped and the stream goes on: the failure is
    logged once, with its traceback.
    """

    def __init__(
        self,
        inner: AudioSource,
        source_format: AudioFormat,
        target_format: AudioFormat,
    ) -> None:
        check_convertible(source_format, target_format)
        self._inner = inner
        self._source_format = source_format
        self._target_format = target_format
        self._queue: asyncio.Queue[bytes] | None = None
        self._task: asyncio.Task | None = None

    def start(self) -> asyncio.Queue[bytes]:
        """Start the inner source and return the queue of converted chunks."""
        converter = AudioFormatConverter(self._source_format, self._target_format)
        inner_queue = self._inner.start()
        self._queue = asyncio.Queue()
        self._task = asyncio.create_task(self._run(converter, inner_queue, self._queue))
        return self._queue

    def stop(self) -> None:
        """Stop converting and stop the inner source."""
        if self._task is not None:
            self._task.cancel()
            self._task = None
        self._inner.stop()

    @staticmethod
    async def _run(
        converter: AudioFormatConverter,
        inner_queue: asyncio.Queue[bytes],
        out_queue: asyncio.Queue[bytes],
    ) -> None:
        failed = False
        while True:
            chunk = await inner_queue.get()
            try:
                converted = converter.convert(chunk)
            except Exception:
                if not failed:
                    log.exception(
                        "Audio format conversion failed; dropping the chunks "
                        "that cannot be converted"
                    )
                    failed = True
                continue
            if converted:
                out_queue.put_nowait(converted)
