"""Unit tests for asr_engine.modules.base and load_module — Plan 04."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from asr_engine.audio import AudioFormat
from asr_engine.modules import REGISTRY, load_module
from asr_engine.modules.base import (
    ASRModule,
    SpeechUtterance,
    reconcile_audio_format,
    resolve_api_key,
)

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
        SUPPORTED_SAMPLE_RATES = None
        SUPPORTED_CHANNELS = None
        SUPPORTED_ENCODINGS = None
        DEFAULT_SAMPLE_RATE = 16000
        DEFAULT_CHANNELS = 1
        DEFAULT_ENCODING = "linear16"

        async def start(
            self, audio_queue, on_utterance, on_connected=None, *, audio_format=None
        ):
            pass

        async def stop(self):
            pass

    cfg = {"api_key": "secret", "language": "en"}
    m = FakeModule(config=cfg)
    assert m.config == cfg


@pytest.mark.asyncio
async def test_asr_module_start_stop_called() -> None:
    class TrackingModule(ASRModule):
        SUPPORTED_SAMPLE_RATES = None
        SUPPORTED_CHANNELS = None
        SUPPORTED_ENCODINGS = None
        DEFAULT_SAMPLE_RATE = 16000
        DEFAULT_CHANNELS = 1
        DEFAULT_ENCODING = "linear16"

        def __init__(self, config):
            super().__init__(config)
            self.started = False
            self.stopped = False

        async def start(
            self, audio_queue, on_utterance, on_connected=None, *, audio_format=None
        ):
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
        SUPPORTED_SAMPLE_RATES = None
        SUPPORTED_CHANNELS = None
        SUPPORTED_ENCODINGS = None
        DEFAULT_SAMPLE_RATE = 16000
        DEFAULT_CHANNELS = 1
        DEFAULT_ENCODING = "linear16"

        async def start(
            self, audio_queue, on_utterance, on_connected=None, *, audio_format=None
        ):
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


# ---------------------------------------------------------------------------
# Audio-format capability declaration (__init_subclass__ enforcement)
# ---------------------------------------------------------------------------


def test_concrete_module_missing_capability_attrs_raises() -> None:
    """A concrete module that omits a capability attribute fails to define."""
    with pytest.raises(TypeError, match="SUPPORTED_SAMPLE_RATES"):

        class NoCaps(ASRModule):
            async def start(
                self, audio_queue, on_utterance, on_connected=None, *, audio_format=None
            ):
                pass

            async def stop(self):
                pass


def test_default_outside_supported_set_raises() -> None:
    """A DEFAULT_* not present in its SUPPORTED_* set fails to define."""
    with pytest.raises(TypeError, match="DEFAULT_SAMPLE_RATE"):

        class BadDefault(ASRModule):
            SUPPORTED_SAMPLE_RATES = frozenset({16000})
            SUPPORTED_CHANNELS = None
            SUPPORTED_ENCODINGS = None
            DEFAULT_SAMPLE_RATE = 48000  # not in SUPPORTED_SAMPLE_RATES
            DEFAULT_CHANNELS = 1
            DEFAULT_ENCODING = "linear16"

            async def start(
                self, audio_queue, on_utterance, on_connected=None, *, audio_format=None
            ):
                pass

            async def stop(self):
                pass


def test_abstract_intermediate_base_is_exempt() -> None:
    """An intermediate subclass that stays abstract need not declare capabilities."""

    class AbstractMiddle(ASRModule):  # no start/stop override → still abstract
        pass

    # Concrete subclass must still declare (and can); no error here for the middle.
    class Concrete(AbstractMiddle):
        SUPPORTED_SAMPLE_RATES = None
        SUPPORTED_CHANNELS = None
        SUPPORTED_ENCODINGS = None
        DEFAULT_SAMPLE_RATE = 16000
        DEFAULT_CHANNELS = 1
        DEFAULT_ENCODING = "linear16"

        async def start(
            self, audio_queue, on_utterance, on_connected=None, *, audio_format=None
        ):
            pass

        async def stop(self):
            pass

    assert Concrete(config={}).config == {}


# ---------------------------------------------------------------------------
# reconcile_audio_format
# ---------------------------------------------------------------------------


class _PickyModule(ASRModule):
    """Supports only 16 k / mono / linear16 — for reconcile tests."""

    SUPPORTED_SAMPLE_RATES = frozenset({16000, 48000})
    SUPPORTED_CHANNELS = frozenset({1})
    SUPPORTED_ENCODINGS = frozenset({"linear16", "mulaw"})
    DEFAULT_SAMPLE_RATE = 16000
    DEFAULT_CHANNELS = 1
    DEFAULT_ENCODING = "linear16"

    async def start(
        self, audio_queue, on_utterance, on_connected=None, *, audio_format=None
    ):
        pass

    async def stop(self):
        pass


def test_reconcile_keeps_supported_format_unchanged() -> None:
    desired = AudioFormat(sample_rate=48000, channels=1, encoding="mulaw")
    assert reconcile_audio_format(desired, _PickyModule) == desired


def test_reconcile_errors_on_unsupported_by_default() -> None:
    desired = AudioFormat(sample_rate=44100)  # not in {16000, 48000}
    with pytest.raises(ValueError, match="sample_rate=44100"):
        reconcile_audio_format(desired, _PickyModule)


def test_reconcile_fallback_uses_module_default_and_keeps_supported_dims() -> None:
    desired = AudioFormat(sample_rate=44100, channels=1, encoding="mulaw")
    resolved = reconcile_audio_format(desired, _PickyModule, on_unsupported="fallback")
    # Unsupported rate falls back to the module default; supported dims are kept.
    assert resolved == AudioFormat(sample_rate=16000, channels=1, encoding="mulaw")
