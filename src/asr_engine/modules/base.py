from __future__ import annotations

import asyncio
import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Awaitable, Callable, ClassVar

from asr_engine.audio import DEFAULT_AUDIO_FORMAT, AudioFormat

log = logging.getLogger(__name__)


@dataclass
class SpeechUtterance:
    transcript: str
    is_final: bool
    confidence: float | None  # None if not provided by backend


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


def _reconcile_dimension(
    value: object,
    supported: frozenset | None,
    default: object,
    *,
    field: str,
    module_name: str,
    on_unsupported: str,
) -> object:
    """Resolve one format dimension against a module's declared support.

    Returns *value* when supported (or support is unconstrained). Otherwise
    raises ``ValueError`` (``on_unsupported="error"``) or logs and falls back to
    *default* (``on_unsupported="fallback"``).
    """
    if supported is None or value in supported:
        return value
    if on_unsupported == "error":
        raise ValueError(
            f"{module_name} does not support {field}={value!r}. "
            f"Supported: {sorted(supported)}. Set "
            f"engine.audio.on_unsupported_format='fallback' to use the module "
            f"default ({default!r}) instead."
        )
    log.warning(
        "%s does not support %s=%r; falling back to module default %r",
        module_name,
        field,
        value,
        default,
    )
    return default


def reconcile_audio_format(
    desired: AudioFormat,
    module_cls: type[ASRModule],
    *,
    on_unsupported: str = "error",
) -> AudioFormat:
    """Resolve *desired* against *module_cls*'s declared capabilities.

    Each dimension (rate/channels/encoding) is kept if the module supports it;
    an unsupported value either raises or falls back to the module's default per
    *on_unsupported* (``"error"`` | ``"fallback"``).
    """
    module_name = getattr(module_cls, "__name__", str(module_cls))
    resolved = {}
    for default_attr, supported_attr, field in _FORMAT_DIMENSIONS:
        resolved[field] = _reconcile_dimension(
            getattr(desired, field),
            getattr(module_cls, supported_attr),
            getattr(module_cls, default_attr),
            field=field,
            module_name=module_name,
            on_unsupported=on_unsupported,
        )
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

    @abstractmethod
    async def start(
        self,
        audio_queue: asyncio.Queue[bytes],
        on_utterance: UtteranceCallback,
        on_connected: ConnectedCallback | None = None,
        *,
        audio_format: AudioFormat = DEFAULT_AUDIO_FORMAT,
    ) -> None:
        """
        Start the ASR module.

        - audio_format: the reconciled format (rate/channels/encoding) of the
          chunks arriving on ``audio_queue``; the module reports it to its backend.

        Runs indefinitely until stop() is called. Responsible for reconnecting
        to the backend on connection loss and draining audio_queue while reconnecting.
        """
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Stop the ASR module gracefully."""
        ...
