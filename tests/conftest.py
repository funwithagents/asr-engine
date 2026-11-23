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
        AudioSourceConfig,
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
            audio_source=AudioSourceConfig(type="custom"),
            module=ModuleConfig(type="fake", extra={"utterances": utterances}),
        )
        return ASREngine(
            config,
            on_speech_utterance=on_speech_utterance,
            on_speech_segment=on_speech_segment,
            audio_source=ScriptableAudioSource(real_time=False),
        )

    return factory


class ScriptedClock:
    """Stands in for the ``time`` module in ``asr_engine.segment_timing``: the
    wall clock every stamp is read from, moved by hand."""

    BASE = 1_800_000_000 * 1_000_000_000

    def __init__(self) -> None:
        self.now = self.BASE

    def advance_ms(self, ms: float) -> None:
        self.now += round(ms * 1_000_000)

    def at_ms(self, ms: float) -> int:
        """The stamp *ms* after the clock's start."""
        return self.BASE + round(ms * 1_000_000)

    def time_ns(self) -> int:
        return self.now


@pytest.fixture
def clock(monkeypatch) -> ScriptedClock:
    """The engine's wall clock, scripted (specs/segment-timing.md)."""
    from asr_engine import segment_timing

    scripted = ScriptedClock()
    monkeypatch.setattr(segment_timing, "time", scripted)
    return scripted


@pytest.fixture
def exporter(monkeypatch):
    """An OpenTelemetry SDK for the length of one test, as an application would
    install it; returns its in-memory span exporter."""
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    spans = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(spans))
    monkeypatch.setattr(trace, "get_tracer_provider", lambda: provider)
    return spans
