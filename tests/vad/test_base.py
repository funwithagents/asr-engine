"""The ``VADModule`` interface and the VAD registry (specs/vad.md)."""

from __future__ import annotations

import sys
from unittest.mock import patch

import numpy as np
import pytest

from asr_engine.vad import REGISTRY, resolve_vad_class
from asr_engine.vad.base import VADModule
from tests.vad.scripted import ScriptedVAD


def _define(**attrs: object) -> type[VADModule]:
    """Define a concrete VADModule subclass carrying *attrs* (raises at class
    creation when the declaration is invalid)."""

    body = {
        "frame_samples": lambda self, sample_rate: 160,
        "load": lambda self: None,
        "process_frame": lambda self, frame, sample_rate: 0.0,
        "reset": lambda self: None,
        **attrs,
    }
    return type("CandidateVAD", (VADModule,), body)


def test_a_complete_declaration_is_accepted_and_usable():
    cls = _define(SUPPORTED_SAMPLE_RATES=frozenset({16000}), DEFAULT_SAMPLE_RATE=16000)
    vad = cls({"anything": 1})
    assert vad.config == {"anything": 1}
    assert vad.process_frame(np.zeros(160, dtype=np.float32), 16000) == 0.0


@pytest.mark.parametrize(
    "attrs, missing",
    [
        ({"DEFAULT_SAMPLE_RATE": 16000}, "SUPPORTED_SAMPLE_RATES"),
        ({"SUPPORTED_SAMPLE_RATES": frozenset({16000})}, "DEFAULT_SAMPLE_RATE"),
    ],
)
def test_a_missing_declared_attribute_raises_at_class_creation(attrs, missing):
    with pytest.raises(TypeError, match=missing):
        _define(**attrs)


def test_a_default_rate_outside_the_supported_set_raises_at_class_creation():
    with pytest.raises(TypeError, match="DEFAULT_SAMPLE_RATE=8000"):
        _define(SUPPORTED_SAMPLE_RATES=frozenset({16000}), DEFAULT_SAMPLE_RATE=8000)


def test_an_abstract_intermediate_base_needs_no_declaration():
    class Intermediate(VADModule):  # still abstract: declares nothing
        def load(self) -> None:
            pass

    with pytest.raises(TypeError):  # abstract: cannot be instantiated
        Intermediate({})  # type: ignore[abstract]


def test_unknown_type_lists_the_available_ones():
    with pytest.raises(ValueError, match=r"Unknown VAD type 'nope'.*silero"):
        resolve_vad_class("nope")


def test_missing_extra_names_the_install_command(monkeypatch):
    monkeypatch.setitem(sys.modules, "onnxruntime", None)  # None → not installed
    with pytest.raises(ImportError, match=r"asr-engine\[silero\]"):
        resolve_vad_class("silero")({})


def test_a_class_can_be_registered_eagerly():
    with patch.dict(REGISTRY, {"scripted": ScriptedVAD}):
        assert resolve_vad_class("scripted") is ScriptedVAD
