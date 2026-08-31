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
    ScriptableAudioSource,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_device_list(*names: str):
    """Build a fake sounddevice device-info list with all devices as input-capable."""
    return [{"name": n, "max_input_channels": 1} for n in names]


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
