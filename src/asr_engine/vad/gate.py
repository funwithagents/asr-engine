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
from dataclasses import dataclass

import numpy as np

from asr_engine.audio import (
    END_OF_SPEECH,
    AudioFormat,
    AudioSource,
    EndOfSpeech,
    mulaw_to_linear16,
)
from asr_engine.segment_timing import GateSpeechTiming, now_ns
from asr_engine.vad.base import VADModule

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class VadState:
    """A snapshot of the gate, for display (``ASREngine.vad_state``)."""

    speaking: bool = False  # the gate is open: audio is reaching the module
    failed_open: bool = False  # the VAD raised; everything is forwarded ungated


@dataclass
class _SpeechRun:
    """One stretch the gate was open for, in wall-clock nanoseconds."""

    started_ns: int  # the start of the onset frame
    ended_ns: int  # the end of the latest frame scored as speech
    settled_ns: int | None = None  # the end of the last speech silence followed
    closed_ns: int | None = None  # when the gate closed; None while it is open


# Speech runs kept for a module that emits no final for a long time.
_MAX_PENDING_RUNS = 32


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
        self._frame_ns = round(frame_ms * 1_000_000)
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
        # Speech timing (specs/segment-timing.md): each chunk is stamped when it
        # is received, and a frame is placed by its position inside the audio
        # received so far.
        self._chunk_ns = 0  # when the chunk being processed was received
        self._received_bytes = 0
        self._frame_start_ns = 0  # the frame being stepped
        self._frame_end_ns = 0
        self._onset_ns = 0  # the start of the current run of speech frames
        self._run: _SpeechRun | None = None  # the open run
        self._latest_run: _SpeechRun | None = None
        # The runs no final utterance has been given yet.
        self._pending_runs: deque[_SpeechRun] = deque(maxlen=_MAX_PENDING_RUNS)
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
        self._task = asyncio.create_task(self._run_gate(inner_queue, self._queue))
        return self._queue

    def stop(self) -> None:
        """Stop gating and stop the inner source. An open utterance gets no marker."""
        if self._task is not None:
            self._task.cancel()
            self._task = None
        self._speaking = False
        # The next start() builds a fresh one. Releasing it here also keeps a
        # stopped gate from holding a native soxr object until interpreter exit.
        self._resampler = None
        self._inner.stop()

    def state(self) -> VadState:
        """Whether audio is reaching the module right now, and why."""
        return VadState(
            speaking=self._speaking or self._failed, failed_open=self._failed
        )

    def take_speech_timing(self) -> GateSpeechTiming | None:
        """The timing of the speech a final utterance arriving now transcribes.

        Called by the engine once per final utterance. The speech is what the
        gate has detected since the previous final: it starts with the first of
        those runs and ends with the last speech that silence followed. A run
        the person is still speaking in, with no pause yet, is left for the next
        final. A final with no new speech (a backend that splits one stretch
        into several finals) is given the latest run again.

        ``None`` when no speech was detected, and once the gate has failed open.
        """
        if self._failed:
            return None
        fresh = bool(self._pending_runs)
        runs = list(self._pending_runs)
        if not runs and self._latest_run is not None:
            runs = [self._latest_run]
        if not runs:
            return None
        started = runs[0].started_ns
        last = len(runs) - 1
        while last >= 0 and runs[last].settled_ns is None:
            last -= 1
        if last < 0:
            return GateSpeechTiming(started_ns=started, ended_ns=None, closed_ns=None)
        if fresh:
            for _ in range(last + 1):
                self._pending_runs.popleft()
        end = runs[last]
        return GateSpeechTiming(
            started_ns=started, ended_ns=end.settled_ns, closed_ns=end.closed_ns
        )

    def peek_speech_start(self) -> int | None:
        """The start of the speech an interim utterance arriving now belongs to:
        what the next final will be given as its start. Claims nothing."""
        if self._failed:
            return None
        if self._pending_runs:
            return self._pending_runs[0].started_ns
        return self._latest_run.started_ns if self._latest_run is not None else None

    async def _run_gate(
        self,
        inner_queue: asyncio.Queue[bytes],
        out_queue: asyncio.Queue[bytes | EndOfSpeech],
    ) -> None:
        while True:
            chunk = await inner_queue.get()
            self._chunk_ns = now_ns()
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
                # A gate that no longer scores frames knows nothing of the speech.
                self._run = self._latest_run = None
                self._pending_runs.clear()
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
        self._received_bytes += len(chunk)
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
            self._stamp_frame(boundary)

            self._step(probability >= self._threshold, piece, out_queue)
        self._analysis = self._analysis[offset:]
        self._flush(out_queue)

    def _stamp_frame(self, boundary: int) -> None:
        """Place the frame ending at pipeline sample *boundary* in wall-clock time.

        The chunk was stamped when it was received; the frame ended earlier, by
        the duration of the samples received after it. Stamps never run
        backwards, even when chunks arrive in a burst.
        """
        after = max(0, self._received_bytes // self._sample_bytes - boundary)
        end = self._chunk_ns - after * 1_000_000_000 // self._pipeline_rate
        previous = self._frame_end_ns
        self._frame_end_ns = max(end, previous)
        self._frame_start_ns = max(previous, self._frame_end_ns - self._frame_ns)

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
            if is_speech and self._speech_run == 0:
                self._onset_ns = self._frame_start_ns
            self._speech_run = self._speech_run + 1 if is_speech else 0
            if self._speech_run >= self._open_frames:
                self._speaking = True
                self._silence_run = 0
                # The onset, not its confirmation, and not the pre-roll.
                self._run = _SpeechRun(self._onset_ns, self._frame_end_ns)
                self._latest_run = self._run
                self._pending_runs.append(self._run)
                for kept in self._ring:
                    self._out.extend(kept)
                self._ring.clear()
            return

        self._out.extend(piece)  # pauses included: the hangover is the tail
        self._silence_run = 0 if is_speech else self._silence_run + 1
        run = self._run
        if run is not None:
            if is_speech:
                run.ended_ns = self._frame_end_ns
            else:
                run.settled_ns = run.ended_ns
        if self._silence_run >= self._close_frames:
            if run is not None:
                # Decided now, on the chunk that held the deciding frame.
                run.closed_ns = self._chunk_ns
                self._run = None
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
