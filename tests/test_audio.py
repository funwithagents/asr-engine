"""Unit tests for AudioCapture and ScriptableAudioSource."""

from __future__ import annotations

import asyncio
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from asr_engine.audio import (
    CHANNELS,
    CHUNK_SAMPLES,
    DTYPE,
    SAMPLE_RATE,
    AudioCapture,
    AudioFormat,
    FileAudioSource,
    ScriptableAudioSource,
    linear16_to_mulaw,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_device_list(*names: str):
    """Build a fake sounddevice device-info list with all devices as input-capable."""
    return [{"name": n, "max_input_channels": 1} for n in names]


def _reference_mulaw(sample: int) -> int:
    """Scalar CCITT G.711 μ-law encoder, independent of the numpy vectorized one."""
    bias = 0x84
    clip = 32635
    sign = 0x80 if sample < 0 else 0x00
    sample = min(abs(sample), clip) + bias
    exponent = 7
    mask = 0x4000
    while exponent > 0 and not (sample & mask):
        exponent -= 1
        mask >>= 1
    mantissa = (sample >> (exponent + 3)) & 0x0F
    return ~(sign | (exponent << 4) | mantissa) & 0xFF


# ---------------------------------------------------------------------------
# AudioFormat
# ---------------------------------------------------------------------------


class TestAudioFormat:
    def test_defaults_match_the_16k_mono_s16_contract(self):
        fmt = AudioFormat()
        assert (fmt.sample_rate, fmt.channels, fmt.encoding) == (16000, 1, "linear16")
        assert fmt.bytes_per_sample == 2
        assert fmt.frames_per_chunk == CHUNK_SAMPLES
        assert fmt.chunk_bytes == CHUNK_SAMPLES * 2
        assert fmt.chunk_duration_s == pytest.approx(0.1)

    def test_chunk_math_scales_with_rate_and_encoding(self):
        fmt = AudioFormat(sample_rate=48000, channels=1, encoding="mulaw")
        assert fmt.frames_per_chunk == 4800  # ~100 ms at 48 kHz
        assert fmt.bytes_per_sample == 1  # μ-law packs one byte per sample
        assert fmt.chunk_bytes == 4800
        assert fmt.chunk_duration_s == pytest.approx(0.1)

    def test_rejects_unknown_encoding(self):
        with pytest.raises(ValueError, match="encoding"):
            AudioFormat(encoding="opus")

    def test_rejects_nonpositive_rate(self):
        with pytest.raises(ValueError, match="sample_rate"):
            AudioFormat(sample_rate=0)


# ---------------------------------------------------------------------------
# linear16_to_mulaw
# ---------------------------------------------------------------------------


class TestLinear16ToMulaw:
    def test_silence_encodes_to_0xff(self):
        """μ-law silence is 0xFF, not 0x00 — the reason silence is transcoded."""
        out = linear16_to_mulaw(b"\x00\x00" * 10)
        assert out == b"\xff" * 10

    def test_matches_scalar_reference_across_range(self):
        samples = [-32768, -20000, -1, 0, 1, 100, 5000, 20000, 32767]
        pcm = np.array(samples, dtype="<i2").tobytes()
        got = linear16_to_mulaw(pcm)
        expected = bytes(_reference_mulaw(s) for s in samples)
        assert got == expected

    def test_output_is_one_byte_per_sample(self):
        pcm = np.zeros(50, dtype="<i2").tobytes()  # 50 samples, 100 bytes
        assert len(linear16_to_mulaw(pcm)) == 50


# ---------------------------------------------------------------------------
# list_devices
# ---------------------------------------------------------------------------


class TestListDevices:
    def test_returns_names_of_input_devices(self):
        fake_devices = [
            {"name": "Mic A", "max_input_channels": 2},
            {"name": "Speaker", "max_input_channels": 0},
            {"name": "Mic B", "max_input_channels": 1},
        ]
        with patch("asr_engine.audio.sd.query_devices", return_value=fake_devices):
            result = AudioCapture.list_devices()
        assert result == ["Mic A", "Mic B"]

    def test_empty_when_no_input_devices(self):
        fake_devices = [{"name": "Speaker", "max_input_channels": 0}]
        with patch("asr_engine.audio.sd.query_devices", return_value=fake_devices):
            assert AudioCapture.list_devices() == []

    def test_empty_when_no_devices_at_all(self):
        with patch("asr_engine.audio.sd.query_devices", return_value=[]):
            assert AudioCapture.list_devices() == []


# ---------------------------------------------------------------------------
# start — stream creation parameters
# ---------------------------------------------------------------------------


class TestStart:
    def test_returns_queue(self):
        loop = asyncio.new_event_loop()
        try:
            capture = AudioCapture(device=None, loop=loop)
            mock_stream = MagicMock()
            with patch("asr_engine.audio.sd.InputStream", return_value=mock_stream):
                with patch.object(
                    AudioCapture, "list_devices", return_value=["Default"]
                ):
                    q = capture.start()
            assert isinstance(q, asyncio.Queue)
        finally:
            loop.close()

    def test_stream_created_with_correct_params(self):
        loop = asyncio.new_event_loop()
        try:
            capture = AudioCapture(device=None, loop=loop)
            mock_stream = MagicMock()
            with patch(
                "asr_engine.audio.sd.InputStream", return_value=mock_stream
            ) as mock_cls:
                capture.start()
            _, kwargs = mock_cls.call_args
            assert kwargs["samplerate"] == SAMPLE_RATE
            assert kwargs["channels"] == CHANNELS
            assert kwargs["dtype"] == DTYPE
            assert kwargs["blocksize"] == CHUNK_SAMPLES
            assert kwargs["device"] is None
        finally:
            loop.close()

    def test_stream_started(self):
        loop = asyncio.new_event_loop()
        try:
            capture = AudioCapture(device=None, loop=loop)
            mock_stream = MagicMock()
            with patch("asr_engine.audio.sd.InputStream", return_value=mock_stream):
                capture.start()
            mock_stream.start.assert_called_once()
        finally:
            loop.close()

    def test_named_device_passed_to_stream(self):
        loop = asyncio.new_event_loop()
        try:
            capture = AudioCapture(device="Mic A", loop=loop)
            mock_stream = MagicMock()
            with patch(
                "asr_engine.audio.sd.InputStream", return_value=mock_stream
            ) as mock_cls:
                with patch.object(
                    AudioCapture, "list_devices", return_value=["Mic A", "Mic B"]
                ):
                    capture.start()
            _, kwargs = mock_cls.call_args
            assert kwargs["device"] == "Mic A"
        finally:
            loop.close()

    def test_raises_on_unknown_device_lists_available(self):
        loop = asyncio.new_event_loop()
        try:
            capture = AudioCapture(device="Bad", loop=loop)
            with patch.object(
                AudioCapture, "list_devices", return_value=["Mic A", "Mic B"]
            ):
                with pytest.raises(ValueError, match="Bad") as exc_info:
                    capture.start()
            msg = str(exc_info.value)
            assert "Mic A" in msg
            assert "Mic B" in msg
        finally:
            loop.close()

    def test_error_message_shows_none_when_no_devices(self):
        loop = asyncio.new_event_loop()
        try:
            capture = AudioCapture(device="Bad", loop=loop)
            with patch.object(AudioCapture, "list_devices", return_value=[]):
                with pytest.raises(ValueError, match=r"\(none\)"):
                    capture.start()
        finally:
            loop.close()


# ---------------------------------------------------------------------------
# stop
# ---------------------------------------------------------------------------


class TestStop:
    def test_stop_calls_stream_stop_and_close(self):
        loop = asyncio.new_event_loop()
        try:
            capture = AudioCapture(device=None, loop=loop)
            mock_stream = MagicMock()
            with patch("asr_engine.audio.sd.InputStream", return_value=mock_stream):
                capture.start()
            capture.stop()
            mock_stream.stop.assert_called_once()
            mock_stream.close.assert_called_once()
        finally:
            loop.close()

    def test_stop_is_idempotent(self):
        loop = asyncio.new_event_loop()
        try:
            capture = AudioCapture(device=None, loop=loop)
            mock_stream = MagicMock()
            with patch("asr_engine.audio.sd.InputStream", return_value=mock_stream):
                capture.start()
            capture.stop()
            # second stop should not raise
            capture.stop()
            assert mock_stream.stop.call_count == 1
        finally:
            loop.close()


# ---------------------------------------------------------------------------
# audio callback
# ---------------------------------------------------------------------------


class TestAudioCallback:
    def test_callback_puts_bytes_on_queue_via_call_soon_threadsafe(self):
        loop = asyncio.new_event_loop()
        try:
            capture = AudioCapture(device=None, loop=loop)
            captured_callback = None

            def fake_input_stream(**kwargs):
                nonlocal captured_callback
                captured_callback = kwargs["callback"]
                return MagicMock()

            with patch(
                "asr_engine.audio.sd.InputStream", side_effect=fake_input_stream
            ):
                q = capture.start()

            assert captured_callback is not None

            # Simulate callback invocation from audio thread
            fake_data = np.zeros((CHUNK_SAMPLES, 1), dtype=np.int16)
            expected_bytes = bytes(fake_data)

            loop.run_until_complete(
                _invoke_callback_and_drain(captured_callback, fake_data, loop, q)
            )
            result = q.get_nowait()
            assert result == expected_bytes
        finally:
            loop.close()

    def test_callback_chunk_size_matches_blocksize(self):
        """Each chunk should be CHUNK_SAMPLES * 2 bytes (int16 = 2 bytes/sample, mono)."""
        loop = asyncio.new_event_loop()
        try:
            capture = AudioCapture(device=None, loop=loop)
            captured_callback = None

            def fake_input_stream(**kwargs):
                nonlocal captured_callback
                captured_callback = kwargs["callback"]
                return MagicMock()

            with patch(
                "asr_engine.audio.sd.InputStream", side_effect=fake_input_stream
            ):
                q = capture.start()

            fake_data = np.random.randint(
                -32768, 32767, (CHUNK_SAMPLES, 1), dtype=np.int16
            )
            loop.run_until_complete(
                _invoke_callback_and_drain(captured_callback, fake_data, loop, q)
            )
            chunk = q.get_nowait()
            assert len(chunk) == CHUNK_SAMPLES * 2  # int16 = 2 bytes per sample
        finally:
            loop.close()


async def _invoke_callback_and_drain(callback, data, loop, q):
    """Call the sounddevice callback and let call_soon_threadsafe deliver the item."""
    callback(data, len(data), None, None)
    # yield control so the call_soon_threadsafe scheduled put_nowait runs
    await asyncio.sleep(0)


# ---------------------------------------------------------------------------
# ScriptableAudioSource
# ---------------------------------------------------------------------------

_SILENCE = b"\x00" * (CHUNK_SAMPLES * 2)


def _write_wav(path: Path, chunks: list[bytes]) -> Path:
    """Write a 16 kHz mono s16 WAV whose frames are the concatenated ``chunks``."""
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(b"".join(chunks))
    return path


async def _drain(q: asyncio.Queue[bytes], collected: list[bytes]) -> None:
    while True:
        collected.append(await q.get())


class TestScriptableAudioSource:
    @pytest.mark.asyncio
    async def test_plays_file_audio_contiguously_between_silence(
        self, tmp_path: Path
    ) -> None:
        """play() feeds exactly the file's chunks, in order, surrounded by silence."""
        # Distinct, non-silent chunks so they're unmistakable against silence.
        file_chunks = [bytes([n]) * (CHUNK_SAMPLES * 2) for n in (1, 2, 3, 4, 5)]
        wav = _write_wav(tmp_path / "tone.wav", file_chunks)

        source = ScriptableAudioSource(real_time=False)
        q = source.start()
        collected: list[bytes] = []
        drain_task = asyncio.create_task(_drain(q, collected))
        try:
            # Let a little idle silence flow before the file plays.
            await asyncio.sleep(0)
            await source.play(wav, trailing_silence_s=0.3)
            await asyncio.sleep(0)
        finally:
            source.stop()
            drain_task.cancel()
        while not q.empty():
            collected.append(q.get_nowait())

        # The only non-silence audio is the played file, whole and in order.
        non_silence = [c for c in collected if c != _SILENCE]
        assert non_silence == file_chunks
        # Silence flows around it (idle before + trailing after).
        assert _SILENCE in collected

    @pytest.mark.asyncio
    async def test_sequences_two_files_in_call_order(self, tmp_path: Path) -> None:
        """Two play() calls feed both files' audio back to back, in call order."""
        a = [bytes([1]) * (CHUNK_SAMPLES * 2), bytes([2]) * (CHUNK_SAMPLES * 2)]
        b = [bytes([3]) * (CHUNK_SAMPLES * 2)]
        wav_a = _write_wav(tmp_path / "a.wav", a)
        wav_b = _write_wav(tmp_path / "b.wav", b)

        source = ScriptableAudioSource(real_time=False)
        q = source.start()
        collected: list[bytes] = []
        drain_task = asyncio.create_task(_drain(q, collected))
        try:
            await source.play(wav_a, trailing_silence_s=0.2)
            await source.play(wav_b, trailing_silence_s=0.2)
            await asyncio.sleep(0)
        finally:
            source.stop()
            drain_task.cancel()
        while not q.empty():
            collected.append(q.get_nowait())

        non_silence = [c for c in collected if c != _SILENCE]
        assert non_silence == a + b

    @pytest.mark.asyncio
    async def test_play_before_start_raises(self, tmp_path: Path) -> None:
        wav = _write_wav(tmp_path / "x.wav", [bytes([1]) * (CHUNK_SAMPLES * 2)])
        source = ScriptableAudioSource()
        with pytest.raises(RuntimeError, match="before start"):
            await source.play(wav)

    @pytest.mark.asyncio
    async def test_play_rejects_wrong_format_file(self, tmp_path: Path) -> None:
        """A WAV that violates the 16 kHz mono s16 contract raises ValueError."""
        wrong = tmp_path / "8k.wav"
        with wave.open(str(wrong), "wb") as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(2)
            wf.setframerate(8000)  # wrong sample rate
            wf.writeframes(b"\x00" * (CHUNK_SAMPLES * 2))

        source = ScriptableAudioSource(real_time=False)
        source.start()
        try:
            with pytest.raises(ValueError, match="sample rate"):
                await source.play(wrong)
        finally:
            source.stop()

    @pytest.mark.asyncio
    async def test_play_validates_against_configured_format(
        self, tmp_path: Path
    ) -> None:
        """A file at the default 16 kHz is rejected when the source wants 48 kHz."""
        wav = _write_wav(tmp_path / "16k.wav", [b"\x00" * (CHUNK_SAMPLES * 2)])
        source = ScriptableAudioSource(
            audio_format=AudioFormat(sample_rate=48000), real_time=False
        )
        source.start()
        try:
            with pytest.raises(ValueError, match="sample rate"):
                await source.play(wav)
        finally:
            source.stop()


# ---------------------------------------------------------------------------
# FileAudioSource — format handling
# ---------------------------------------------------------------------------


def _write_pcm_wav(path: Path, pcm: bytes, sample_rate: int) -> Path:
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return path


async def _collect_file_chunks(
    path: Path, fmt: AudioFormat, *, timeout: float = 0.5
) -> list[bytes]:
    """Drain every chunk a FileAudioSource emits for *path* under *fmt*."""
    source = FileAudioSource(path, audio_format=fmt)
    q = source.start()
    chunks: list[bytes] = []
    try:
        while True:
            chunks.append(await asyncio.wait_for(q.get(), timeout=timeout))
    except asyncio.TimeoutError:
        pass
    finally:
        source.stop()
    return chunks


class TestFileAudioSourceFormat:
    @pytest.mark.asyncio
    async def test_streams_chunks_sized_for_a_non_default_rate(
        self, tmp_path: Path
    ) -> None:
        fmt = AudioFormat(sample_rate=48000)
        frames = fmt.frames_per_chunk  # 4800 at 48 kHz
        pcm = (np.arange(frames * 2, dtype="<i2") % 100 + 1).tobytes()
        wav = _write_pcm_wav(tmp_path / "48k.wav", pcm, 48000)

        chunks = await _collect_file_chunks(wav, fmt)

        assert [len(c) for c in chunks] == [frames * 2, frames * 2]

    @pytest.mark.asyncio
    async def test_transcodes_file_audio_to_mulaw(self, tmp_path: Path) -> None:
        fmt = AudioFormat(sample_rate=16000, encoding="mulaw")
        frames = fmt.frames_per_chunk
        wav = _write_pcm_wav(
            tmp_path / "sil.wav", np.zeros(frames, dtype="<i2").tobytes(), 16000
        )

        chunks = await _collect_file_chunks(wav, fmt)

        # One chunk, μ-law: one byte per sample (half of s16) and silence is 0xFF.
        assert len(chunks) == 1
        assert chunks[0] == b"\xff" * frames
