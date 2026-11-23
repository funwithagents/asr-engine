from __future__ import annotations

import asyncio
import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Awaitable, Callable, ClassVar, Literal, TypeGuard

from asr_engine.audio import DEFAULT_AUDIO_FORMAT, AudioFormat, EndOfSpeech
from asr_engine.segment_timing import UtteranceTiming

log = logging.getLogger(__name__)


@dataclass
class SpeechUtterance:
    transcript: str
    is_final: bool
    confidence: float | None  # None if not provided by backend
    # Set by the engine on a final utterance (specs/segment-timing.md); None on
    # an interim. A module leaves it alone.
    timing: UtteranceTiming | None = None


# What a module's audio queue carries: PCM chunks, plus the VAD gate's
# end-of-speech marker when a VAD is configured (specs/vad.md).
AudioQueue = asyncio.Queue[bytes | EndOfSpeech]

# How a module relates to VAD gating (specs/vad.md "The module contract").
VadGating = Literal["unsupported", "supported", "required"]

UtteranceCallback = Callable[[SpeechUtterance], Awaitable[None]]
ConnectedCallback = Callable[[bool], None]


def resolve_api_key(config: dict, module_label: str) -> str:
    """Resolve an API key from a module config, so keys can stay out of config files.

    Precedence:
    1. ``config["api_key"]`` — a literal key.
    2. ``config["api_key_env"]`` — the name of an environment variable to read.

    Raises ``ValueError`` if neither is provided, or if the named environment
    variable is unset or empty.
    """
    api_key = config.get("api_key")
    if api_key:
        return api_key
    env_name = config.get("api_key_env")
    if env_name:
        value = os.environ.get(env_name)
        if not value:
            raise ValueError(
                f"{module_label} module: environment variable '{env_name}' "
                f"(api_key_env) is not set or is empty"
            )
        return value
    raise ValueError(
        f"{module_label} module requires 'api_key' or 'api_key_env' in config"
    )


_CAPABILITY_ATTRS = (
    "SUPPORTED_SAMPLE_RATES",
    "SUPPORTED_CHANNELS",
    "SUPPORTED_ENCODINGS",
    "DEFAULT_SAMPLE_RATE",
    "DEFAULT_CHANNELS",
    "DEFAULT_ENCODING",
)

# (DEFAULT_* attr, SUPPORTED_* attr, AudioFormat field) per format dimension.
_FORMAT_DIMENSIONS = (
    ("DEFAULT_SAMPLE_RATE", "SUPPORTED_SAMPLE_RATES", "sample_rate"),
    ("DEFAULT_CHANNELS", "SUPPORTED_CHANNELS", "channels"),
    ("DEFAULT_ENCODING", "SUPPORTED_ENCODINGS", "encoding"),
)


def _is_concrete(cls: type) -> bool:
    """True unless ``start``/``stop`` are still abstract on *cls*.

    ``__abstractmethods__`` isn't populated by ``ABCMeta`` until after
    ``__init_subclass__`` runs, so abstractness is read off the methods directly.
    """
    for name in ("start", "stop"):
        if getattr(getattr(cls, name, None), "__isabstractmethod__", False):
            return False
    return True


def parse_sample_rate_overrides(
    config: dict, module_cls: type[ASRModule], module_label: str
) -> tuple[frozenset[int] | None, int]:
    """Read the optional ``supported_sample_rates`` / ``default_sample_rate`` fields.

    For a protocol module whose servers differ in the rates they accept: the class
    attributes describe the default server, and these two config fields describe
    another one. Returns ``(supported, default)`` for the instance.

    - Neither field: the class values.
    - ``supported_sample_rates`` only: the default is the class default when the
      list holds it, else the list's single value; a longer list needs an explicit
      ``default_sample_rate``.
    - ``default_sample_rate`` only: that rate is the only one supported.

    Raises ``ValueError`` on a malformed field or a default outside the list.
    """

    def is_rate(value: object) -> TypeGuard[int]:
        return isinstance(value, int) and not isinstance(value, bool) and value > 0

    supported_raw = config.get("supported_sample_rates")
    default_raw = config.get("default_sample_rate")
    if supported_raw is None and default_raw is None:
        return module_cls.SUPPORTED_SAMPLE_RATES, module_cls.DEFAULT_SAMPLE_RATE

    default: int | None = None
    if default_raw is not None:
        if not is_rate(default_raw):
            raise ValueError(
                f"{module_label} module: 'default_sample_rate' must be a positive "
                f"integer"
            )
        default = default_raw
    if supported_raw is None:
        assert default is not None  # one of the two fields is set
        return frozenset({default}), default

    if (
        not isinstance(supported_raw, list)
        or not supported_raw
        or not all(is_rate(rate) for rate in supported_raw)
    ):
        raise ValueError(
            f"{module_label} module: 'supported_sample_rates' must be a non-empty "
            f"list of positive integers"
        )
    supported: frozenset[int] = frozenset(supported_raw)
    if default is None:
        if module_cls.DEFAULT_SAMPLE_RATE in supported:
            return supported, module_cls.DEFAULT_SAMPLE_RATE
        if len(supported) == 1:
            return supported, next(iter(supported))
        raise ValueError(
            f"{module_label} module: 'supported_sample_rates' {sorted(supported)} "
            f"does not hold the module's default rate "
            f"({module_cls.DEFAULT_SAMPLE_RATE}): set 'default_sample_rate' to one "
            f"of them"
        )
    if default not in supported:
        raise ValueError(
            f"{module_label} module: 'default_sample_rate' {default} is not in "
            f"'supported_sample_rates' {sorted(supported)}"
        )
    return supported, default


def resolve_module_audio_format(
    module: ASRModule,
    *,
    sample_rate: int | None = None,
    channels: int | None = None,
    encoding: str | None = None,
) -> AudioFormat:
    """The format *module* is fed: its defaults, unless the config asks otherwise.

    The arguments are the fields of ``engine.module.audio_format``. Each replaces
    the module's default when set, and must then be a value the module supports,
    else ``ValueError``. The audio source plays no part: the engine converts
    whatever it produces into this format.
    """
    requested = (
        ("sample_rate", sample_rate, module.supported_sample_rates),
        ("channels", channels, module.SUPPORTED_CHANNELS),
        ("encoding", encoding, module.SUPPORTED_ENCODINGS),
    )
    resolved: dict = {
        "sample_rate": module.default_sample_rate,
        "channels": module.DEFAULT_CHANNELS,
        "encoding": module.DEFAULT_ENCODING,
    }
    for field, value, supported in requested:
        if value is None:
            continue
        if supported is not None and value not in supported:
            raise ValueError(
                f"{type(module).__name__} does not support "
                f"engine.module.audio_format.{field}={value!r}. "
                f"Supported: {sorted(supported)}. Remove the field to use the "
                f"module's default ({resolved[field]!r})."
            )
        resolved[field] = value
    return AudioFormat(**resolved)


class ASRModule(ABC):
    # Audio-format capabilities — every concrete module MUST declare all six
    # (enforced by __init_subclass__ below). A ``SUPPORTED_*`` of ``None`` means
    # "any value" for that dimension. Declared as annotations only (no values),
    # so an omitted attribute is genuinely absent rather than silently defaulted.
    SUPPORTED_SAMPLE_RATES: ClassVar[frozenset[int] | None]
    SUPPORTED_CHANNELS: ClassVar[frozenset[int] | None]
    SUPPORTED_ENCODINGS: ClassVar[frozenset[str] | None]
    DEFAULT_SAMPLE_RATE: ClassVar[int]
    DEFAULT_CHANNELS: ClassVar[int]
    DEFAULT_ENCODING: ClassVar[str]

    # VAD gating (specs/vad.md). Unlike the capabilities above this has a default,
    # so a module written before VAD existed keeps working unchanged.
    # "unsupported": never sent a marker; VAD + this module is a config error.
    # "supported":   handles the marker; works with or without VAD.
    # "required":    produces no final without it; no VAD + this module is a config error.
    VAD_GATING: ClassVar[VadGating] = "unsupported"

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if not _is_concrete(cls):
            return  # abstract intermediate base; its concrete subclasses are checked
        missing = [a for a in _CAPABILITY_ATTRS if not hasattr(cls, a)]
        if missing:
            raise TypeError(
                f"{cls.__name__} must declare audio-format capabilities: "
                f"{', '.join(missing)}"
            )
        for default_attr, supported_attr, _ in _FORMAT_DIMENSIONS:
            supported = getattr(cls, supported_attr)
            default = getattr(cls, default_attr)
            if supported is not None and default not in supported:
                raise TypeError(
                    f"{cls.__name__}.{default_attr}={default!r} is not in "
                    f"{supported_attr}={sorted(supported)!r}"
                )

    def __init__(self, config: dict) -> None:
        self.config = config

    @property
    def supported_sample_rates(self) -> frozenset[int] | None:
        """The rates this instance accepts (``None`` = any).

        The class attribute, unless the module lets its config say otherwise (a
        protocol module pointed at another server: ``parse_sample_rate_overrides``).
        """
        return self.SUPPORTED_SAMPLE_RATES

    @property
    def default_sample_rate(self) -> int:
        """The rate this instance is fed unless ``engine.module.audio_format`` sets
        another."""
        return self.DEFAULT_SAMPLE_RATE

    @abstractmethod
    async def start(
        self,
        audio_queue: AudioQueue,
        on_utterance: UtteranceCallback,
        on_connected: ConnectedCallback | None = None,
        *,
        audio_format: AudioFormat = DEFAULT_AUDIO_FORMAT,
    ) -> None:
        """
        Start the ASR module.

        - audio_queue: PCM chunks. A module whose ``VAD_GATING`` is not
          ``"unsupported"`` may also receive ``END_OF_SPEECH`` markers on it (only
          when a VAD is configured) and calls ``finalize()`` for each.
        - audio_format: the format (rate/channels/encoding) of the chunks arriving
          on ``audio_queue`` — ``engine.module_audio_format``, which the engine
          resolved against this module; the module reports it to its backend.

        Runs indefinitely until stop() is called. Responsible for reconnecting
        to the backend on connection loss and draining audio_queue while reconnecting.
        """
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Stop the ASR module gracefully."""
        ...

    async def finalize(self) -> None:
        """The audio received so far ends an utterance: emit its final and be
        ready for the next one.

        Called by the module itself, from its own drain loop, when it reads an
        ``END_OF_SPEECH`` marker. May return before the final has been emitted,
        and must not block the drain loop for long. Default: no-op.
        """
