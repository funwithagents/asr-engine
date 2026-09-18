"""Shared fixtures and test-environment setup."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

# Stub out sounddevice before any test module imports it so that the PortAudio
# shared library is never needed in the test environment.
_sd_mock = MagicMock()
sys.modules.setdefault("sounddevice", _sd_mock)


@pytest.fixture
def fake_engine_factory():
    """Build an ASREngine on the scripted ``fake`` module (specs/modules/fake.md).

    ``fake_engine_factory(utterances, **overrides)`` returns an engine fed by a
    ``ScriptableAudioSource(real_time=False)``, so the script replays on the audio
    clock as fast as the loop drains. Sound feedback is disabled. ``overrides``:
    ``trigger_words``, ``listen_default_segmentation_mode``,
    ``dictation_default_segmentation_mode``, ``on_speech_utterance``,
    ``on_speech_segment``.
    """
    # Imported lazily so the sounddevice stub above is installed first.
    from asr_engine import (
        ASREngine,
        ASREngineConfig,
        ModuleConfig,
        ScriptableAudioSource,
        SegmentationConfig,
        SoundFeedbackConfig,
    )

    def factory(
        utterances: list[dict],
        *,
        trigger_words: list[str] | None = None,
        listen_default_segmentation_mode: str = "trigger_word",
        dictation_default_segmentation_mode: str = "trigger_word",
        on_speech_utterance=None,
        on_speech_segment=None,
    ) -> ASREngine:
        segmentation = SegmentationConfig()
        if trigger_words is not None:
            segmentation.trigger_words = trigger_words
        config = ASREngineConfig(
            listen_default_segmentation_mode=listen_default_segmentation_mode,
            dictation_default_segmentation_mode=dictation_default_segmentation_mode,
            segmentation=segmentation,
            sound_feedback=SoundFeedbackConfig(enabled=False),
            module=ModuleConfig(type="fake", extra={"utterances": utterances}),
        )
        return ASREngine(
            config,
            on_speech_utterance=on_speech_utterance,
            on_speech_segment=on_speech_segment,
            audio_source=ScriptableAudioSource(real_time=False),
        )

    return factory
