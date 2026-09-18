"""Per-module ASR conformance, driven through ASREngine (no MCP server).

Parametrized over ``MODULES`` in ``helpers``: each backend must connect, emit
interim and final utterances, finalize on silence, remain usable for a second
utterance, and disconnect cleanly. Shared engine behavior is covered once with
the default module elsewhere.

Run one module with ``-k``, e.g. ``uv run pytest tests-e2e -k deepgram_v1``.
Needs each module's API key env var set (see helpers); modules lacking one skip.
Local-model modules (kyutai) are opt-in instead: see ``helpers.require_local_model``.
"""

from __future__ import annotations

import pytest
from helpers import (
    MODULES,
    ModuleAudio,
    build_engine,
    normalize_transcript,
    require_api_key,
    require_local_model,
    wait_until,
)

from asr_engine import ScriptableAudioSource, SpeechUtterance


@pytest.mark.asyncio
@pytest.mark.parametrize("module_type, module_config, silence_s, audio", MODULES)
async def test_engine_streams(
    module_type: str, module_config: dict, silence_s: float, audio: ModuleAudio
) -> None:
    """A module completes the live start/stream/finalize/reuse/stop lifecycle."""
    require_api_key(module_config)
    require_local_model(module_type, module_config)
    utterances: list[SpeechUtterance] = []

    async def on_utt(u: SpeechUtterance) -> None:
        utterances.append(u)

    source = ScriptableAudioSource(audio_format=audio.audio_format)
    engine = build_engine(
        source,
        module_type,
        module_config,
        audio_format=audio.audio_format,
        on_speech_utterance=on_utt,
    )
    try:
        await engine.start()
        await wait_until(lambda: engine.status()["connected"])

        # Silence after the first fixture must make the module commit a final.
        await source.play(audio.blue, trailing_silence_s=silence_s)
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
        await source.play(audio.blue_validate, trailing_silence_s=silence_s)
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
