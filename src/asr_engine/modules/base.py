from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, ClassVar, Literal, TypeGuard

from asr_engine.audio import DEFAULT_AUDIO_FORMAT, AudioFormat, EndOfSpeech
from asr_engine.errors import ConfigError, MissingSecretError
from asr_engine.modules.fields import Field, field_problems, fields_schema, read_fields
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


# --- The setup side of a module (specs/setup.md "On a module") ----------------

ModuleKind = Literal["hosted", "local", "fixture"]
ProgressCallback = Callable[[dict[str, Any]], None]


class Declared:
    """What an application needs to set a module up before constructing it, as
    class-level data and classmethods: its name for a person, its kind, the extra
    carrying its dependencies, its config fields, and the setup contract's methods
    over its own config block, each with a default a module overrides only when it
    needs to. ``ASRModule`` and ``VADModule`` both are.

    None of it imports the provider's library, except ``imports`` (and what runs
    once it succeeded: ``check_files``, ``prepare``, ``probe``).
    """

    # Declared by every module (no default): "hosted" (a provider's API, a key
    # and a network), "local" (a model run on this machine), "fixture" (needs
    # nothing; for tests and for trying the pipeline).
    kind: ClassVar[ModuleKind]
    title: ClassVar[str]  # "Deepgram Nova", "Kyutai"
    extra: ClassVar[str | None]  # "deepgram"; None when it needs none
    fields: ClassVar[tuple[Field, ...]]
    # The fields a `Field` cannot state (a list of strings, a boolean or an
    # object): their JSON Schema, for the form. `config_rules` validates them.
    schema_extras: ClassVar[Mapping[str, Mapping[str, Any]]] = {}
    # What the module is called in its messages: "Silero VAD 'model_path' …".
    noun: ClassVar[str] = "module"

    @classmethod
    def label(cls) -> str:
        """The prefix of the module's messages: "Deepgram Nova module"."""
        return f"{cls.title} {cls.noun}"

    # --- The setup contract, over the module's own block.

    @classmethod
    def required_extras(cls, config: dict) -> list[str]:
        """The extras this module needs, as requirement strings: its `extra`,
        whatever the rest of the config says. Never raises."""
        return [f"asr-engine[{cls.extra}]"] if cls.extra is not None else []

    @classmethod
    def check_config(cls, config: dict) -> list[tuple[str, str]]:
        """Everything that makes the module refuse this config block, as
        `(field name, message)`; the name is "" for a `config_rules` rule. Empty
        when the constructor would accept it. Imports nothing."""
        found = field_problems(config, cls.fields_for(config), label=cls.label())
        try:
            cls._refuse_key(config)
        except ConfigError as exc:
            found.insert(0, ("api_key", str(exc)))
        if not found:
            try:
                cls.validated(config)
            except ConfigError as exc:
                found.append(("", str(exc)))
        return found

    @classmethod
    def check_setup(cls, config: dict) -> list[dict[str, Any]]:
        """What this machine is missing to run this config block, each item
        `{"id", "title", "status", "detail", "preparable"}`: the module's extra
        (its imports tried), the keys its config names, then its files once the
        extra imports (`check_files`). Local: no network. Never raises."""
        found: list[dict[str, Any]] = []
        imported = True
        for requirement in cls.required_extras(config):
            try:
                cls.imports(config)
            except Exception as exc:  # noqa: BLE001 - reported, never raised
                imported = False
                status, detail = "missing", str(exc) or type(exc).__name__
            else:
                status, detail = "ready", "Installed."
            found.append(
                {
                    "id": f"extra:{requirement}",
                    "title": f"{cls.title} extra",
                    "status": status,
                    "detail": detail,
                    "preparable": False,
                }
            )
        found += cls._key_items(config)
        if imported:
            try:
                found += cls.check_files(config)
            except Exception as exc:  # noqa: BLE001 - reported, never raised
                found.append(
                    {
                        "id": f"files:{cls.title}",
                        "title": f"{cls.title} files",
                        "status": "unknown",
                        "detail": str(exc) or type(exc).__name__,
                        "preparable": False,
                    }
                )
        return found

    @classmethod
    def prepare(cls, config: dict, on_progress: ProgressCallback | None = None) -> None:
        """Make every preparable item of this config ready (download the model).
        Needs the extra installed. Blocking and idempotent; raises on failure.
        Nothing to do by default."""

    @classmethod
    def options(cls, name: str, config: dict) -> list[dict[str, Any]]:
        """The choices of a named list the module offers. None by default."""
        return []

    @classmethod
    def probe(cls, config: dict, secrets: Mapping[str, str] | None = None) -> str:
        """Try the config for real, briefly and for free, and say what was done;
        raise on failure. A hosted module makes one authenticated listing call, a
        local one loads its model and releases it. Nothing to run by default."""
        return f"{cls.title}: nothing to try; it needs no key and no model."

    # --- What the contract methods are built on.

    @classmethod
    def imports(cls, config: dict) -> None:
        """Import what the module's extra provides, raising `ImportError` with the
        install hint when it is missing. The constructor calls it before its first
        use of the provider, and `check_setup` tries it. Nothing by default."""

    @classmethod
    def install_hint(cls, extra: str, *, note: str = "") -> str:
        """The message of a missing extra, the same for every module."""
        return (
            f"{cls.label()} requires the optional dependency '{extra}' which is "
            f"not installed. Install it with: pip install 'asr-engine[{extra}]' "
            f"(from source: uv sync --extra {extra})" + note
        )

    @classmethod
    def check_files(cls, config: dict) -> list[dict[str, Any]]:
        """The files this config needs on the machine (model weights), as
        `check_setup` items. Called only once `imports` succeeded. Local: no
        network, no model load. None by default."""
        return []

    @classmethod
    def validated(cls, config: dict) -> dict[str, Any]:
        """The module's fields read from its config block (a default where one is
        absent), after `config_rules`. Raises `ConfigError` on the first invalid
        field. The first thing a constructor does, before any provider import:
        what `check_config` accepts is what the constructor accepts."""
        cls._refuse_key(config)
        values = read_fields(
            config, cls.fields_for(config), label=cls.label(), error=ConfigError
        )
        cls.config_rules(values, config)
        return values

    @classmethod
    def fields_for(cls, config: dict) -> tuple[Field, ...]:
        """The fields as this config reads them: `fields`, unless a rule depends
        on another field's value."""
        return cls.fields

    @classmethod
    def config_rules(cls, values: dict[str, Any], config: dict) -> None:
        """The module's rules across fields, or on a field a `Field` cannot state
        (`schema_extras`). Raises `ConfigError`. Looks at the config only."""

    @classmethod
    def schema(cls) -> dict[str, Any]:
        """The module's fields as the `properties` and `required` of a JSON
        Schema object: its `fields`, then its `schema_extras`."""
        schema = fields_schema(cls.fields)
        for name, node in cls.schema_extras.items():
            schema["properties"][name] = dict(node)
        return schema

    # --- Keys (specs/setup.md "Keys").

    @classmethod
    def takes_secrets(cls) -> bool:
        """Whether the module reads a key (an `api_key_env` field): only such a
        module is given the secrets of `ASREngine(config, secrets=...)`."""
        return any(f.secret == "env" for f in getattr(cls, "fields", ()))

    @classmethod
    def key_variable(cls, config: dict) -> str | None:
        """The environment variable the config names for the key (`api_key_env`),
        else the field's default; None when there is none."""
        field = next((f for f in getattr(cls, "fields", ()) if f.secret == "env"), None)
        if field is None:
            return None
        name = config.get(field.name)
        if name is None:
            name = field.default
        return name if isinstance(name, str) and name else None

    @classmethod
    def resolve_api_key(
        cls, config: dict, secrets: Mapping[str, str] | None = None
    ) -> str:
        """The module's API key, under the variable its `api_key_env` field names
        (the field's default when absent): from *secrets* (variable name → key,
        given in code) first, then from the environment. The config never holds
        the key: one written there is refused. No key under that name is a
        `MissingSecretError` carrying the variable."""
        cls._refuse_key(config)
        name = cls.key_variable(config)
        if name is None:
            raise ConfigError(f"{cls.label()} 'api_key_env' must be a non-empty string")
        api_key = (secrets or {}).get(name) or os.environ.get(name)
        if not api_key:
            raise MissingSecretError(name, label=cls.label())
        return api_key

    @classmethod
    def _refuse_key(cls, config: dict) -> None:
        if cls.takes_secrets():
            refuse_api_key(config, label=cls.label())

    @classmethod
    def _key_items(cls, config: dict) -> list[dict[str, Any]]:
        name = cls.key_variable(config)
        if name is None:
            return []
        present = bool(os.environ.get(name))
        return [
            {
                "id": f"key:{name}",
                "title": name,
                "status": "ready" if present else "missing",
                "detail": (
                    "Set."
                    if present
                    else f"Set the environment variable {name} (named by "
                    "'api_key_env')."
                ),
                "preparable": False,
            }
        ]


def key_env_field(provider: str, variable: str | None) -> Field:
    """The `api_key_env` field of a module that authenticates: the environment
    variable holding *provider*'s API key, *variable* by default. A config never
    holds the key itself (no `api_key` field). With no default (*variable* None, a
    compatible server that often needs no key), the field may stay absent and no
    key is sent."""
    message = f"{provider} module 'api_key_env' must be a non-empty string"
    if variable is None:
        return Field(
            "api_key_env",
            "string",
            f"The environment variable holding the {provider} API key. Absent, no "
            "key is sent, for a server that needs none.",
            secret="env",
            message=message,
        )
    return Field(
        "api_key_env",
        "string",
        f"The environment variable holding the {provider} API key.",
        default=variable,
        secret="env",
        message=message,
    )


def refuse_api_key(config: dict, *, label: str) -> None:
    """Refuse a key written in the config: `api_key` was accepted until the engine
    took keys only from environment variables."""
    if "api_key" in config:
        raise ConfigError(
            f"{label} does not take 'api_key': keep the key in an environment "
            "variable and name it in 'api_key_env'"
        )


# --- Probing a provider: one listing call (specs/setup.md "probe") -------------

_PROBE_TIMEOUT_S = 10.0


def get_json(url: str, headers: Mapping[str, str], *, what: str) -> Any:
    """GET *url* and return its JSON body; a refusal, an error status or no answer
    raises `RuntimeError` saying so in words. *what* names the service."""
    request = urllib.request.Request(url, headers=dict(headers))
    try:
        with urllib.request.urlopen(request, timeout=_PROBE_TIMEOUT_S) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise RuntimeError(f"{what} refused the key (HTTP {exc.code}).") from exc
        raise RuntimeError(f"{what} answered HTTP {exc.code} at {url}.") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise RuntimeError(f"{what} did not answer at {url}: {reason}.") from exc
    except ValueError as exc:
        raise RuntimeError(f"{what} answered with no JSON at {url}.") from exc


def listed_model_ids(body: Any) -> list[str]:
    """The model ids of an OpenAI-style listing (`{"data": [{"id": …}]}`)."""
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, list):
        return []
    return [
        m["id"] for m in data if isinstance(m, dict) and isinstance(m.get("id"), str)
    ]


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

    Raises ``ConfigError`` on a malformed field or a default outside the list.
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
            raise ConfigError(
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
        raise ConfigError(
            f"{module_label} module: 'supported_sample_rates' must be a non-empty "
            f"list of positive integers"
        )
    supported: frozenset[int] = frozenset(supported_raw)
    if default is None:
        if module_cls.DEFAULT_SAMPLE_RATE in supported:
            return supported, module_cls.DEFAULT_SAMPLE_RATE
        if len(supported) == 1:
            return supported, next(iter(supported))
        raise ConfigError(
            f"{module_label} module: 'supported_sample_rates' {sorted(supported)} "
            f"does not hold the module's default rate "
            f"({module_cls.DEFAULT_SAMPLE_RATE}): set 'default_sample_rate' to one "
            f"of them"
        )
    if default not in supported:
        raise ConfigError(
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
    else ``ConfigError``. The audio source plays no part: the engine converts
    whatever it produces into this format.
    """
    return format_for(
        module,
        (module.supported_sample_rates, module.default_sample_rate),
        sample_rate=sample_rate,
        channels=channels,
        encoding=encoding,
    )


def format_for(
    module: ASRModule | type[ASRModule],
    rates: tuple[frozenset[int] | None, int],
    *,
    sample_rate: int | None = None,
    channels: int | None = None,
    encoding: str | None = None,
) -> AudioFormat:
    """``resolve_module_audio_format`` from a module or its class (its channel and
    encoding capabilities) and the rates its config gives (``ASRModule.sample_rates``):
    the setup checks use it on the class, without building the module."""
    module_cls = module if isinstance(module, type) else type(module)
    supported_rates, default_rate = rates
    requested = (
        ("sample_rate", sample_rate, supported_rates),
        ("channels", channels, module.SUPPORTED_CHANNELS),
        ("encoding", encoding, module.SUPPORTED_ENCODINGS),
    )
    resolved: dict = {
        "sample_rate": default_rate,
        "channels": module.DEFAULT_CHANNELS,
        "encoding": module.DEFAULT_ENCODING,
    }
    for field, value, supported in requested:
        if value is None:
            continue
        if supported is not None and value not in supported:
            raise ConfigError(
                f"{module_cls.__name__} does not support "
                f"engine.module.audio_format.{field}={value!r}. "
                f"Supported: {sorted(supported)}. Remove the field to use the "
                f"module's default ({resolved[field]!r})."
            )
        resolved[field] = value
    return AudioFormat(**resolved)


class ASRModule(Declared, ABC):
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

    @classmethod
    def sample_rates(cls, config: dict) -> tuple[frozenset[int] | None, int]:
        """The rates a config block of this module accepts and its default rate:
        the class attributes, unless the module lets its config say otherwise
        (``parse_sample_rate_overrides``). Reads the config only, so the setup
        checks can tell an unsupported ``engine.module.audio_format`` without
        building the module."""
        return cls.SUPPORTED_SAMPLE_RATES, cls.DEFAULT_SAMPLE_RATE

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


def models_url_for(endpoint: str) -> str:
    """The OpenAI-style model listing beside *endpoint* (a ws(s):// or http(s)://
    URL): the same host, the path up to its ``/v1``, then ``/models``."""
    parts = urllib.parse.urlsplit(endpoint)
    scheme = {"wss": "https", "ws": "http"}.get(parts.scheme, parts.scheme)
    path = parts.path
    prefix = path[: path.index("/v1") + 3] if "/v1" in path else "/v1"
    return f"{scheme}://{parts.netloc}{prefix}/models"


def openai_needs_a_key(label: str) -> str:
    """The message of an OpenAI-protocol config pointed at hosted OpenAI with no
    key variable named: such a module has no default variable."""
    return (
        f"{label}: api.openai.com needs a key: name its variable in 'api_key_env', "
        "usually 'OPENAI_API_KEY'"
    )


def probe_listing(
    cls: type[Declared],
    config: dict,
    secrets: Mapping[str, str] | None,
    endpoint: str,
    model: str,
) -> str:
    """The probe of an OpenAI-protocol module: list the server's models (with the
    key, when the config names one) and find the configured model there."""
    headers: dict[str, str] = {}
    if cls.key_variable(config) is not None:
        headers["Authorization"] = f"Bearer {cls.resolve_api_key(config, secrets)}"
    url = models_url_for(endpoint)
    host = urllib.parse.urlsplit(url).netloc
    body = get_json(url, headers, what=f"The server at {host}")
    if model not in listed_model_ids(body):
        raise RuntimeError(
            f"The server at {host} answered but does not list the model {model!r}."
        )
    return f"The server at {host} answered and lists the model {model!r}."
