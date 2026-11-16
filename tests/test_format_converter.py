"""Tests for the source → module audio format conversion (specs/format-conversion.md)."""

from __future__ import annotations

import numpy as np
import pytest

from asr_engine.audio import AudioFormat, linear16_to_mulaw, mulaw_to_linear16
from asr_engine.format_converter import (
    AudioFormatConverter,
    ConvertedAudioSource,
    check_convertible,
)
from tests.vad.scripted import HandFedSource, settle

# The resampler holds back at most this much audio (its filter's look-ahead).
HOLD_S = 0.15


def tone(rate: int, seconds: float = 1.0, hz: float = 440.0) -> np.ndarray:
    """A sine at half scale, as int16 samples."""
    t = np.arange(round(rate * seconds)) / rate
    return (np.sin(2 * np.pi * hz * t) * 16000).astype("<i2")


def convert_in_chunks(
    converter: AudioFormatConverter, data: bytes, chunk_bytes: int
) -> bytes:
    return b"".join(
        converter.convert(data[i : i + chunk_bytes])
        for i in range(0, len(data), chunk_bytes)
    )


def dominant_hz(pcm: bytes, rate: int) -> float:
    samples = np.frombuffer(pcm, dtype="<i2").astype(np.float64)
    spectrum = np.abs(np.fft.rfft(samples * np.hanning(len(samples))))
    return float(np.fft.rfftfreq(len(samples), 1 / rate)[int(spectrum.argmax())])


# ------------------------------------------------------------------ resampling


@pytest.mark.parametrize(
    "source_rate, target_rate",
    [(44100, 16000), (44100, 24000), (16000, 24000), (48000, 8000)],
)
def test_resampling_keeps_the_duration_the_pitch_and_the_level(
    source_rate: int, target_rate: int
) -> None:
    source, target = AudioFormat(source_rate), AudioFormat(target_rate)
    converter = AudioFormatConverter(source, target)
    out = convert_in_chunks(converter, tone(source_rate).tobytes(), source.chunk_bytes)

    samples = np.frombuffer(out, dtype="<i2")
    # One second in, one second out — less what the resampler still holds.
    assert target_rate * (1 - HOLD_S) <= len(samples) <= target_rate
    assert dominant_hz(out, target_rate) == pytest.approx(440, abs=2)
    # The tone's peak survives (no clipping, no attenuation), edges aside.
    assert np.abs(samples[1000:-1000]).max() == pytest.approx(16000, rel=0.02)


def test_the_output_does_not_depend_on_how_the_stream_is_chunked() -> None:
    source, target = AudioFormat(44100), AudioFormat(16000)
    data = tone(44100).tobytes()
    regular = convert_in_chunks(AudioFormatConverter(source, target), data, 8820)
    # 1001 bytes: every other chunk ends in the middle of a sample.
    ragged = convert_in_chunks(AudioFormatConverter(source, target), data, 1001)

    a = np.frombuffer(regular, dtype="<i2").astype(np.int32)
    b = np.frombuffer(ragged, dtype="<i2").astype(np.int32)
    n = min(len(a), len(b))
    assert abs(len(a) - len(b)) < 16000 * HOLD_S
    # The same signal, to the rounding of each int16 sample.
    assert np.abs(a[:n] - b[:n]).max() <= 4


def test_frame_counters_map_the_output_back_to_the_source() -> None:
    converter = AudioFormatConverter(AudioFormat(44100), AudioFormat(16000))
    out = convert_in_chunks(converter, tone(44100, seconds=2.0).tobytes(), 8820)

    assert converter.frames_in == 88200
    assert converter.frames_out == len(out) // 2
    # Output time trails input time by the resampler's hold only.
    lag_s = converter.frames_in / 44100 - converter.frames_out / 16000
    assert 0 <= lag_s < HOLD_S


# -------------------------------------------------------- channels and encoding


def test_stereo_is_downmixed_to_the_average_of_its_channels() -> None:
    stereo = np.array([[1000, 3000], [-2000, -4000], [32767, 32767]], dtype="<i2")
    converter = AudioFormatConverter(AudioFormat(16000, 2), AudioFormat(16000, 1))
    out = np.frombuffer(converter.convert(stereo.tobytes()), dtype="<i2")
    assert out.tolist() == [2000, -3000, 32767]  # no overflow at full scale


def test_mono_is_duplicated_onto_every_channel() -> None:
    mono = np.array([100, -200, 300], dtype="<i2")
    converter = AudioFormatConverter(AudioFormat(16000, 1), AudioFormat(16000, 2))
    out = np.frombuffer(converter.convert(mono.tobytes()), dtype="<i2")
    assert out.tolist() == [100, 100, -200, -200, 300, 300]


def test_a_frame_split_across_chunks_is_reassembled() -> None:
    stereo = np.array([[1000, 3000], [-2000, -4000]], dtype="<i2").tobytes()
    converter = AudioFormatConverter(AudioFormat(16000, 2), AudioFormat(16000, 1))
    # 3 bytes: less than one 4-byte stereo frame, so nothing can come out yet.
    assert converter.convert(stereo[:3]) == b""
    out = np.frombuffer(converter.convert(stereo[3:]), dtype="<i2")
    assert out.tolist() == [2000, -3000]


def test_linear16_is_encoded_to_mulaw_and_back() -> None:
    pcm = tone(8000, seconds=0.1).tobytes()
    to_mulaw = AudioFormatConverter(
        AudioFormat(8000, 1, "linear16"), AudioFormat(8000, 1, "mulaw")
    )
    mulaw = to_mulaw.convert(pcm)
    assert mulaw == linear16_to_mulaw(pcm)  # one byte per sample

    to_linear = AudioFormatConverter(
        AudioFormat(8000, 1, "mulaw"), AudioFormat(8000, 1, "linear16")
    )
    assert to_linear.convert(mulaw) == mulaw_to_linear16(mulaw)


def test_every_dimension_converts_at_once() -> None:
    # 8 kHz stereo μ-law telephony audio → 16 kHz mono linear16.
    stereo = np.repeat(tone(8000).reshape(-1, 1), 2, axis=1)
    source = AudioFormat(8000, 2, "mulaw")
    converter = AudioFormatConverter(source, AudioFormat(16000, 1, "linear16"))
    out = convert_in_chunks(
        converter, linear16_to_mulaw(stereo.tobytes()), source.chunk_bytes
    )
    assert 16000 * (1 - HOLD_S) <= len(out) // 2 <= 16000
    assert dominant_hz(out, 16000) == pytest.approx(440, abs=2)


def test_channel_layouts_with_no_mono_side_cannot_be_converted() -> None:
    check_convertible(AudioFormat(16000, 2), AudioFormat(48000, 1, "mulaw"))  # fine
    with pytest.raises(ValueError, match="Cannot convert 2 audio channels to 6"):
        check_convertible(AudioFormat(16000, 2), AudioFormat(16000, 6))
    with pytest.raises(ValueError, match="Cannot convert 2 audio channels to 6"):
        AudioFormatConverter(AudioFormat(16000, 2), AudioFormat(16000, 6))


# -------------------------------------------------------- ConvertedAudioSource


@pytest.mark.asyncio
async def test_the_wrapped_source_delivers_converted_chunks_in_order() -> None:
    inner = HandFedSource()
    source_format, target_format = AudioFormat(44100), AudioFormat(16000)
    converted = ConvertedAudioSource(inner, source_format, target_format)
    queue = converted.start()

    data = tone(44100).tobytes()
    for i in range(0, len(data), source_format.chunk_bytes):
        inner.queue.put_nowait(data[i : i + source_format.chunk_bytes])
    await settle()

    out = b""
    while not queue.empty():
        out += queue.get_nowait()
    expected = convert_in_chunks(
        AudioFormatConverter(source_format, target_format),
        data,
        source_format.chunk_bytes,
    )
    assert out == expected
    converted.stop()
    assert (inner.starts, inner.stops) == (1, 1)


@pytest.mark.asyncio
async def test_a_restart_begins_a_fresh_stream() -> None:
    inner = HandFedSource()
    # Stereo → mono with a dangling half frame left over from the first run.
    converted = ConvertedAudioSource(
        inner, AudioFormat(16000, 2), AudioFormat(16000, 1)
    )
    converted.start()
    inner.queue.put_nowait(b"\x01\x02\x03")  # an incomplete frame
    await settle()
    converted.stop()

    queue = converted.start()
    frame = np.array([[1000, 3000]], dtype="<i2").tobytes()
    inner.queue.put_nowait(frame)
    await settle()
    # The first run's leftover bytes did not shift the second run's frames.
    assert np.frombuffer(queue.get_nowait(), dtype="<i2").tolist() == [2000]
    converted.stop()
    assert (inner.starts, inner.stops) == (2, 2)


@pytest.mark.asyncio
async def test_a_chunk_that_fails_to_convert_is_dropped_and_the_stream_goes_on(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    real_convert = AudioFormatConverter.convert

    def convert(self: AudioFormatConverter, chunk: bytes) -> bytes:
        if chunk == b"bad!":
            raise RuntimeError("cannot convert")
        return real_convert(self, chunk)

    monkeypatch.setattr(AudioFormatConverter, "convert", convert)
    inner = HandFedSource()
    converted = ConvertedAudioSource(
        inner, AudioFormat(16000, 2), AudioFormat(16000, 1)
    )
    queue = converted.start()
    good = np.array([[1000, 3000]], dtype="<i2").tobytes()
    for chunk in (b"bad!", good, b"bad!", good):
        inner.queue.put_nowait(chunk)
    await settle()

    assert queue.qsize() == 2  # both good chunks came through
    failures = [r for r in caplog.records if "conversion failed" in r.message]
    assert len(failures) == 1  # logged once, not once per chunk
    converted.stop()
