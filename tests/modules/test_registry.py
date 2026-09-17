"""Tests for the lazy ASR module registry (specs/asr-module-interface.md)."""

from __future__ import annotations

import subprocess
import sys
from unittest.mock import patch

import pytest

from asr_engine.modules import REGISTRY, LazyModule, resolve_module_class
from asr_engine.modules.fake import FakeASRModule


def test_unknown_type_lists_available_modules():
    with pytest.raises(
        ValueError, match=r"Unknown ASR type 'nope'\. Available: .*fake"
    ):
        resolve_module_class("nope")


def test_lazy_entry_imports_and_returns_class():
    assert resolve_module_class("fake") is FakeASRModule


def test_eager_entry_returned_as_registered():
    sentinel = object()
    with patch.dict(REGISTRY, {"eager": sentinel}):  # type: ignore[dict-item]
        assert resolve_module_class("eager") is sentinel


def test_missing_third_party_dependency_names_the_extra(tmp_path, monkeypatch):
    (tmp_path / "needs_missing_sdk.py").write_text(
        "import asr_engine_test_missing_sdk_xyz\n\nclass Module: ...\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    entry = LazyModule("needs_missing_sdk:Module", extra="acme")
    with patch.dict(REGISTRY, {"acme_v1": entry}):
        with pytest.raises(ImportError) as exc_info:
            resolve_module_class("acme_v1")

    message = str(exc_info.value)
    assert "ASR module 'acme_v1'" in message
    assert "pip install 'asr-engine[acme]'" in message
    assert "uv sync --extra acme" in message
    assert isinstance(exc_info.value.__cause__, ModuleNotFoundError)


def test_missing_own_module_propagates_unchanged():
    entry = LazyModule("asr_engine.modules.does_not_exist:Module", extra="acme")
    with patch.dict(REGISTRY, {"broken": entry}):
        with pytest.raises(ModuleNotFoundError, match="does_not_exist"):
            resolve_module_class("broken")


def test_importing_the_package_loads_no_provider_sdk():
    # Stub sounddevice as tests/conftest.py does, so PortAudio isn't needed.
    code = (
        "import sys; from unittest.mock import MagicMock; "
        "sys.modules['sounddevice'] = MagicMock(); "
        "import asr_engine, asr_engine.modules; "
        "assert 'deepgram' not in sys.modules, 'deepgram imported eagerly'"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
