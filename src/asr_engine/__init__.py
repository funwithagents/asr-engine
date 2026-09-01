"""ASR Engine — a real-time speech-recognition engine usable by direct import.

The public surface for programs that ``import asr_engine`` (rather than running
the bundled MCP server): construct an :class:`ASREngine` from an
:class:`ASREngineConfig`, drive it with an audio source, and consume the utterance
and segment streams. See specs/engine.md and specs/e2e-testing.md.
"""

import logging

from asr_engine.audio import (
    AudioCapture,
    AudioSource,
    FileAudioSource,
    ScriptableAudioSource,
)
from asr_engine.config import (
    ASREngineConfig,
    AudioConfig,
    ModuleConfig,
    SegmentationConfig,
    SoundFeedbackConfig,
)
from asr_engine.engine import ASREngine
from asr_engine.modules.base import SpeechUtterance
from asr_engine.segmenter import SpeechSegment
from asr_engine.tools import AsrTools

# Library convention: never configure logging (no handlers, no levels, no
# basicConfig). Attach a NullHandler to the package logger so a program that
# imports asr_engine and configures nothing drops records silently instead of
# hitting the "last resort" handler. Applications/entry points own configuration
# via asr_engine._logging.setup_logging().
logging.getLogger("asr_engine").addHandler(logging.NullHandler())

__all__ = [
    # Engine + tools
    "ASREngine",
    "AsrTools",
    # Config
    "ASREngineConfig",
    "AudioConfig",
    "ModuleConfig",
    "SegmentationConfig",
    "SoundFeedbackConfig",
    # Audio sources
    "AudioSource",
    "AudioCapture",
    "FileAudioSource",
    "ScriptableAudioSource",
    # Data types
    "SpeechUtterance",
    "SpeechSegment",
]
