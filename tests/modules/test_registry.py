"""Tests for the ASR module registry (specs/asr-module-interface.md)."""

from __future__ import annotations

import logging
import subprocess
import sys
from unittest.mock import patch

import pytest

from asr_engine.modules import (
    REGISTRY,
    RENAMED_MODULE_TYPES,
    resolve_module_class,
)
from asr_engine.modules.fake import FakeASRModule


def test_unknown_type_lists_available_modules():
    with pytest.raises(
        ValueError, match=r"Unknown ASR type 'nope'\. Available: .*fake"
    ):
        resolve_module_class("nope")


def test_unknown_type_lists_only_the_hyphenated_names():
    with pytest.raises(ValueError) as exc_info:
        resolve_module_class("nope")
    available = str(exc_info.value).split("Available: ", 1)[1].split(", ")
    assert "deepgram-v1" in available and "openai-realtime-compatible" in available
    assert not [name for name in available if "_" in name]


def test_every_registry_key_is_written_with_hyphens():
    assert [key for key in REGISTRY if "_" in key] == []


@pytest.mark.parametrize(("old", "new"), sorted(RENAMED_MODULE_TYPES.items()))
def test_an_old_name_resolves_to_its_new_module_with_a_warning(old, new, caplog):
    with caplog.at_level(logging.WARNING, logger="asr_engine.modules"):
        old_cls = resolve_module_class(old)
    assert old_cls is resolve_module_class(new)
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings == [f"ASR module type '{old}' is deprecated, use '{new}' instead"]


def test_a_new_name_logs_no_warning(caplog):
    with caplog.at_level(logging.WARNING, logger="asr_engine.modules"):
        resolve_module_class("deepgram-v1")
    assert caplog.records == []


@pytest.mark.parametrize(
    ("module_type", "sdk"),
    [
        ("openai-realtime-compatible", "websockets"),
        ("openai-transcriptions-compatible", "httpx"),
    ],
)
def test_the_protocol_modules_install_hint_names_the_shared_extra(
    module_type, sdk, monkeypatch
):
    # Simulate the extra not being installed: the SDK import fails when the
    # module is built.
    monkeypatch.setitem(sys.modules, sdk, None)  # None → ModuleNotFoundError
    cls = resolve_module_class(module_type)

    with pytest.raises(ImportError) as exc_info:
        cls(
            {"url": "ws://localhost:8000/v1/realtime"}
            if sdk == "websockets"
            else {"url": "http://localhost:8000/v1/audio/transcriptions"}
        )

    message = str(exc_info.value)
    assert f"{cls.title} module" in message
    assert "pip install 'asr-engine[openai-compatible]'" in message
    assert "uv sync --extra openai-compatible" in message


def test_a_registered_type_resolves_to_its_class():
    assert resolve_module_class("fake") is FakeASRModule


def test_a_class_registered_at_runtime_is_returned():
    with patch.dict(REGISTRY, {"scripted": FakeASRModule}):
        assert resolve_module_class("scripted") is FakeASRModule


def test_importing_the_package_loads_no_provider_sdk():
    # Stub sounddevice as tests/conftest.py does, so PortAudio isn't needed.
    code = (
        "import sys; from unittest.mock import MagicMock; "
        "sys.modules['sounddevice'] = MagicMock(); "
        "import asr_engine, asr_engine.modules, asr_engine.vad, asr_engine.setup; "
        "eager = [n for n in ('deepgram', 'websockets', 'httpx', 'onnxruntime', "
        "'mlx_audio', 'moshi', 'moshi_mlx') if n in sys.modules]; "
        "assert not eager, f'imported eagerly: {eager}'"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def test_the_e2e_tier_knows_every_vad_required_module() -> None:
    # tests-e2e/helpers.py names them so the ungated conformance case skips them
    # without importing module classes at collection time.
    from helpers import VAD_REQUIRED_MODULES

    from asr_engine.modules import REGISTRY, resolve_module_class

    required = {
        key for key in REGISTRY if resolve_module_class(key).VAD_GATING == "required"
    }
    assert required == VAD_REQUIRED_MODULES
