"""Unit tests for asr_mcp.modules.base and load_module — Plan 04."""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest

from asr_mcp.modules import REGISTRY, load_module
from asr_mcp.modules.base import ASRModule, ASRResult


# ---------------------------------------------------------------------------
# ASRResult dataclass
# ---------------------------------------------------------------------------


def test_asr_result_fields() -> None:
    r = ASRResult(transcript="hello", is_final=True, confidence=0.95)
    assert r.transcript == "hello"
    assert r.is_final is True
    assert r.confidence == 0.95


def test_asr_result_confidence_none() -> None:
    r = ASRResult(transcript="hi", is_final=False, confidence=None)
    assert r.confidence is None


def test_asr_result_equality() -> None:
    a = ASRResult(transcript="x", is_final=True, confidence=0.5)
    b = ASRResult(transcript="x", is_final=True, confidence=0.5)
    assert a == b


def test_asr_result_inequality() -> None:
    a = ASRResult(transcript="x", is_final=True, confidence=0.5)
    b = ASRResult(transcript="y", is_final=True, confidence=0.5)
    assert a != b


# ---------------------------------------------------------------------------
# ASRModule ABC
# ---------------------------------------------------------------------------


def test_asr_module_cannot_be_instantiated() -> None:
    with pytest.raises(TypeError):
        ASRModule(config={})  # type: ignore[abstract]


def test_asr_module_concrete_subclass_stores_config() -> None:
    class FakeModule(ASRModule):
        async def start(self, audio_queue, on_result):
            pass

        async def stop(self):
            pass

    cfg = {"api_key": "secret", "language": "en"}
    m = FakeModule(config=cfg)
    assert m.config == cfg


@pytest.mark.asyncio
async def test_asr_module_start_stop_called() -> None:
    class TrackingModule(ASRModule):
        def __init__(self, config):
            super().__init__(config)
            self.started = False
            self.stopped = False

        async def start(self, audio_queue, on_result):
            self.started = True

        async def stop(self):
            self.stopped = True

    m = TrackingModule(config={})
    queue: asyncio.Queue[bytes] = asyncio.Queue()
    cb = AsyncMock()
    await m.start(queue, cb)
    await m.stop()
    assert m.started
    assert m.stopped


# ---------------------------------------------------------------------------
# load_module
# ---------------------------------------------------------------------------


def _make_fake_class() -> type[ASRModule]:
    class FakeModule(ASRModule):
        async def start(self, audio_queue, on_result):
            pass

        async def stop(self):
            pass

    return FakeModule


def test_load_module_unknown_type_raises() -> None:
    with pytest.raises(ValueError, match="unknown_backend"):
        load_module({"type": "unknown_backend"})


def test_load_module_missing_type_raises() -> None:
    with pytest.raises(ValueError):
        load_module({})


def test_load_module_instantiates_registered_class(monkeypatch) -> None:
    FakeModule = _make_fake_class()
    monkeypatch.setitem(REGISTRY, "fake", FakeModule)

    module = load_module({"type": "fake"})
    assert isinstance(module, FakeModule)


def test_load_module_strips_type_from_config(monkeypatch) -> None:
    FakeModule = _make_fake_class()
    monkeypatch.setitem(REGISTRY, "fake", FakeModule)

    module = load_module({"type": "fake", "api_key": "abc", "lang": "fr"})
    assert "type" not in module.config
    assert module.config == {"api_key": "abc", "lang": "fr"}


def test_load_module_empty_extra_config(monkeypatch) -> None:
    FakeModule = _make_fake_class()
    monkeypatch.setitem(REGISTRY, "fake", FakeModule)

    module = load_module({"type": "fake"})
    assert module.config == {}


def test_load_module_error_message_lists_available(monkeypatch) -> None:
    FakeModule = _make_fake_class()
    monkeypatch.setitem(REGISTRY, "real_module", FakeModule)

    with pytest.raises(ValueError, match="real_module"):
        load_module({"type": "nope"})
