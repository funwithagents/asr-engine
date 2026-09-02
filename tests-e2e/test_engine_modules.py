"""Per-module ASR conformance, driven through ASREngine (no MCP server).

Parametrized over ``MODULES`` in ``helpers``: each backend must connect, emit
interim and final utterances, finalize on silence, remain usable for a second
utterance, and disconnect cleanly. Shared engine behavior is covered once with
the default module elsewhere.

Run one module with ``-k``, e.g. ``uv run pytest tests-e2e -k deepgram_v1``.
Needs each module's API key env var set (see helpers); modules lacking one skip.
"""

from __future__ import annotations

import pytest
from helpers import (
    FIXTURE_BLUE,
    FIXTURE_BLUE_VALIDATE,
    FORMAT_MP3_44100,
    MODULES,
    build_engine,
    normalize_transcript,
    require_api_key,
    wait_until,
)

from asr_engine import ScriptableAudioSource, SpeechUtterance


@pytest.mark.asyncio
@pytest.mark.parametrize("module_type, module_config, silence_s", MODULES)
async def test_engine_streams(
    module_type: str, module_config: dict, silence_s: float
) -> None:
    """A module completes the live start/stream/finalize/reuse/stop lifecycle."""
    require_api_key(module_config)
    utterances: list[SpeechUtterance] = []

    async def on_utt(u: SpeechUtterance) -> None:
        utterances.append(u)

    source = ScriptableAudioSource(audio_format=FORMAT_MP3_44100)
    engine = build_engine(
        source,
        module_type,
        module_config,
        audio_format=FORMAT_MP3_44100,
        on_speech_utterance=on_utt,
    )
    try:
        await engine.start()
        await wait_until(lambda: engine.status()["connected"])

        # Silence after the first fixture must make the module commit a final.
        await source.play(FIXTURE_BLUE, trailing_silence_s=silence_s)
        await wait_until(
            lambda: any(
                u.is_final and "the sky is blue" in normalize_transcript(u.transcript)
                for u in utterances
            )
        )
        first_final_index = next(i for i, u in enumerate(utterances) if u.is_final)
        second_start_index = len(utterances)
        assert engine.status() == {"running": True, "connected": True}

        # The same connection remains usable after the first final utterance.
        await source.play(FIXTURE_BLUE_VALIDATE, trailing_silence_s=silence_s)
        await wait_until(
            lambda: any(
                u.is_final and "validate" in normalize_transcript(u.transcript)
                for u in utterances[second_start_index:]
            )
        )
    finally:
        await engine.stop()

    assert any(not u.is_final for u in utterances[: first_final_index + 1]), (
        "expected an interim utterance before the first final"
    )
    assert engine.status() == {"running": False, "connected": False}
