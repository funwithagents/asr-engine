"""Per-provider ASR module conformance, driven through ASREngine (no MCP server).

Parametrized over the module registry (see ``MODULES`` in ``helpers``): each backend
must, as the engine observes it, emit interim+final utterances with the right
transcript, surface an interim (open) segment, and finalize on silence into closed
segments. The module-agnostic stack (MCP resource/tool, asr-to-terminal) is covered
once elsewhere; here we vary only the module.

Run one module with ``-k``, e.g. ``uv run pytest tests-e2e -k deepgram_v1``.
Needs each module's API key env var set (see helpers); modules lacking one skip.
"""

from __future__ import annotations

import asyncio
import re

import pytest
from helpers import (
    FIXTURE_BLUE,
    FIXTURE_BLUE_VALIDATE,
    FIXTURE_BLUE_WAV_16000,
    FORMAT_MP3_44100,
    FORMAT_WAV_16000,
    MODULES,
    build_engine,
    build_file_engine,
    require_api_key,
)

from asr_engine import ScriptableAudioSource, SpeechSegment, SpeechUtterance


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()


async def _wait_until(predicate, timeout: float = 30.0) -> None:
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.1)
    raise TimeoutError("condition not met within timeout")


@pytest.mark.asyncio
@pytest.mark.parametrize("module_type, module_config, silence_s", MODULES)
async def test_engine_streams(
    module_type: str, module_config: dict, silence_s: float
) -> None:
    """Two utterances split by silence: interim+final utterances and interim+final segments."""
    require_api_key(module_config)
    utterances: list[SpeechUtterance] = []
    segments: list[SpeechSegment] = []

    async def on_utt(u: SpeechUtterance) -> None:
        utterances.append(u)

    async def on_seg(s: SpeechSegment) -> None:
        segments.append(s)

    source = ScriptableAudioSource(audio_format=FORMAT_MP3_44100)
    engine = build_engine(
        source,
        module_type,
        module_config,
        audio_format=FORMAT_MP3_44100,
        segmentation_mode="utterance",
        on_speech_utterance=on_utt,
        on_speech_segment=on_seg,
    )
    try:
        await engine.start()

        # First utterance: play, then wait for the engine to close its segment.
        await source.play(FIXTURE_BLUE, trailing_silence_s=silence_s)
        await _wait_until(lambda: any(s.is_final for s in segments))
        finals_after_first = sum(1 for s in segments if s.is_final)

        # Second utterance, sequenced after the first — a fresh segment closes.
        await source.play(FIXTURE_BLUE_VALIDATE, trailing_silence_s=silence_s)
        await _wait_until(
            lambda: sum(1 for s in segments if s.is_final) > finals_after_first
        )
    finally:
        await engine.stop()

    # Utterances: at least one interim and one final flowed through.
    assert any(not u.is_final for u in utterances), "expected an interim utterance"
    finals = [u for u in utterances if u.is_final]
    assert finals, "expected at least one final utterance"
    assert "the sky is blue" in _normalize(" ".join(u.transcript for u in finals))

    # Segments: an interim (open) segment was observed, plus a closed one per utterance.
    assert any(not s.is_final for s in segments), "expected an interim (open) segment"
    closed = [s for s in segments if s.is_final]
    assert len(closed) >= 2, "expected a closed segment per utterance"
    assert all(s.end_reason == "utterance" for s in closed)
    assert "the sky is blue" in _normalize(closed[0].transcript)
    # The second fixture adds the word "validate"; it lands in a later segment.
    assert "validate" in _normalize(" ".join(s.transcript for s in closed[1:]))


@pytest.mark.asyncio
@pytest.mark.parametrize("module_type, module_config, silence_s", MODULES)
async def test_listen_trigger_word(
    module_type: str, module_config: dict, silence_s: float
) -> None:
    """engine.listen(trigger_word) returns the closed segment, excluding the trigger."""
    require_api_key(module_config)
    engine = build_file_engine(
        FIXTURE_BLUE_VALIDATE,
        module_type,
        module_config,
        trigger_words=["validate"],
        trailing_silence_s=silence_s,
    )
    segment = await asyncio.wait_for(engine.listen(mode="trigger_word"), timeout=30.0)

    assert segment.is_final is True
    assert segment.end_reason == "trigger_word"
    assert "validate" not in _normalize(segment.transcript)
    assert engine.status()["running"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("module_type, module_config, silence_s", MODULES)
async def test_listen_timeout(
    module_type: str, module_config: dict, silence_s: float
) -> None:
    """engine.listen(timeout) closes on end-of-speech silence with the full transcript."""
    require_api_key(module_config)
    engine = build_file_engine(
        FIXTURE_BLUE,
        module_type,
        module_config,
        end_of_speech_timeout_s=2.0,
        trailing_silence_s=silence_s,
    )
    segment = await asyncio.wait_for(engine.listen(mode="timeout"), timeout=30.0)

    assert segment.is_final is True
    assert segment.end_reason == "end_of_speech_timeout"
    assert "the sky is blue" in _normalize(segment.transcript)


@pytest.mark.asyncio
@pytest.mark.parametrize("module_type, module_config, silence_s", MODULES)
async def test_sample_rate_compat_16000_wav(
    module_type: str, module_config: dict, silence_s: float
) -> None:
    """A different input format (16 kHz WAV) transcribes the same as the 44.1 kHz mp3.

    The default conformance tests run at 44.1 kHz from MP3; this pins that a module
    also works when the engine is configured for a 16 kHz WAV input.
    """
    require_api_key(module_config)
    engine = build_file_engine(
        FIXTURE_BLUE_WAV_16000,
        module_type,
        module_config,
        audio_format=FORMAT_WAV_16000,
        end_of_speech_timeout_s=2.0,
        trailing_silence_s=silence_s,
    )
    assert engine.audio_format == FORMAT_WAV_16000
    segment = await asyncio.wait_for(engine.listen(mode="timeout"), timeout=30.0)

    assert segment.is_final is True
    assert "the sky is blue" in _normalize(segment.transcript)
