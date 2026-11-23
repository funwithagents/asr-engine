"""Per-module ASR conformance, driven through ASREngine (no MCP server).

Parametrized over ``MODULES`` in ``helpers``: each backend must connect, emit
interim and final utterances, finalize on silence, remain usable for a second
utterance, and disconnect cleanly. Shared engine behavior is covered once with
the default module elsewhere.

``test_engine_modules_vad`` repeats the two-utterance scenario behind the Silero
VAD gate (``MODULES_VAD``, specs/vad.md): the gate drops the silence, so each
final has to come from the module's handling of the end-of-speech marker.

Run one module with ``-k``, e.g. ``uv run pytest tests-e2e -k deepgram-v1``.
Needs each module's API key env var set (see helpers); modules lacking one skip.
Local-model modules (kyutai, mlx-audio) run once their weights are cached: see
``helpers.require_local_model``. VAD-required (batch) modules run only behind the
gate: ``MODULES_UNGATED`` leaves them out of ``test_engine_streams``.

``test_engine_modules_converted`` runs the modules of ``MODULES_CONVERTED`` on
44.1 kHz audio they are not fed as such (specs/format-conversion.md): the engine
resamples it, and the backend must still recognize the speech.
"""

from __future__ import annotations

import pytest
from helpers import (
    FIXTURE_BLUE,
    FORMAT_MP3_44100,
    MODULES_CONVERTED,
    MODULES_UNGATED,
    MODULES_VAD,
    ModuleAudio,
    UtteranceRecorder,
    build_engine,
    normalize_transcript,
    require_api_key,
    require_local_model,
    require_vad_runtime,
    wait_until,
)

from asr_engine import ScriptableAudioSource


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "module_type, module_config, silence_s, audio", MODULES_UNGATED
)
async def test_engine_streams(
    module_type: str, module_config: dict, silence_s: float, audio: ModuleAudio
) -> None:
    """A module completes the live start/stream/finalize/reuse/stop lifecycle."""
    require_api_key(module_config)
    require_local_model(module_type, module_config)
    recorder = UtteranceRecorder()
    utterances = recorder.utterances

    source = ScriptableAudioSource(audio_format=audio.audio_format)
    engine = build_engine(
        source,
        module_type,
        module_config,
        audio_format=audio.audio_format,
        on_speech_utterance=recorder,
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
            ),
            describe=recorder.describe,
        )
        first_final_index = next(i for i, u in enumerate(utterances) if u.is_final)
        second_start_index = len(utterances)
        assert engine.status() == {"running": True, "connected": True, "vad": None}

        # The same connection remains usable after the first final utterance.
        await source.play(audio.blue_validate, trailing_silence_s=silence_s)
        await wait_until(
            lambda: any(
                u.is_final and "validate" in normalize_transcript(u.transcript)
                for u in utterances[second_start_index:]
            ),
            describe=recorder.describe,
        )
    finally:
        await engine.stop()

    assert any(not u.is_final for u in utterances[: first_final_index + 1]), (
        "expected an interim utterance before the first final"
    )
    assert engine.status() == {"running": False, "connected": False, "vad": None}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "module_type, module_config, silence_s, audio, vad", MODULES_VAD
)
async def test_engine_modules_vad(
    module_type: str,
    module_config: dict,
    silence_s: float,
    audio: ModuleAudio,
    vad: dict,
) -> None:
    """Behind the VAD gate a module still finalizes the whole of each utterance.

    The source feeds continuous silence around each fixture, as a microphone
    would; the gate forwards only the speech (with its pre-roll and hangover), so
    the backend never hears the long silence it normally finalizes on. An
    utterance may come out as several finals (specs/vad.md "Live tier"): together
    they must hold the whole phrase.
    """
    require_api_key(module_config)
    require_local_model(module_type, module_config)
    require_vad_runtime()
    recorder = UtteranceRecorder()
    utterances = recorder.utterances

    def finals(start: int = 0) -> list[str]:
        return [
            normalize_transcript(u.transcript) for u in utterances[start:] if u.is_final
        ]

    source = ScriptableAudioSource(audio_format=audio.audio_format)
    engine = build_engine(
        source,
        module_type,
        module_config,
        audio_format=audio.audio_format,
        on_speech_utterance=recorder,
        vad=vad,
    )
    try:
        await engine.start()
        await wait_until(lambda: engine.status()["connected"])
        assert engine.status()["vad"] == "silero"

        # First utterance: its finals must hold the whole phrase — the first word
        # (the pre-roll kept it) and the last (the flush brought it out).
        await source.play(audio.blue, trailing_silence_s=silence_s)
        await wait_until(
            lambda: "the sky is blue" in " ".join(finals()),
            describe=recorder.describe,
        )
        second_start_index = len(utterances)

        # Second utterance, after a gated gap: the module survived a stretch with
        # no audio at all and finalizes again.
        await source.play(audio.blue_validate, trailing_silence_s=silence_s)
        await wait_until(
            lambda: (
                "the sky is blue" in " ".join(finals(second_start_index))
                and "validate" in " ".join(finals(second_start_index))
            ),
            describe=recorder.describe,
        )
        assert engine.status()["connected"] is True
    finally:
        await engine.stop()

    assert engine.status() == {"running": False, "connected": False, "vad": "silero"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "module_type, module_config, silence_s, module_sample_rate, vad",
    MODULES_CONVERTED,
)
async def test_engine_modules_converted(
    module_type: str,
    module_config: dict,
    silence_s: float,
    module_sample_rate: int,
    vad: dict | None,
) -> None:
    """A module recognizes speech from a source whose rate it is not fed.

    The source plays the 44.1 kHz MP3; the engine resamples it to the module's
    rate. Only a live backend can show the converted audio is still intelligible.
    """
    require_api_key(module_config)
    require_local_model(module_type, module_config)
    if vad is not None:
        require_vad_runtime()
    recorder = UtteranceRecorder()
    utterances = recorder.utterances

    source = ScriptableAudioSource(audio_format=FORMAT_MP3_44100)
    engine = build_engine(
        source,
        module_type,
        module_config,
        audio_format=FORMAT_MP3_44100,
        module_sample_rate=module_sample_rate,
        on_speech_utterance=recorder,
        vad=vad,
    )
    # The source keeps its own rate; the module is fed another.
    assert engine.audio_format == FORMAT_MP3_44100
    assert engine.module_audio_format.sample_rate == module_sample_rate
    try:
        await engine.start()
        await wait_until(lambda: engine.status()["connected"])
        await source.play(FIXTURE_BLUE, trailing_silence_s=silence_s)
        await wait_until(
            lambda: any(
                u.is_final and "the sky is blue" in normalize_transcript(u.transcript)
                for u in utterances
            ),
            describe=recorder.describe,
        )
    finally:
        await engine.stop()
