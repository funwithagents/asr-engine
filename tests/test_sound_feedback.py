"""Unit tests for asr_mcp.sound_feedback — Plan 14."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from asr_mcp.sound_feedback import NoOpSoundFeedback, SoundFeedback


def _make_wave_mock(sampwidth: int = 2, n_frames: int = 4, framerate: int = 16000):
    """Return a mock wave file context manager."""
    mock_wf = MagicMock()
    mock_wf.getnchannels.return_value = 1
    mock_wf.getsampwidth.return_value = sampwidth
    mock_wf.getframerate.return_value = framerate
    mock_wf.getnframes.return_value = n_frames
    mock_wf.readframes.return_value = bytes(n_frames * sampwidth)
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=mock_wf)
    cm.__exit__ = MagicMock(return_value=False)
    return cm


@pytest.mark.asyncio
async def test_play_start_calls_sounddevice() -> None:
    wave_cm = _make_wave_mock()
    with (
        patch("asr_mcp.sound_feedback.wave.open", return_value=wave_cm) as mock_open,
        patch("asr_mcp.sound_feedback.sd.play") as mock_play,
        patch("asr_mcp.sound_feedback.sd.wait") as mock_wait,
    ):
        sf = SoundFeedback()
        await sf.play_start()

    assert "feedback_asr_start.wav" in str(mock_open.call_args[0][0])
    mock_play.assert_called_once()
    mock_wait.assert_called_once()


@pytest.mark.asyncio
async def test_play_stop_calls_sounddevice() -> None:
    wave_cm = _make_wave_mock()
    with (
        patch("asr_mcp.sound_feedback.wave.open", return_value=wave_cm) as mock_open,
        patch("asr_mcp.sound_feedback.sd.play") as mock_play,
        patch("asr_mcp.sound_feedback.sd.wait") as mock_wait,
    ):
        sf = SoundFeedback()
        await sf.play_stop()

    assert "feedback_asr_stop.wav" in str(mock_open.call_args[0][0])
    mock_play.assert_called_once()
    mock_wait.assert_called_once()


@pytest.mark.asyncio
async def test_output_device_passed_to_sd_play() -> None:
    wave_cm = _make_wave_mock()
    with (
        patch("asr_mcp.sound_feedback.wave.open", return_value=wave_cm),
        patch("asr_mcp.sound_feedback.sd.play") as mock_play,
        patch("asr_mcp.sound_feedback.sd.wait"),
    ):
        sf = SoundFeedback(output_device="speakers")
        await sf.play_start()

    _, kwargs = mock_play.call_args
    assert kwargs["device"] == "speakers"


@pytest.mark.asyncio
async def test_error_is_logged_not_raised(caplog) -> None:
    wave_cm = _make_wave_mock()
    with (
        patch("asr_mcp.sound_feedback.wave.open", return_value=wave_cm),
        patch("asr_mcp.sound_feedback.sd.play", side_effect=OSError("no device")),
        patch("asr_mcp.sound_feedback.sd.wait"),
    ):
        sf = SoundFeedback()
        import logging

        with caplog.at_level(logging.ERROR, logger="asr_mcp.sound_feedback"):
            await sf.play_start()  # must not raise

    assert any("start" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_noop_does_not_call_sounddevice() -> None:
    with patch("asr_mcp.sound_feedback.sd.play") as mock_play:
        sf = NoOpSoundFeedback()
        await sf.play_start()
        await sf.play_stop()

    mock_play.assert_not_called()
