"""Unit tests for asr_mcp.modules.base and load_module — Plan 04."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from asr_mcp.modules import REGISTRY, load_module
from asr_mcp.modules.base import ASRModule, SpeechUtterance, resolve_api_key

# ---------------------------------------------------------------------------
# SpeechUtterance dataclass
# ---------------------------------------------------------------------------


def test_utterance_fields() -> None:
    r = SpeechUtterance(transcript="hello", is_final=True, confidence=0.95)
    assert r.transcript == "hello"
    assert r.is_final is True
    assert r.confidence == 0.95


def test_utterance_confidence_none() -> None:
    r = SpeechUtterance(transcript="hi", is_final=False, confidence=None)
    assert r.confidence is None


def test_utterance_equality() -> None:
    a = SpeechUtterance(transcript="x", is_final=True, confidence=0.5)
    b = SpeechUtterance(transcript="x", is_final=True, confidence=0.5)
    assert a == b


def test_utterance_inequality() -> None:
    a = SpeechUtterance(transcript="x", is_final=True, confidence=0.5)
    b = SpeechUtterance(transcript="y", is_final=True, confidence=0.5)
    assert a != b


# ---------------------------------------------------------------------------
# resolve_api_key
# ---------------------------------------------------------------------------


def test_resolve_api_key_literal_wins(monkeypatch) -> None:
    monkeypatch.setenv("SOME_ENV", "from-env")
    key = resolve_api_key({"api_key": "literal", "api_key_env": "SOME_ENV"}, "mod")
    assert key == "literal"


def test_resolve_api_key_from_env(monkeypatch) -> None:
    monkeypatch.setenv("MY_KEY_VAR", "secret-value")
    key = resolve_api_key({"api_key_env": "MY_KEY_VAR"}, "mod")
    assert key == "secret-value"


def test_resolve_api_key_env_unset_raises(monkeypatch) -> None:
    monkeypatch.delenv("MISSING_KEY_VAR", raising=False)
    with pytest.raises(ValueError, match="MISSING_KEY_VAR"):
        resolve_api_key({"api_key_env": "MISSING_KEY_VAR"}, "mod")


def test_resolve_api_key_env_empty_raises(monkeypatch) -> None:
    monkeypatch.setenv("EMPTY_KEY_VAR", "")
    with pytest.raises(ValueError, match="EMPTY_KEY_VAR"):
        resolve_api_key({"api_key_env": "EMPTY_KEY_VAR"}, "mod")


def test_resolve_api_key_neither_raises() -> None:
    with pytest.raises(ValueError, match="api_key.*or.*api_key_env"):
        resolve_api_key({}, "mod")


# ---------------------------------------------------------------------------
# ASRModule ABC
# ---------------------------------------------------------------------------


def test_asr_module_cannot_be_instantiated() -> None:
    with pytest.raises(TypeError):
        ASRModule(config={})  # type: ignore[abstract]


def test_asr_module_concrete_subclass_stores_config() -> None:
    class FakeModule(ASRModule):
        async def start(self, audio_queue, on_utterance, on_connected=None):
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

        async def start(self, audio_queue, on_utterance, on_connected=None):
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
        async def start(self, audio_queue, on_utterance, on_connected=None):
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
