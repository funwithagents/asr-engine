"""Default-module (``fake``) e2e coverage for the public direct-engine APIs."""

from __future__ import annotations

import asyncio

import pytest
from helpers import (
    FIXTURE_BLUE_VALIDATE,
    FIXTURE_BLUE_WAV_16000,
    FORMAT_MP3_44100,
    FORMAT_WAV_16000,
    build_engine,
    build_file_engine,
    default_module,
    script_blue,
    script_blue_validate,
    wait_until,
)

from asr_engine import ScriptableAudioSource, SpeechSegment


@pytest.mark.asyncio
async def test_listen_pipeline() -> None:
    """listen transcribes 16 kHz input, closes on silence, and stops the engine."""
    module_type, module_config = default_module(script_blue())
    engine = build_file_engine(
        FIXTURE_BLUE_WAV_16000,
        module_type,
        module_config,
        audio_format=FORMAT_WAV_16000,
        end_of_speech_timeout_s=2.0,
        trailing_silence_s=3.0,
    )

    segment = await asyncio.wait_for(engine.listen(mode="timeout"), timeout=30.0)

    assert engine.audio_format == FORMAT_WAV_16000
    assert segment.is_final is True
    assert segment.end_reason == "end_of_speech_timeout"
    assert segment.transcript == "the sky is blue"
    assert engine.status() == {"running": False, "connected": False}


@pytest.mark.asyncio
async def test_dictation_pipeline() -> None:
    """dictation closes a trigger-word segment and reverts without stopping ASR."""
    # The fake's clock starts at engine.start(), before play(): delay the script
    # past the silence fed while dictation is being armed.
    module_type, module_config = default_module(script_blue_validate(delay_s=0.5))
    segments: list[SpeechSegment] = []

    async def on_segment(segment: SpeechSegment) -> None:
        segments.append(segment)

    source = ScriptableAudioSource(audio_format=FORMAT_MP3_44100)
    engine = build_engine(
        source,
        module_type,
        module_config,
        trigger_words=["validate"],
        on_speech_segment=on_segment,
    )
    try:
        await engine.start()
        await wait_until(lambda: engine.status()["connected"])
        await engine.start_dictation(
            end_on_final_segment=False, segmentation_mode="trigger_word"
        )

        await source.play(FIXTURE_BLUE_VALIDATE, trailing_silence_s=3.0)
        await wait_until(
            lambda: any(
                segment.is_final and segment.end_reason == "trigger_word"
                for segment in segments
            )
        )

        await engine.stop_dictation()
        assert engine.dictating is False
        assert engine.segmentation_mode == "utterance"
        assert engine.status() == {"running": True, "connected": True}
    finally:
        await engine.stop()

    closed = [segment for segment in segments if segment.is_final]
    assert closed[-1].end_reason == "trigger_word"
    # The trigger utterance closes the segment but is excluded from it.
    assert closed[-1].transcript == "the sky is blue"
