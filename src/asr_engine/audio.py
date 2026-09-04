from __future__ import annotations

import asyncio
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np
import sounddevice as sd
import soundfile as sf

SAMPLE_RATE = 16000
CHANNELS = 1
DTYPE = "int16"  # capture is always signed 16-bit PCM; encoding happens after
CHUNK_SAMPLES = 1600  # ~100 ms at 16 kHz
CHUNK_MS = 100  # target chunk length; frames-per-chunk derives from the rate

# Bytes each encoding uses per sample. Capture always produces linear16 (s16);
# other encodings are transcoded from it before a chunk reaches the queue.
_BYTES_PER_SAMPLE = {"linear16": 2, "mulaw": 1}


@dataclass(frozen=True)
class AudioFormat:
    """The audio format delivered to an ASR module: rate, channels, encoding.

    Threads through the whole pipeline — the capture layer opens its stream at
    this rate/channels and transcodes to this encoding, and the module reports it
    to its backend. Chunk sizing (~``CHUNK_MS``) derives from the rate.
    """

    sample_rate: int = SAMPLE_RATE
    channels: int = CHANNELS
    encoding: str = "linear16"  # "linear16" | "mulaw"

    def __post_init__(self) -> None:
        if self.encoding not in _BYTES_PER_SAMPLE:
            raise ValueError(
                f"Unsupported audio encoding '{self.encoding}'. "
                f"Supported: {', '.join(sorted(_BYTES_PER_SAMPLE))}."
            )
        if self.sample_rate <= 0:
            raise ValueError(f"sample_rate must be positive, got {self.sample_rate}")
        if self.channels <= 0:
            raise ValueError(f"channels must be positive, got {self.channels}")

    @property
    def bytes_per_sample(self) -> int:
        return _BYTES_PER_SAMPLE[self.encoding]

    @property
    def frames_per_chunk(self) -> int:
        """Samples per channel in one ~``CHUNK_MS`` chunk (the stream blocksize)."""
        return round(self.sample_rate * CHUNK_MS / 1000)

    @property
    def chunk_bytes(self) -> int:
        """Byte length of one full encoded chunk."""
        return self.frames_per_chunk * self.channels * self.bytes_per_sample

    @property
    def chunk_duration_s(self) -> float:
        """Real-time duration of one chunk, for pacing file/scripted sources."""
        return self.frames_per_chunk / self.sample_rate


DEFAULT_AUDIO_FORMAT = AudioFormat()


class AudioSource(Protocol):
    """Audio source for an ``ASREngine`` — the public, stable injection seam.

    Pass an implementation as ``ASREngine(config, audio_source=...)`` to feed the
    engine from something other than the local mic or a file (e.g. an external,
    already-captured PCM stream). The bundled ``AudioCapture`` (default mic),
    ``FileAudioSource`` and ``ScriptableAudioSource`` all satisfy this Protocol.
    See specs/audio-source.md for the full contract; in brief:

    - ``start()`` opens the source and returns the ``asyncio.Queue[bytes]`` it
      **owns and feeds**; the engine reads that queue (it does not create it).
    - Each queue item is one chunk of raw PCM bytes **in the engine's reconciled
      ``engine.audio_format``**: ``linear16`` is signed 16-bit little-endian,
      ``mulaw`` is G.711; mono unless ``channels > 1`` (then interleaved). Any
      reasonable chunk size is accepted — the module forwards bytes as-is; the
      ``~CHUNK_MS`` target is what the bundled sources use, not a requirement.
    - The engine does **not** resample/re-encode chunks from an injected source;
      a custom source must produce ``engine.audio_format`` itself (which is valid
      immediately after construction, before ``start()``).
    - ``stop()`` halts feeding and releases resources. The engine calls it on
      ``engine.stop()`` and in ``listen()`` teardown (always, even on error), and
      may call ``start()`` again afterwards — the source must be restartable.
    - The source owns the queue and picks bounded vs unbounded. The module drains
      continuously (including during reconnect backoff), so an unbounded queue
      with ``put_nowait`` never wedges the engine.
    """

    def start(self) -> asyncio.Queue[bytes]:
        """Open the source and return the queue it feeds with PCM chunks."""
        ...

    def stop(self) -> None:
        """Stop and close the source (restartable via a later ``start()``)."""
        ...


def linear16_to_mulaw(pcm: bytes) -> bytes:
    """Encode signed 16-bit little-endian PCM to G.711 μ-law (1 byte/sample).

    A vectorized numpy implementation of the CCITT G.711 encoder; ``audioop`` is
    not used because it is removed in Python 3.13 (this package targets >=3.11).
    """
    samples = np.frombuffer(pcm, dtype="<i2").astype(np.int32)
    bias = 0x84
    clip = 32635
    sign = ((samples >> 8) & 0x80).astype(np.int32)  # 0x80 for negatives, else 0
    magnitude = np.minimum(np.abs(samples), clip) + bias
    segment = (magnitude >> 7) & 0xFF
    exponent = np.zeros_like(segment)
    nonzero = segment > 0
    exponent[nonzero] = np.floor(np.log2(segment[nonzero])).astype(np.int32)
    mantissa = (magnitude >> (exponent + 3)) & 0x0F
    ulaw = ~(sign | (exponent << 4) | mantissa) & 0xFF
    return ulaw.astype(np.uint8).tobytes()


def _encode_chunk(linear16: bytes, fmt: AudioFormat) -> bytes:
    """Transcode a linear16 (s16) chunk to *fmt*'s encoding (no-op for linear16)."""
    if fmt.encoding == "mulaw":
        return linear16_to_mulaw(linear16)
    return linear16


def _silence_chunk(fmt: AudioFormat) -> bytes:
    """One chunk of digital silence in *fmt*'s encoding.

    μ-law silence is not all-zero bytes, so silence is built as linear16 zeros
    and transcoded — keeping the queue's chunks uniform in encoding.
    """
    zeros = b"\x00" * (fmt.frames_per_chunk * fmt.channels * 2)
    return _encode_chunk(zeros, fmt)


def _validate_audio_format(info, path: Path, fmt: AudioFormat) -> None:
    """Assert a decoded file matches *fmt*'s rate/channels, else ValueError.

    *info* is a ``soundfile`` info object. Sources are decoded to s16 (any
    container/encoding libsndfile supports, e.g. WAV or MP3) but are **not**
    resampled or remixed, so the file's own sample rate and channel count must
    already match the target format.
    """
    if info.samplerate != fmt.sample_rate:
        raise ValueError(
            f"{path}: expected sample rate {fmt.sample_rate}, got {info.samplerate}"
        )
    if info.channels != fmt.channels:
        raise ValueError(
            f"{path}: expected {fmt.channels} channel(s), got {info.channels}"
        )


def _iter_audio_chunks(path: Path | str, fmt: AudioFormat) -> Iterator[bytes]:
    """Yield a decoded audio file's frames as *fmt*-encoded chunks.

    Decodes via ``soundfile`` (libsndfile), so any supported container/codec —
    WAV, MP3, FLAC, … — works. Validates the file's rate/channels against *fmt*
    up front (raising ValueError on a mismatch; files are not resampled), then
    streams ~``CHUNK_MS`` blocks as s16 and transcodes each to *fmt*'s encoding.
    The final chunk may be shorter than a full chunk.
    """
    path = Path(path)
    _validate_audio_format(sf.info(str(path)), path, fmt)
    for block in sf.blocks(
        str(path), blocksize=fmt.frames_per_chunk, dtype="int16", always_2d=True
    ):
        # block: (frames, channels) int16 → interleaved little-endian s16 bytes.
        pcm = np.ascontiguousarray(block, dtype="<i2").tobytes()
        yield _encode_chunk(pcm, fmt)


class FileAudioSource:
    """Decodes an audio file (WAV/MP3/… via libsndfile) and feeds PCM chunks into
    an asyncio queue at real-time pace, in the given ``AudioFormat``."""

    def __init__(
        self,
        path: Path | str,
        audio_format: AudioFormat = DEFAULT_AUDIO_FORMAT,
        trailing_silence_s: float = 0.0,
    ) -> None:
        self._path = Path(path)
        self._format = audio_format
        self._trailing_silence_s = trailing_silence_s
        self._queue: asyncio.Queue[bytes] | None = None
        self._task: asyncio.Task | None = None

    def start(self) -> asyncio.Queue[bytes]:
        self._queue = asyncio.Queue()
        self._task = asyncio.create_task(self._feed())
        return self._queue

    async def _feed(self) -> None:
        assert self._queue is not None  # set by start() before this task runs
        chunk_duration = self._format.chunk_duration_s
        for data in _iter_audio_chunks(self._path, self._format):
            await self._queue.put(data)
            await asyncio.sleep(chunk_duration)

        silence_chunk = _silence_chunk(self._format)
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
        audio_format: AudioFormat = DEFAULT_AUDIO_FORMAT,
        *,
        real_time: bool = True,
        maxsize: int = 50,
    ) -> None:
        self._format = audio_format
        self._chunk_duration = audio_format.chunk_duration_s
        self._real_time = real_time
        self._maxsize = maxsize
        self._silence = _silence_chunk(audio_format)
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
        has transcribed it). Raises ValueError if the file's rate/channels don't
        match this source's ``AudioFormat``, and RuntimeError if called before
        ``start()``.
        """
        if self._requests is None:
            raise RuntimeError("ScriptableAudioSource.play() called before start()")
        # list(...) validates the format now, so a bad file raises here at the call
        # site rather than later inside the background task.
        chunks = list(_iter_audio_chunks(path, self._format))
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

    def __init__(
        self,
        device: str | None,
        loop: asyncio.AbstractEventLoop,
        audio_format: AudioFormat = DEFAULT_AUDIO_FORMAT,
    ) -> None:
        self._device = device
        self._loop = loop
        self._format = audio_format
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

        fmt = self._format

        def _callback(indata: np.ndarray, frames: int, time, status) -> None:  # noqa: ARG001
            # indata is s16 PCM (dtype below); transcode to the target encoding.
            chunk = _encode_chunk(bytes(indata), fmt)
            self._loop.call_soon_threadsafe(queue.put_nowait, chunk)

        self._stream = sd.InputStream(
            samplerate=fmt.sample_rate,
            channels=fmt.channels,
            dtype=DTYPE,
            blocksize=fmt.frames_per_chunk,
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
