"""Unit tests for asr_engine.modules.base and load_module — Plan 04."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from asr_engine.audio import AudioFormat
from asr_engine.modules import REGISTRY, load_module
from asr_engine.modules.base import (
    ASRModule,
    AudioQueue,
    SpeechUtterance,
    parse_sample_rate_overrides,
    resolve_api_key,
    resolve_module_audio_format,
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
    queue: AudioQueue = asyncio.Queue()
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
# resolve_module_audio_format
# ---------------------------------------------------------------------------


class _PickyModule(ASRModule):
    """Supports 16 k and 48 k / mono / linear16 and mulaw — for format-resolution tests."""

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


def _resolve(module: ASRModule | None = None, **source) -> AudioFormat:
    source = {"sample_rate": None, "channels": None, "encoding": "linear16", **source}
    return resolve_module_audio_format(module or _PickyModule(config={}), **source)


def test_a_source_format_the_module_supports_is_kept_unchanged() -> None:
    resolved = _resolve(sample_rate=48000, channels=1, encoding="mulaw")
    assert resolved == AudioFormat(sample_rate=48000, channels=1, encoding="mulaw")


def test_each_unsupported_dimension_resolves_to_the_module_default() -> None:
    # 44.1 kHz stereo is not something the module takes; mulaw is.
    resolved = _resolve(sample_rate=44100, channels=2, encoding="mulaw")
    assert resolved == AudioFormat(sample_rate=16000, channels=1, encoding="mulaw")


def test_a_source_with_no_rate_of_its_own_gets_the_module_defaults() -> None:
    assert _resolve() == AudioFormat(sample_rate=16000, channels=1)


def test_a_requested_rate_wins_over_the_source_rate() -> None:
    # The source's 16 kHz is supported, yet the module is asked to be fed 48 kHz.
    resolved = _resolve(sample_rate=16000, requested_sample_rate=48000)
    assert resolved.sample_rate == 48000


def test_a_requested_rate_the_module_does_not_support_is_an_error() -> None:
    with pytest.raises(ValueError, match=r"engine.module.sample_rate=44100.*16000"):
        _resolve(sample_rate=16000, requested_sample_rate=44100)


class _AnyRateModule(_PickyModule):
    SUPPORTED_SAMPLE_RATES = None


def test_a_module_taking_any_rate_keeps_the_source_or_the_requested_rate() -> None:
    module = _AnyRateModule(config={})
    assert _resolve(module, sample_rate=44100).sample_rate == 44100
    assert _resolve(module, requested_sample_rate=11025).sample_rate == 11025


# ---------------------------------------------------------------------------
# parse_sample_rate_overrides
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "config, expected",
    [
        ({}, (frozenset({16000, 48000}), 16000)),
        # A list holding the class default keeps it as the default.
        ({"supported_sample_rates": [8000, 16000]}, (frozenset({8000, 16000}), 16000)),
        # A single other rate is its own default.
        ({"supported_sample_rates": [24000]}, (frozenset({24000}), 24000)),
        # A default alone is the only rate supported.
        ({"default_sample_rate": 8000}, (frozenset({8000}), 8000)),
        (
            {"supported_sample_rates": [8000, 24000], "default_sample_rate": 24000},
            (frozenset({8000, 24000}), 24000),
        ),
    ],
)
def test_sample_rate_overrides(config, expected) -> None:
    assert parse_sample_rate_overrides(config, _PickyModule, "picky") == expected


@pytest.mark.parametrize(
    "config, message",
    [
        ({"supported_sample_rates": []}, "non-empty list"),
        ({"supported_sample_rates": 24000}, "non-empty list"),
        ({"supported_sample_rates": [24000, "16000"]}, "non-empty list"),
        ({"default_sample_rate": 0}, "'default_sample_rate' must be a positive"),
        ({"default_sample_rate": True}, "'default_sample_rate' must be a positive"),
        # Two rates, neither the class default: which one to feed is ambiguous.
        ({"supported_sample_rates": [8000, 24000]}, "set 'default_sample_rate'"),
        (
            {"supported_sample_rates": [8000], "default_sample_rate": 24000},
            "is not in 'supported_sample_rates'",
        ),
    ],
)
def test_invalid_sample_rate_overrides_are_rejected(config, message) -> None:
    with pytest.raises(ValueError, match=f"picky module: .*{message}"):
        parse_sample_rate_overrides(config, _PickyModule, "picky")
